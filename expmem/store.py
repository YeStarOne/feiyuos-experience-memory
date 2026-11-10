# -*- coding: utf-8 -*-
"""AppendOnlyStore：jsonl 追加、幂等、崩溃安全。

- 只追加，不改历史（改判走 feedback 新行，方案 §3 evidence.py）。
- 幂等键默认 'id'：同一输入重放两次，不产生重复行。
- 索引/缓存才允许 os.replace（本文件只管追加，不建索引）。"""
import json
import os


class AppendOnly:
    def __init__(self, path: str, key: str = "id"):
        self.path = path
        self.key = key
        self._seen = set()
        self._n = 0
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        self._seen.add(json.loads(line)[key])
                        self._n += 1
                    except Exception:
                        pass  # 坏行留档不读，绝不静默删历史
        self._fh = open(path, "a", encoding="utf-8", newline="\n")

    def append(self, row: dict) -> bool:
        rid = row.get(self.key)
        if rid in self._seen:
            return False
        self._fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True,
                                  separators=(",", ":")) + "\n")
        self._seen.add(rid)
        self._n += 1
        return True

    def close(self):
        self._fh.close()

    def __len__(self):
        return self._n
