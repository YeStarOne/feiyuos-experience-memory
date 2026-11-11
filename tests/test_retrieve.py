# -*- coding: utf-8 -*-
"""retrieve：三闸（硬阈值/不静默截断/去重）、deprecated 不进、domain 过滤、conflictCard、降级阶梯。"""
import unittest

from expmem.retrieve import (LIMITS, catalog_lines, empty_hint, estimate_tokens,
                             load_catalog, retrieve_skills)

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
        r = retrieve_skills("钓鱼 矿洞 战斗", self.cat)  # 种子卡完全不含这些词
        self.assertEqual(len(r["refs"]), 0)

    def test_cjk_recall_multi_token(self):
        # cmb-P0：中文 query 能召回——「怎么买东西」与卡共享 买东西/买（anchor 已按
        # Voyager 纪律补全真实查询词）；intel-③ 单字唯一命中不递
        r = retrieve_skills("怎么买东西", self.cat)
        self.assertEqual(len(r["refs"]), 1)

    def test_single_char_only_hit_not_delivered(self):
        # intel 实测：功能字/单字唯一命中不许过闸（「一」在任何中文卡里都有；
        # 「收菜」在 anchor 补全后是真匹配，不再是噪声——见 test_cjk_recall）
        for q in ("一", "是不是"):
            r = retrieve_skills(q, self.cat)
            self.assertEqual(r["refs"], [], q)

    def test_compound_token_split(self):
        # cmb-P0②：复合 token 拆层——卡名 stardew-probe-after-action 按 '-' 拆开，
        # 查询里 probe / after 各自都能命中（11-12 起 min_multi=2，故用两个拆出来的
        # 词而不是单词「probe」——单关键词召回经真库实测不是真需求，数字见
        # expmem/retrieve.py 里 LIMITS 的注释）
        r = retrieve_skills("probe after", self.cat)
        self.assertEqual(len(r["refs"]), 1)

    def test_hit_above_threshold(self):
        r = retrieve_skills("存仓库 对账 stored 收庄稼 state", self.cat)
        self.assertEqual(len(r["refs"]), 1)
        # 过闸只吃命中证据（cmb 验收：闸与排序分家），排序分不再当闸用
        self.assertGreaterEqual(r["refs"][0]["hits"], 1)

    def test_below_threshold_counted(self):
        # 「咖啡因摄入」零共享字（用脚本核过：连单字召回都打不中）→ 空手不硬递
        r = retrieve_skills("咖啡因摄入", self.cat)
        self.assertEqual(r["refs"], [])
        self.assertEqual(r["dropped_weak"], 0)

    def test_cap_and_incomplete(self):
        import tempfile, os, json as J
        d = tempfile.mkdtemp()
        for i in range(5):
            # 11-12 通用 token 剥离 + min_multi=2 后，cap 夹具要给每卡**两个**
            # 唯一实词：共享词「对账 state」会被当无鉴别力剥掉，只留一个 uniqN
            # 则每条证据不足被丢——那测的就不是 cap 而是闸了
            front = {"name": "card-%d" % i, "description": "x", "state": "observed",
                     "tool_key": "stardew.state", "version": 1,
                     "retrieval_anchor": "对账 state uniq%d zeta%d" % (i, i),
                     "task_query": "写操作后同回合核对结果"}
            os.makedirs(os.path.join(d, "card-%d" % i))
            with open(os.path.join(d, "card-%d" % i, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write("---\n%s\n---\n正文" % J.dumps(front, ensure_ascii=False))
        q = "对账 state " + " ".join("uniq%d zeta%d" % (i, i) for i in range(5))
        r = retrieve_skills(q, load_catalog(d))
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
        text, level = catalog_lines(r)
        self.assertIn("未确认哪版为准", text)
        self.assertEqual(level, 1)


def _mk_catalog(fronts):
    import tempfile, os, json as J
    d = tempfile.mkdtemp()
    for front in fronts:
        os.makedirs(os.path.join(d, front["name"]))
        with open(os.path.join(d, front["name"], "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\n%s\n---\n正文" % J.dumps(front, ensure_ascii=False))
    return load_catalog(d)


class TestGateRankingSplit(unittest.TestCase):
    """cmb 验收夹具（11-11）：闸与排序必须分家——过闸只吃命中证据
    （hit 数/多字 token），confidence 与 state bonus 只许进排序。
    否则零相关卡只要 conf 高就能混进目录行。"""

    def setUp(self):
        self.cat = load_catalog(SEED)

    def test_fixture1_highfreq_single_char_query_rejected(self):
        for q in ("的", "了", "我", "是不是"):
            self.assertEqual(retrieve_skills(q, self.cat)["refs"], [], q)

    def test_fixture2_real_chinese_query_still_recalls_candidate(self):
        r = retrieve_skills("怎么把东西存进仓库", self.cat)
        self.assertEqual([x["name"] for x in r["refs"]], ["stardew-probe-after-action"])
        self.assertEqual(r["refs"][0]["state"], "candidate")

    def test_fixture3_confidence_ranks_but_never_gates(self):
        terms = "对账 state 存仓库 收庄稼"
        fronts = [
            {"name": "conf-high", "description": "x", "state": "observed", "version": 1,
             "confidence": 1.0, "tool_key": "a.b",
             "retrieval_anchor": terms, "task_query": "核对"},
            {"name": "conf-zero", "description": "x", "state": "observed", "version": 1,
             "confidence": 0.0, "tool_key": "a.b",
             "retrieval_anchor": terms, "task_query": "核对"},
        ]
        r = retrieve_skills(terms, _mk_catalog(fronts))
        # 同等命中：conf=0 的照样过闸，只是排在 conf=1 之后
        self.assertEqual([x["name"] for x in r["refs"]], ["conf-high", "conf-zero"])

    def test_zero_overlap_card_with_max_confidence_never_injected(self):
        fronts = [{"name": "noise-conf-1", "description": "x", "state": "verified",
                   "version": 1, "confidence": 1.0, "tool_key": "a.b",
                   "retrieval_anchor": "钓鱼 矿洞 战斗 下矿", "task_query": "打怪"}]
        self.assertEqual(retrieve_skills("对账 state 存仓库", _mk_catalog(fronts))["refs"], [])

    def test_dropped_weak_regained_discriminative_power(self):
        # 单字命中 + conf 0.9 也不许被顶过闸；被丢的计数看得见（此前恒 0 = 无鉴别力）
        fronts = [{"name": "weak", "description": "x", "state": "candidate", "version": 1,
                   "confidence": 0.9, "tool_key": "a.b",
                   "retrieval_anchor": "锄地 浇水 种子", "task_query": "种地"}]
        r = retrieve_skills("锄", _mk_catalog(fronts))
        self.assertEqual(r["refs"], [])
        self.assertEqual(r["dropped_weak"], 1)


class TestGateRegression(unittest.TestCase):
    """cmb 复核 59b0110 时实测出来的三个真漏口（真实形态 12 卡 × 12 无关 query，
    误召回从 3/12 降到 0/12）。单卡语料挡不住漏口——它们本来就不是靠「卡里没有」
    挡住的，所以这里必须造出卡里**真有的**那些字。"""

    CARDS = [
        {"name": "sd-probe", "state": "candidate", "tool_key": "stardew.state",
         "retrieval_anchor": "对账 state 同一回合 下一步 收菜 收成 存仓库",
         "task_query": "写操作后同一回合核对结果"},
        {"name": "sd-till", "state": "observed", "tool_key": "stardew.till",
         "retrieval_anchor": "锄地 浇水 种子 精力 体力",
         "task_query": "下地前先看体力够不够"},
        {"name": "mc-skin", "state": "observed", "tool_key": "mc.skin",
         "retrieval_anchor": "皮肤 换衣服 玩家外观 材质包",
         "task_query": "换皮肤前先备份材质"},
        {"name": "mc-craft", "state": "observed", "tool_key": "mc.craft",
         "retrieval_anchor": "合成 木板 工作台 材料 配方",
         "task_query": "合成前先数材料"},
        {"name": "sd-plant", "state": "observed", "tool_key": "stardew.plant",
         "retrieval_anchor": "种一个 种子 间距 翻地",
         "task_query": "播种前先算间距"},
    ]

    def setUp(self):
        self.cat = _mk_catalog(self.CARDS)

    def test_pure_function_bigram_is_not_evidence(self):
        # 「是不是」与卡里可能写的「是不是」同形——纯功能字 bigram 不进召回集合
        self.assertEqual(retrieve_skills("是不是", self.cat)["refs"], [])

    def test_single_char_pile_does_not_pass(self):
        # 「一下」→ matched 里只有单字（一/下），旧规则 hit=2 就过闸了
        r = retrieve_skills("一下", self.cat)
        self.assertEqual(r["refs"], [])
        self.assertGreater(r["dropped_weak"], 0)   # 有命中但被丢，计数看得见

    def test_three_real_false_positives_now_blocked(self):
        for q in ("外面是不是要下雨", "能不能休息一会儿", "她怎么还没回话"):
            self.assertEqual(retrieve_skills(q, self.cat)["refs"], [], q)

    def test_related_queries_still_recall(self):
        for q, want in (("收菜对账", "sd-probe"),
                        ("锄地之前看看体力", "sd-till"),
                        ("合成需要几块木板", "mc-craft")):
            names = [r["name"] for r in retrieve_skills(q, self.cat)["refs"]]
            self.assertIn(want, names, "%s → %s" % (q, names))

    def test_known_residual_documented(self):
        # cmb 记的残余：「一个」里的「个」不是功能字，旧规则（min_multi=1）会过闸。
        # 11-12 夜把 min_multi 提到 2 后这条残余**已关闭**——「一个」只产出
        # 「一个」这 1 个多字命中，不够两条证据。仍然有命中，所以 dropped_weak
        # 要看得见（不是靠悄悄消失）。
        r = retrieve_skills("一个", self.cat)
        self.assertEqual(r["refs"], [])
        self.assertGreater(r["dropped_weak"], 0)

    def test_empty_refs_still_tells_her_the_book_exists(self):
        r = retrieve_skills("咖啡因摄入", self.cat)
        self.assertEqual(r["refs"], [])
        self.assertEqual(r["total_active"], len(self.CARDS))
        self.assertIn("另有 %d 条" % len(self.CARDS), empty_hint(r))
        self.assertEqual(empty_hint({"refs": [{"name": "x"}], "total_active": 3}), "")
        self.assertEqual(empty_hint({"refs": [], "total_active": 0}), "")


class TestBudget(unittest.TestCase):
    def test_degradation_never_empty(self):
        refs = [{"name": "a-very-long-name", "when": "很长的" * 60, "state": "observed",
                 "version": 3, "confidence": 0.5, "tool_key": "", "contradicts": [],
                 "score": 5}]
        tiny = {"refs": refs, "incomplete": False, "dropped_weak": 0,
                "conflict_pairs": []}
        # P2-4：三级降级各自可观测（level 1=完整行 / 2=只留名字 / 3=连名字都放不下）
        for budget, want_level in ((600, 1), (60, 2), (10, 2), (2, 3)):
            text, level = catalog_lines(tiny, token_budget=budget)
            self.assertTrue(text.strip())
            self.assertEqual(level, want_level, "budget=%d" % budget)
            if level < 3:
                self.assertLessEqual(estimate_tokens(text), max(budget, 20))
        # level 3 是「永不空手」的逃生舱：单行退守，宁超预算不许空手
        text3, _ = catalog_lines(tiny, token_budget=2)
        self.assertEqual(len(text3.splitlines()), 1)

    def test_estimate_tokens_cjk_heavier(self):
        self.assertGreater(estimate_tokens("一二三四五"), estimate_tokens("abcde"))


class TestLedgerGate(unittest.TestCase):
    """ExpeL 台账对检索的影响：counter≤0 移出书；counter 只进排序不进闸。"""

    FRONT = {"name": "stardew-probe-after-action", "description": "x",
             "state": "candidate", "tool_key": "stardew.state", "version": 1,
             "retrieval_anchor": "对账 state 存仓库 收庄稼",
             "task_query": "写操作后同回合核对结果"}

    def test_counter_le_zero_removed_from_book(self):
        import tempfile, os, json as J
        d = tempfile.mkdtemp()
        for nm, cnt in (("alive", 1), ("removed", 0), ("dead", -2)):
            os.makedirs(os.path.join(d, nm))
            front = dict(self.FRONT, name=nm, counter=cnt)
            with open(os.path.join(d, nm, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write("---\n%s\n---\n正文" % J.dumps(front, ensure_ascii=False))
        self.assertEqual([c["name"] for c in load_catalog(d)], ["alive"])

    def test_counter_missing_defaults_baseline(self):
        cat = load_catalog(SEED)   # 种子卡没有 counter 字段
        self.assertEqual(len(cat), 1)

    def test_counter_orders_but_never_gates(self):
        fronts = [
            dict(self.FRONT, name="thin", counter=1),
            dict(self.FRONT, name="thick", counter=15),
        ]
        r = retrieve_skills("对账 state 存仓库", _mk_catalog(fronts))
        # 同等证据命中：台账厚的排前面；两者都过闸（counter 不是闸）
        self.assertEqual([x["name"] for x in r["refs"]], ["thick", "thin"])


if __name__ == "__main__":
    unittest.main()
