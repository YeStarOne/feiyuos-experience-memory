# -*- coding: utf-8 -*-
"""retrieve：三闸（硬阈值/不静默截断/去重）、deprecated 不进、domain 过滤、conflictCard、降级阶梯。"""
import unittest

from expmem.retrieve import LIMITS, catalog_lines, estimate_tokens, load_catalog, retrieve_skills

SEED = "skills"


class TestCatalog(unittest.TestCase):
    def test_seed_card_loaded_not_deprecated_excluded(self):
        cat = load_catalog(SEED)
        self.assertEqual(len(cat), 1)
        self.assertEqual(cat[0]["name"], "stardew-probe-after-action")

    def test_deprecated_never_enters_catalog(self):
        import tempfile, os, json as J
        d = tempfile.mkdtemp()
        front = {"name": "old-card", "description": "x", "state": "deprecated",
                 "retrieval_anchor": "对账 state", "task_query": "对账"}
        os.makedirs(os.path.join(d, "old-card"))
        with open(os.path.join(d, "old-card", "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\n%s\n---\n正文" % J.dumps(front, ensure_ascii=False))
        self.assertEqual(load_catalog(d), [])


class TestRetrieve(unittest.TestCase):
    def setUp(self):
        self.cat = load_catalog(SEED)

    def test_miss_is_empty_not_forced(self):
        r = retrieve_skills("买东西 价格 扣钱", self.cat)
        self.assertEqual(len(r["refs"]), 0)  # 种子卡不含买东西词，宁缺毋滥

    def test_hit_above_threshold(self):
        r = retrieve_skills("存仓库 对账 stored 收庄稼 state", self.cat)
        self.assertEqual(len(r["refs"]), 1)
        self.assertGreaterEqual(r["refs"][0]["score"], LIMITS["score_threshold"])

    def test_weak_below_threshold_dropped(self):
        r = retrieve_skills("对账", self.cat)  # 单词弱命中
        self.assertEqual(r["refs"], [])
        self.assertGreaterEqual(r["dropped_weak"], 1)

    def test_cap_and_incomplete(self):
        import tempfile, os, json as J
        d = tempfile.mkdtemp()
        for i in range(5):
            front = {"name": "card-%d" % i, "description": "x", "state": "observed",
                     "tool_key": "stardew.state", "version": 1,
                     "retrieval_anchor": "对账 state 存仓库 收庄稼",
                     "task_query": "写操作后同回合核对结果"}
            os.makedirs(os.path.join(d, "card-%d" % i))
            with open(os.path.join(d, "card-%d" % i, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write("---\n%s\n---\n正文" % J.dumps(front, ensure_ascii=False))
        r = retrieve_skills("对账 state 存仓库", load_catalog(d))
        self.assertEqual(len(r["refs"]), LIMITS["max_inject"])
        self.assertTrue(r["incomplete"])

    def test_domain_filter(self):
        import tempfile, os, json as J
        d = tempfile.mkdtemp()
        for nm, dom in (("mc-card", "minecraft"), ("sd-card", "stardew")):
            front = {"name": nm, "description": "x", "state": "observed", "domain": dom,
                     "tool_key": "x.y", "version": 1,
                     "retrieval_anchor": "对账 state", "task_query": "核对"}
            os.makedirs(os.path.join(d, nm))
            with open(os.path.join(d, nm, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write("---\n%s\n---\n正文" % J.dumps(front, ensure_ascii=False))
        cat = load_catalog(d)
        self.assertEqual([r["name"] for r in retrieve_skills("对账 state", cat, domain="stardew")["refs"]],
                         ["sd-card"])
        self.assertEqual(len(retrieve_skills("对账 state", cat)["refs"]), 2)  # 不点名=都放行

    def test_conflict_pair_rendered(self):
        import tempfile, os, json as J
        d = tempfile.mkdtemp()
        fronts = [
            {"name": "buy-with-price", "description": "x", "state": "observed",
             "tool_key": "stardew.buy", "version": 2, "contradicts": ["buy-blind"],
             "retrieval_anchor": "买 价格 扣钱", "task_query": "买东西必带价格"},
            {"name": "buy-blind", "description": "x", "state": "pending_review",
             "tool_key": "stardew.buy", "version": 1, "contradicts": ["buy-with-price"],
             "retrieval_anchor": "买 价格 扣钱", "task_query": "买东西不带价格也能成"},
        ]
        for front in fronts:
            os.makedirs(os.path.join(d, front["name"]))
            with open(os.path.join(d, front["name"], "SKILL.md"), "w", encoding="utf-8") as f:
                f.write("---\n%s\n---\n正文" % J.dumps(front, ensure_ascii=False))
        r = retrieve_skills("买 价格 扣钱", load_catalog(d))
        self.assertEqual(len(r["conflict_pairs"]), 1)
        text = catalog_lines(r)
        self.assertIn("未确认哪版为准", text)


class TestBudget(unittest.TestCase):
    def test_degradation_never_empty(self):
        refs = [{"name": "a-very-long-name", "when": "很长的" * 60, "state": "observed",
                 "version": 3, "confidence": 0.5, "tool_key": "", "contradicts": [],
                 "score": 5}]
        for budget in (600, 60, 10):
            tiny = {"refs": refs, "incomplete": False, "dropped_weak": 0,
                    "conflict_pairs": []}
            text = catalog_lines(tiny, token_budget=budget)
            self.assertTrue(text.strip())
            self.assertLessEqual(estimate_tokens(text), max(budget, 20))

    def test_estimate_tokens_cjk_heavier(self):
        self.assertGreater(estimate_tokens("一二三四五"), estimate_tokens("abcde"))


if __name__ == "__main__":
    unittest.main()
