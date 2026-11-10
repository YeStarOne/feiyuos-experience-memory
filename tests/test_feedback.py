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


if __name__ == "__main__":
    unittest.main()
