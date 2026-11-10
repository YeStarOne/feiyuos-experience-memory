# -*- coding: utf-8 -*-
"""feedback：ExpeL 台账（counter/conf/test_count）+ A-MEM 三守卫。
夹具纪律（cmb）：每条规则都有已知该拒/该收的真实形态样本，不许只测 happy path。"""
import unittest

from expmem.feedback import apply_feedback, parse_feedback, record_feedback

FRONT = {"name": "stardew-probe-after-action", "state": "candidate",
         "counter": 1, "test_count": 0, "strong_ok": 0, "strong_fail": 0,
         "confidence": 0.55, "contradicts": ["buy-blind"], "tags": ["对账"]}
SHOWN = ("stardew-probe-after-action",)


class TestLedger(unittest.TestCase):
    def test_weak_positive_counter_only(self):
        f, ok, _ = record_feedback(dict(FRONT), {"name": FRONT["name"],
                                                 "kind": "weak_positive"}, SHOWN)
        self.assertTrue(ok)
        self.assertEqual(f["counter"], 2)
        self.assertEqual(f["confidence"], 0.55)     # conf 不吃 weak
        self.assertEqual(f["test_count"], 0)

    def test_weak_negative_counter_only(self):
        f, ok, _ = record_feedback(dict(FRONT), {"name": FRONT["name"],
                                                 "kind": "weak_negative"}, SHOWN)
        self.assertEqual(f["counter"], 0)

    def test_counter_le_zero_means_out_of_book(self):
        # ExpeL REMOVE：连吃三条 weak_negative → counter≤0；移出书由 retrieve 兑现
        f = dict(FRONT)
        for _ in range(3):
            f, ok, _ = record_feedback(f, {"name": FRONT["name"],
                                           "kind": "weak_negative"}, SHOWN)
            self.assertTrue(ok)
        self.assertEqual(f["counter"], -2)

    def test_strong_success_feeds_conf_and_test_count(self):
        f, ok, _ = record_feedback(dict(FRONT), {"name": FRONT["name"], "kind": "strong",
                                                 "verdict": "success",
                                                 "executed": True}, SHOWN)
        self.assertTrue(ok)
        self.assertEqual(f["strong_ok"], 1)
        self.assertEqual(f["confidence"], 1.0)
        self.assertEqual(f["test_count"], 1)        # 执行过=被测过（爬升闸的粮食）
        self.assertEqual(f["counter"], 2)

    def test_strong_failure_no_test_count(self):
        # 失败的执行不是「验证」——不许给爬升闸喂粮食
        f, ok, _ = record_feedback(dict(FRONT), {"name": FRONT["name"], "kind": "strong",
                                                 "verdict": "failure",
                                                 "executed": True}, SHOWN)
        self.assertEqual(f["strong_fail"], 1)
        self.assertEqual(f["test_count"], 0)
        self.assertEqual(f["confidence"], 0.0)
        self.assertEqual(f["counter"], 0)

    def test_conf_only_eats_strong(self):
        # 混合流：3 weak_negative + 2 strong success → conf 仍 1.0（weak 不掺）
        f = dict(FRONT)
        for _ in range(3):
            f, _, _ = record_feedback(f, {"name": FRONT["name"],
                                          "kind": "weak_negative"}, SHOWN)
        for _ in range(2):
            f, _, _ = record_feedback(f, {"name": FRONT["name"], "kind": "strong",
                                          "verdict": "success", "executed": True}, SHOWN)
        self.assertEqual(f["confidence"], 1.0)
        self.assertEqual(f["strong_ok"], 2)


