# -*- coding: utf-8 -*-
"""11-12 下一批验收：
1) AppendOnly 跨进程幂等（两实例/外部直写/崩溃残行）
2) replay meta.json 版本闸（拒绝混合重放）+ source 落盘
3) 执行/基准证据分离（bench 行不进两轨）
4) pending_review 不进 refs、只进冲突对；冲突对不要求双边进 refs
5) settled 台账压实（同键留 max(v)，.bak 留档）
跑法：python -m pytest tests/test_next_batch.py -q（或 unittest）。
"""
import json
import os
import shutil
import tempfile
import unittest

from expmem import api, consolidate, retrieve
from expmem.store import AppendOnly


# ---------- 1) AppendOnly 跨进程 ----------

class TestAppendOnlyCrossProcess(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = os.path.join(self.d, "actions.jsonl")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _lines(self):
        out = []
        with open(self.p, encoding="utf-8") as f:
            for x in f:
                x = x.strip()
                if not x:
                    continue
                try:
                    out.append(json.loads(x))
                except Exception:
                    out.append(None)      # 残行/坏行：留档但不许破坏新行
        return out

    def test_two_instances_same_id_dedupes(self):
        a = AppendOnly(self.p)
        b = AppendOnly(self.p)          # 模拟另一个进程：各自 _seen
        self.assertTrue(a.append({"id": "x", "v": 1}))
        self.assertFalse(b.append({"id": "x", "v": 2}))   # B 吸收后认账
        self.assertTrue(b.append({"id": "y"}))
        a.close(); b.close()
        rows = self._lines()
        self.assertEqual([r["id"] for r in rows], ["x", "y"])
        self.assertEqual(rows[0]["v"], 1)   # 先到先得，后来者不覆盖

    def test_absorbs_external_writes(self):
        a = AppendOnly(self.p)
        a.append({"id": "a"})
        # 别家不经 AppendOnly 直写（如旧版本程序/手工补行）
        with open(self.p, "a", encoding="utf-8") as f:
            f.write(json.dumps({"id": "b"}) + "\n")
        a.append({"id": "c"})
        self.assertEqual([r["id"] for r in self._lines()], ["a", "b", "c"])
        self.assertEqual(len(a), 3)
        self.assertFalse(a.append({"id": "b"}))   # 吸收过的 id 不重复写
        a.close()

    def test_partial_line_repaired_not_absorbed(self):
        a = AppendOnly(self.p)
        a.append({"id": "a"})
        with open(self.p, "ab") as f:
            f.write(b'{"id":"crash-debris')      # 写方崩在半行（无换行）
        self.assertTrue(a.append({"id": "c"}))   # 补换行终止残行再追加
        rows = self._lines()
        self.assertEqual([(r or {}).get("id") for r in rows], ["a", None, "c"])
        self.assertTrue(a.append({"id": "c"}) is False)  # 幂等仍成立
        a.close()
        # 残行留档不删（历史一字不改）
        with open(self.p, "rb") as f:
            self.assertIn(b"crash-debris", f.read())


# ---------- 2) meta 版本闸 + replay 落 source ----------

def _mk_session_root(d):
    """最小 owner 会话：一回合一次 sd state 读。返回 root。"""
    root = os.path.join(d, "ws", "onebot-private-1000000001-test")
    os.makedirs(root, exist_ok=True)
    ev = [
        {"type": "turn/start", "seq": 1, "time": 1000, "data": {"turn": 1}},
        {"type": "tool/call", "seq": 2, "time": 1005, "data": {
            "turn": 1, "step": 1, "callId": "c1", "name": "bash",
            "arguments": json.dumps({"command": "sd raw /state"})}},
        {"type": "tool/result", "seq": 3, "time": 1010, "data": {
            "message": {"toolCallId": "c1", "isError": False,
                        "content": [{"type": "text", "text":
                                     json.dumps({"state": {"season": "winter",
                                                           "dayOfMonth": 25,
                                                           "time": 1740}})}]}}},
        {"type": "turn/end", "seq": 4, "time": 1100,
         "data": {"turn": 1, "reason": {"kind": "completed"}}},
    ]
    with open(os.path.join(root, "session.v4.jsonl"), "w", encoding="utf-8") as f:
        for e in ev:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return os.path.join(d, "ws")


class TestReplayMetaGate(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.root = _mk_session_root(self.d)
        self.out = os.path.join(self.d, "out")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_replay_writes_meta_and_source(self):
        m = api.replay(self.root, self.out, source="execution")
        self.assertEqual(m["actions_written"], 1)
        meta = json.load(open(os.path.join(self.out, "meta.json"), encoding="utf-8"))
        self.assertEqual(meta["parser_v"], 1)
        self.assertEqual(meta["outcome_v"], 2)
        self.assertEqual(meta["source"], "execution")
        with open(os.path.join(self.out, "episodes.jsonl"), encoding="utf-8") as f:
            rows = [json.loads(x) for x in f if x.strip()]
        self.assertTrue(rows)
        self.assertTrue(all(r.get("source") == "execution" for r in rows))

    def test_replay_idempotent_rerun(self):
        api.replay(self.root, self.out)
        m2 = api.replay(self.root, self.out)
        self.assertEqual(m2["actions_written"], 1)   # 同目录重跑不重复写
        with open(os.path.join(self.out, "actions.jsonl"), encoding="utf-8") as f:
            self.assertEqual(sum(1 for x in f if x.strip()), 1)

    def test_version_mismatch_rejected(self):
        api.replay(self.root, self.out)
        meta_path = os.path.join(self.out, "meta.json")
        meta = json.load(open(meta_path, encoding="utf-8"))
        meta["parser_v"] = 99
        json.dump(meta, open(meta_path, "w", encoding="utf-8"))
        with self.assertRaises(ValueError) as cm:
            api.replay(self.root, self.out)
        self.assertIn("版本不一致", str(cm.exception))
        meta["parser_v"] = 1
        meta["outcome_v"] = 1                        # 判据版本也要对齐
        json.dump(meta, open(meta_path, "w", encoding="utf-8"))
        with self.assertRaises(ValueError):
            api.replay(self.root, self.out)

    def test_legacy_dir_without_meta_passes(self):
        os.makedirs(self.out, exist_ok=True)
        with open(os.path.join(self.out, "actions.jsonl"), "w") as f:
            f.write("{}\n")                          # 旧产物没有 meta
        api.replay(self.root, self.out)              # 不抛；且补写了 meta
        self.assertTrue(os.path.exists(os.path.join(self.out, "meta.json")))


# ---------- 3) 执行/基准分离 ----------

def _ep_row(verdict, eid="e1", **kw):
    row = {"episode_id": eid, "session": "s1", "turn": 1, "t_from": 0, "t_to": 1,
           "game_day": "", "goal_hint": "", "actions": [], "verdict": verdict,
           "probe_status": "ok", "failure_class": "", "act_count": 0, "game": True,
           "outcome": {}, "skill_refs": []}
    row.update(kw)
    return row


def _act_row(aid, out, verb="raw", call_id="c1", session="s1"):
    return {"id": aid, "ts": 0, "session": session, "turn": 1, "step": 1,
            "tool": "bash", "adapter": "stardew_sd", "call_id": call_id,
            "sub_idx": 0, "verb": verb, "raw_path": "/store", "segment": "sd raw /store",
            "endpoint": "RAW /store", "is_error": False, "latency_ms": 1, "cwd": "",
            "out_head": out, "out_sha": "", "poll_repeat": False, "out_source": "",
            "job_id": "", "job_status": "", "job_exit": None, "job_done_ts": None}


class TestSourceGate(unittest.TestCase):
    def test_bench_episode_not_in_failure_track(self):
        eps = [_ep_row("failure", eid="b1", source="bench",
                       failure_class="noop",
                       outcome={"action": "a1", "attribution": "segment"})]
        acts = [_act_row("a1", 'stored:[]')]
        specs, dropped = consolidate.scan_candidates(eps, acts)
        self.assertEqual(specs, [])
        self.assertTrue(any(x.get("reason") == "non_execution_source"
                            for x in dropped))

    def test_bench_episode_not_in_success_track(self):
        eps = [_ep_row("success", eid="b1", source="fixture", goal_hint="存仓库")]
        acts = [_act_row("a1", '{"stored":["x"],"totalCost":0}')]
        stats = {}
        rows = consolidate.scan_success(eps, acts, stats=stats)
        self.assertEqual(rows, [])
        self.assertEqual(stats.get("excluded_non_execution"), 1)

    def test_missing_source_defaults_execution(self):
        eps = [_ep_row("failure", eid="e1", failure_class="noop",
                       outcome={"action": "a1", "attribution": "segment"})]
        acts = [_act_row("a1", 'stored:[]')]
        specs, dropped = consolidate.scan_candidates(eps, acts)
        self.assertEqual(len(specs), 1)              # 旧行无 source = 执行经验
        self.assertEqual(dropped, [])


# ---------- 4) pending_review 检索策略 ----------

class TestPendingPolicy(unittest.TestCase):
    def _cat(self, fronts):
        import expmem.retrieve as R
        return [dict(f) for f in fronts]

    def test_pending_not_in_refs_but_in_pairs(self):
        cat = self._cat([
            {"name": "buy-with-price", "description": "买东西必带价格", "state": "observed",
             "tool_key": "stardew.buy", "version": 2, "contradicts": ["buy-blind"],
             "retrieval_anchor": "buy price charge", "task_query": "买东西必带价格"},
            {"name": "buy-blind", "description": "买东西不带价格也能成", "state": "pending_review",
             "tool_key": "stardew.buy", "version": 1, "contradicts": ["buy-with-price"],
             "retrieval_anchor": "buy price charge", "task_query": "买东西不带价格"},
        ])
        r = retrieve.retrieve_skills("buy price charge", cat)
        names = [x["name"] for x in r["refs"]]
        self.assertIn("buy-with-price", names)
        self.assertNotIn("buy-blind", names)         # pending 不进目录行
        self.assertEqual(r["pending_hidden"], 1)
        self.assertEqual(r["conflict_pairs"], [["buy-blind", "buy-with-price"]])
        text, _ = retrieve.catalog_lines(r)
        self.assertIn("未确认哪版为准", text)

    def test_pair_survives_cap_single_side(self):
        cat = self._cat([
            {"name": "buy-with-price", "description": "买东西必带价格", "state": "verified",
             "tool_key": "stardew.buy", "version": 3, "contradicts": ["buy-blind"],
             "retrieval_anchor": "buy price charge", "task_query": "买东西必带价格"},
            {"name": "buy-blind", "description": "买东西不带价格也能成", "state": "pending_review",
             "tool_key": "stardew.buy", "version": 1, "contradicts": [],
             "retrieval_anchor": "buy price charge", "task_query": "买东西不带价格"},
        ])
        r = retrieve.retrieve_skills("buy price charge", cat, top_k=1)
        self.assertEqual([x["name"] for x in r["refs"]], ["buy-with-price"])
        # buy-blind 没过闸也没进 refs，但注入侧的 contradicts 指着它——照标
        self.assertEqual(r["conflict_pairs"], [["buy-blind", "buy-with-price"]])

    def test_deprecated_partner_not_a_pair(self):
        cat = self._cat([
            {"name": "buy-with-price", "description": "买东西必带价格", "state": "verified",
             "tool_key": "stardew.buy", "version": 3, "contradicts": ["buy-blind"],
             "retrieval_anchor": "buy price charge", "task_query": "买东西必带价格"},
            {"name": "buy-blind", "description": "旧版", "state": "deprecated",
             "tool_key": "stardew.buy", "version": 1, "contradicts": [],
             "retrieval_anchor": "buy price charge", "task_query": "旧"},
        ])
        r = retrieve.retrieve_skills("buy price charge", cat)
        self.assertEqual(r["conflict_pairs"], [])    # 降废的旧版不算悬案

    def test_generic_token_stripped_on_real_size_catalog(self):
        # 11-12 夜真库反事实实锤：8 张卡的默认 task_query 全含「这张卡」，
        # 聊天回合「把这张图发给小主人」曾命中全部卡。真库量级(≥7)下
        # 通用 token 必须剥掉；剥完空的不进 refs 也不进 dropped_weak。
        cat = self._cat([
            {"name": "card-%d" % i, "description": "星露谷 工具%d" % i,
             "state": "candidate", "tool_key": "stardew.t%d" % i,
             "retrieval_anchor": "tool%d zig%d" % (i, i),
             "task_query": "对 stardew.t%d 的结果负责时先查这张卡" % i}
            for i in range(8)
        ])
        r = retrieve.retrieve_skills("把这张图发给小主人", cat)
        self.assertEqual([x["name"] for x in r["refs"]], [])
        self.assertEqual(r["generic_only"], 8)       # 全部是模板词命中
        self.assertEqual(r["dropped_weak"], 0)
        r2 = retrieve.retrieve_skills("tool3 zig3", cat)   # 唯一实词照常命中
        self.assertEqual([x["name"] for x in r2["refs"]], ["card-3"])
        self.assertEqual(r2["generic_only"], 0)

    def test_small_catalog_untouched(self):
        # 小语料（<7 张）cap>N 等于不剥——夹具/小目录向后兼容
        cat = self._cat([
            {"name": "a", "description": "甲", "state": "candidate",
             "tool_key": "stardew.a", "retrieval_anchor": "alpha 这张",
             "task_query": "先查这张卡"},
            {"name": "b", "description": "乙", "state": "candidate",
             "tool_key": "stardew.b", "retrieval_anchor": "beta 这张",
             "task_query": "先查这张卡"},
        ])
        r = retrieve.retrieve_skills("这张", cat)
        self.assertEqual(len(r["refs"]), 2)          # 两张都能过（旧行为保留）


# ---------- 5) settled 台账压实 ----------

class TestSettleCompaction(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = os.path.join(self.d, ".settled.jsonl")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _write(self, rows):
        with open(self.p, "w", encoding="utf-8", newline="\n") as f:
            for s, t, v in rows:
                f.write(json.dumps({"session": s, "turn": t, "v": v,
                                    "reason": "complete"}) + "\n")

    def _read(self):
        with open(self.p, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]

    def test_keeps_max_v_per_key(self):
        self._write([("s1", 1, 1), ("s1", 1, 2), ("s2", 2, 2)])
        n = api._compact_settled(self.p)
        self.assertEqual(n, 2)
        rows = self._read()
        self.assertEqual([(r["session"], r["turn"], r["v"]) for r in rows],
                         [("s1", 1, 2), ("s2", 2, 2)])
        self.assertTrue(os.path.exists(self.p + ".bak"))    # 旧账留档
        self.assertEqual(len(open(self.p + ".bak", encoding="utf-8").readlines()), 3)

    def test_no_duplicate_keys_untouched(self):
        self._write([("s1", 1, 2), ("s2", 2, 2)])
        n = api._compact_settled(self.p)
        self.assertEqual(n, 2)
        self.assertFalse(os.path.exists(self.p + ".bak"))   # 无压空间不动文件

    def test_missing_file_zero(self):
        self.assertEqual(api._compact_settled(self.p), 0)

    def test_settle_result_carries_compacted(self):
        # 接口层冒烟：空集 settle 也带 compacted 键（消费者不用 getattr 探测）
        sdir = tempfile.mkdtemp()
        try:
            out = api.settle_auto_marks([], [], skills_dir=sdir)
            self.assertEqual(out["compacted"], 0)
        finally:
            shutil.rmtree(sdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
