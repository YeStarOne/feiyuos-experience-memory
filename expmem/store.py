# -*- coding: utf-8 -*-
"""AppendOnlyStore：jsonl 追加、幂等、崩溃安全（11-12 起跨进程也幂等）。

- 只追加，不改历史（改判走 feedback 新行，方案 §3 evidence.py）。
- 幂等键默认 'id'：同一输入重放两次，不产生重复行。
- 索引/缓存才允许 os.replace（本文件只管追加，不建索引）。
- 跨进程幂等（11-12 下一批）：原实现两个进程各持一份 _seen，A 写过的 id B
  不知道 → B 会重复写同一行。修法 = 每次 append 在 .lock 独占锁内先「吸收」
  自本进程上次已知位置之后的新行（别家写进来的认账），再判幂等再写。
  崩溃残留的半行（无结尾换行）：吸收时只吃完整行，追加前先补一个换行把
  残行终止掉——jsonl 每行自洽，历史一字不改。"""
import json
import os

from .locks import exclusive_lock


class AppendOnly:
    def __init__(self, path: str, key: str = "id"):
        self.path = path
        self.key = key
        self._lock_path = path + ".lock"
        # 初始扫描也在锁内：别家正在写时我不读半行
        with exclusive_lock(self._lock_path):
            self._seen, self._n, self._size = self._load()
        self._fh = open(path, "a", encoding="utf-8", newline="\n")

    # ---------- 内部 ----------

    def _load(self):
        seen, n, size = set(), 0, 0
        if os.path.exists(self.path):
            with open(self.path, "rb") as f:
                blob = f.read()
            size = len(blob)
            for line in blob.split(b"\n")[:-1]:   # 只吃完整行；残行留给 _repair
                line = line.strip()
                if not line:
                    continue
                try:
                    seen.add(json.loads(line.decode("utf-8"))[self.key])
                    n += 1
                except Exception:
                    pass  # 坏行留档不读，绝不静默删历史
        return seen, n, size

    def _absorb(self):
        """锁内调用：把别家追加的完整行并进 _seen/_n，推进 _size。
        末尾若有无换行的残行（写方崩溃），返回 True（追加前要先补换行）。"""
        if not os.path.exists(self.path):
            return False
        cur = os.path.getsize(self.path)
        if cur <= self._size:
            return False
        with open(self.path, "rb") as f:
            f.seek(self._size)
            blob = f.read()
        partial = not blob.endswith(b"\n")
        if partial:
            blob = blob[:blob.rfind(b"\n") + 1]   # 残行不吸收（永远不完整）
        self._size += len(blob)
        for line in blob.split(b"\n"):
            line = line.strip()
            if not line:
                continue
            try:
                rid = json.loads(line.decode("utf-8"))[self.key]
            except Exception:
                continue
            if rid not in self._seen:
                self._seen.add(rid)
                self._n += 1
        return partial

    # ---------- 接口 ----------

    def append(self, row: dict) -> bool:
        rid = row.get(self.key)
        with exclusive_lock(self._lock_path):
            partial = self._absorb()
            if rid in self._seen:
                return False
            line = json.dumps(row, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":")) + "\n"
            if partial:
                # 上一位写方崩在半行：补一个换行终止残行（残行留档，历史不改）
                self._fh.write("\n")
                self._fh.flush()
                os.fsync(self._fh.fileno())
                self._size += 1
            self._fh.write(line)
            self._fh.flush()
            # 崩溃安全兑现（评审 11-12 P2）：显式刷盘，进程退出/断电不丢已 append 行
            os.fsync(self._fh.fileno())
            self._seen.add(rid)
            self._n += 1
            self._size += len(line.encode("utf-8"))
            return True

    def close(self):
        self._fh.close()

    def __len__(self):
        return self._n