class TestAMemGuards(unittest.TestCase):
    def test_guard1_not_shown_this_round(self):
        f, ok, why = record_feedback(dict(FRONT), {"name": FRONT["name"],
                                                   "kind": "weak_positive"},
                                     shown_ids=("some-other-card",))
        self.assertFalse(ok)
        self.assertEqual(why, "not_shown_this_round")
        self.assertEqual(f["counter"], 1)           # 原样

    def test_guard2_parse_failure_fail_closed(self):
        # 坏 JSON / 未知 kind / strong 缺 verdict：整条拒收，front 原样
        for raw in ('{"name": "x", "kind": ', '{"name": "x", "kind": "wat"}',
                    '{"name": "x", "kind": "strong", "executed": true}'):
            f, ok, why = record_feedback(dict(FRONT), raw, SHOWN)
            self.assertFalse(ok, raw)
            self.assertTrue(why.startswith("parse_rejected"))
            self.assertEqual(f["counter"], 1)

    def test_guard2_strong_without_executed_rejected(self):
        f, ok, why = record_feedback(dict(FRONT), {"name": FRONT["name"], "kind": "strong",
                                                   "verdict": "success"}, SHOWN)
        self.assertFalse(ok)
        self.assertIn("executed", why)

    def test_guard3_empty_list_never_clears(self):
        f, ok, _ = record_feedback(dict(FRONT), {"name": FRONT["name"],
                                                 "kind": "weak_positive",
                                                 "contradicts": [], "tags": []}, SHOWN)
        self.assertTrue(ok)
        self.assertEqual(f["contradicts"], ["buy-blind"])   # 空列表不清旧值
        self.assertEqual(f["tags"], ["对账"])

    def test_guard3_nonempty_list_replaces(self):
        f, ok, _ = record_feedback(dict(FRONT), {"name": FRONT["name"],
                                                 "kind": "weak_positive",
                                                 "tags": ["对账", "防坑"]}, SHOWN)
        self.assertEqual(f["tags"], ["对账", "防坑"])
        self.assertEqual(f["contradicts"], ["buy-blind"])   # 没提的字段不动

    def test_name_mismatch_rejected(self):
        f, ok, why = record_feedback(dict(FRONT), {"name": "some-other-card",
                                                   "kind": "weak_positive"}, SHOWN)
        self.assertFalse(ok)
        self.assertTrue(why.startswith("name_mismatch"))

    def test_no_shown_context_allows_manual_cli(self):
        # shown_ids=() = 无本轮展示上下文（人工 CLI）——守卫①放行，②③照常
        f, ok, _ = record_feedback(dict(FRONT), {"name": FRONT["name"],
                                                 "kind": "weak_positive"}, shown_ids=())
        self.assertTrue(ok)
        self.assertEqual(f["counter"], 2)

    def test_list_of_nonstrings_rejected(self):
        f, ok, why = record_feedback(dict(FRONT), {"name": FRONT["name"],
                                                   "kind": "weak_positive",
                                                   "tags": [1, 2]}, SHOWN)
        self.assertFalse(ok)
        self.assertIn("字符串列表", why)


class TestAutoMark(unittest.TestCase):
    """ACE 自动打标接线（intel 复审 #1）：used 缺省 False（缺省往好猜=每个成功
    回合都记给在册卡）；接线只从注入清单取 used。"""

    def test_default_used_is_false(self):
        f, ok, why = apply_feedback.__globals__[
            "auto_from_episode"](dict(FRONT), "success")
        self.assertFalse(ok)
        self.assertEqual(why, "not_used")
        self.assertEqual(f["counter"], 1)      # 原样

    def test_used_true_marks_once(self):
        from expmem.feedback import auto_from_episode
        f, ok, _ = auto_from_episode(dict(FRONT), "success", used=True)
        self.assertTrue(ok)
        self.assertEqual(f["counter"], 2)
        self.assertEqual(f["confidence"], 0.55)   # conf 只吃 strong，weak 不碰


class TestManifestWiring(unittest.TestCase):
    """注入清单（intel 复审 #3）：本轮递了什么卡必须落盘——守卫①和自动打标
    的 used 都从这里取，清单不存在 = fail-safe 空列表。"""

    def test_retrieve_writes_manifest_and_readback(self):
        import os
        import tempfile
        from expmem import api
        d = tempfile.mkdtemp()
        os.environ["EXPMEM_SKILLS_DIR"] = d
        try:
            import shutil
            src = os.path.join(os.path.dirname(__file__), "..", "skills",
                               "stardew-probe-after-action")
            shutil.copytree(src, os.path.join(d, "stardew-probe-after-action"))
            r = api.retrieve_skills("怎么把东西存进仓库", session="s1", turn=7)
            self.assertEqual(len(r["refs"]), 1)
            self.assertEqual(api.injected_names(d, "s1", 7),
                             ["stardew-probe-after-action"])
            self.assertEqual(api.injected_names(d, "other", 7), [])
        finally:
            del os.environ["EXPMEM_SKILLS_DIR"]

    def test_settle_auto_marks_only_used_success(self):
        import os
        import json as J
        import tempfile
        from expmem import api
        d = tempfile.mkdtemp()
        os.environ["EXPMEM_SKILLS_DIR"] = d
        try:
            import shutil
            src = os.path.join(os.path.dirname(__file__), "..", "skills",
                               "stardew-probe-after-action")
            shutil.copytree(src, os.path.join(d, "stardew-probe-after-action"))
            # 清单：s1/turn1 递过这张卡
            with open(os.path.join(d, ".injected.jsonl"), "w",
                      encoding="utf-8") as f:
                f.write(J.dumps({"ts": 0, "session": "s1", "turn": 1,
                                 "names": ["stardew-probe-after-action"]}) + "\n")
            # 注意 (session,turn) 粒度：cmb-P2 后同键回合同轮只结一次，
            # 所以四个夹具各占一个 turn
            eps = [
                # 命中：清单有 + 本集真的执行了 stardew.state 的动作
                {"session": "s1", "turn": 1, "verdict": "success",
                 "episode_id": "e1", "actions": ["a1"]},
                # 清单没有 → skip（递没递是清单说了算）
                {"session": "s1", "turn": 2, "verdict": "success",
                 "episode_id": "e2", "actions": []},
                # 失败 → skip
                {"session": "s1", "turn": 3, "verdict": "failure",
                 "episode_id": "e3", "actions": ["a1"]},
                # intel-B 回声室夹具：递了但本集零动作（tool_key 对不上）→ 不标
                {"session": "s1", "turn": 4, "verdict": "success",
                 "episode_id": "e4", "actions": []},
            ]
            acts = [{"id": "a1", "verb": "state", "raw_path": "",
                     "segment": "sd state"}]
            out = api.settle_auto_marks(eps, acts, d)
            self.assertEqual(out["marked"], {"stardew-probe-after-action": 1})
            self.assertEqual(out["skipped"], 3)   # e2 清单缺 / e3 失败 / e4 没用
            self.assertEqual(out["already_settled"], 0)
            front, _ = __import__("expmem.skills", fromlist=["load_skill"]) \
                .load_skill(os.path.join(d, "stardew-probe-after-action",
                                         "SKILL.md"))
            self.assertEqual(front["counter"], 2)
            # intel-A 幂等：全史重放三遍也只结一次
            for _ in range(3):
                out2 = api.settle_auto_marks(eps, acts, d)
            self.assertEqual(out2["marked"], {})
            self.assertGreaterEqual(out2["already_settled"], 3)
            front2, _ = __import__("expmem.skills", fromlist=["load_skill"]) \
                .load_skill(os.path.join(d, "stardew-probe-after-action",
                                         "SKILL.md"))
            self.assertEqual(front2["counter"], 2)     # 不被重放灌水
        finally:
            del os.environ["EXPMEM_SKILLS_DIR"]

    def test_settle_missing_card_keeps_turn_unsettled(self):
        # intel 复审 A：卡缺失 = 该回合不结（下次重试），不许永久作废信用
        import os
        import json as J
        import tempfile
        from expmem import api
        d = tempfile.mkdtemp()
        os.environ["EXPMEM_SKILLS_DIR"] = d
        try:
            with open(os.path.join(d, ".injected.jsonl"), "w",
                      encoding="utf-8") as f:
                f.write(J.dumps({"ts": 0, "session": "s1", "turn": 1,
                                 "names": ["ghost-card"]}) + "\n")
            eps = [{"session": "s1", "turn": 1, "verdict": "success",
                    "episode_id": "e1", "actions": []}]
            out = api.settle_auto_marks(eps, [], d)
            self.assertEqual(out["marked"], {})
            self.assertFalse(os.path.exists(os.path.join(d, ".settled.jsonl")),
                             "settled 未写=可重试")
        finally:
            del os.environ["EXPMEM_SKILLS_DIR"]


if __name__ == "__main__":
    unittest.main()
