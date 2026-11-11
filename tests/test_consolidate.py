# -*- coding: utf-8 -*-
"""consolidate 规则版：拆优先于合、evidence 点得回、blocked 不炼卡、AGPL 闸。"""
import unittest

from expmem.consolidate import dedupe_add, induct_specs, scan_candidates, scan_success

EP_STORE = {"episode_id": "ep_store", "verdict": "failure", "failure_class": "noop",
            "outcome": {"rule": "stored_empty", "action": "a1", "attribution": "segment"},
            "game_day": "冬25"}
ACT_STORE = {"id": "a1", "verb": "raw", "raw_path": "/store",
             "segment": "sd raw /store {\"id\":\"Cranberries\"}",
             "out_head": "{\"ok\": true, \"stored\": []}"}


class TestScan(unittest.TestCase):
    def test_single_failure_one_spec(self):
        specs, dropped = scan_candidates([EP_STORE], [ACT_STORE])
        self.assertEqual(len(specs), 1)
        s = specs[0]
        self.assertEqual(s["name"], "stardew-noop-store")
        self.assertEqual(s["tool_key"], "stardew.store")
        self.assertEqual(s["state"], "candidate")
        self.assertEqual(s["evidence"][0]["episode"], "ep_store")
        self.assertEqual(s["stats"]["game_days"], ["冬25"])
        self.assertEqual(dropped, [])

    def test_split_before_merge(self):
        # 不同 tool_key 各自一张 spec，绝不合并（拆优先于合）
        ep2 = {"episode_id": "ep_harv", "verdict": "failure", "failure_class": "noop",
               "outcome": {"rule": "harvest_zero_with_ready", "action": "a2",
                           "attribution": "segment"}, "game_day": "冬25"}
        act2 = {"id": "a2", "verb": "", "raw_path": "/interact",
                "segment": "sd raw /interact", "out_head": "收菜完成0件"}
        specs, _ = scan_candidates([EP_STORE, ep2], [ACT_STORE, act2])
        self.assertEqual({s["name"] for s in specs},
                         {"stardew-noop-store", "stardew-noop-interact"})

    def test_blocked_not_distilled(self):
        ep = {"episode_id": "ep_b", "verdict": "failure", "failure_class": "blocked",
              "outcome": {"rule": "blocked", "action": "a1"}}
        specs, _ = scan_candidates([ep], [ACT_STORE])
        self.assertEqual(specs, [])

    def test_unresolvable_action_dropped(self):
        ep = dict(EP_STORE, outcome={"rule": "stored_empty", "action": "ghost",
                                     "attribution": "segment"})
        specs, dropped = scan_candidates([ep], [ACT_STORE])
        self.assertEqual(specs, [])
        self.assertEqual(dropped[0]["reason"], "action_unresolvable")

    def test_fallback_attribution_not_distilled(self):
        # 归因是 fallback（调用藏在 python heredoc 等）→ 宁缺毋滥，不炼卡
        ep = dict(EP_STORE, outcome={"rule": "stored_empty", "action": "a1",
                                     "attribution": "fallback"})
        specs, dropped = scan_candidates([ep], [ACT_STORE])
        self.assertEqual(specs, [])
        self.assertEqual(dropped[0]["reason"], "attribution_fallback")

    def test_agpl_guard_rejects_original_text(self):
        act = dict(ACT_STORE, out_head="see viking://agent/skills for details")
        specs, dropped = scan_candidates([EP_STORE], [act])
        self.assertEqual(specs, [])
        self.assertEqual(dropped[0]["reason"], "agpl_guard")


class TestScanSuccess(unittest.TestCase):
    """成功轨：机械可验证效果（outcome 三条失败规则的干净反演）。"""

    def test_store_nonempty_is_success_evidence(self):
        ep = {"episode_id": "ep_ok", "verdict": "success", "probe_status": "ok",
              "actions": ["a1"], "game_day": "冬25"}
        act = dict(ACT_STORE, id="a1",
                   out_head='{"ok": true, "stored": [{"id":"Cranberries","count":3}]}')
        rows = scan_success([ep], [act])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rule"], "stored_nonempty")
        self.assertEqual(rows[0]["verdict"], "success")

    def test_stored_empty_never_success(self):
        ep = {"episode_id": "ep_ok", "verdict": "unknown", "actions": ["a1"]}
        rows = scan_success([ep], [ACT_STORE])   # out_head 是 stored:[]（noop 实锤）
        self.assertEqual(rows, [])

    def test_harvest_count_positive(self):
        ep = {"episode_id": "ep_h", "verdict": "success", "actions": ["a2"]}
        act = {"id": "a2", "verb": "", "raw_path": "/interact",
               "segment": "sd raw /interact", "out_head": "收菜完成 12 件"}
        rows = scan_success([ep], [act])
        self.assertEqual(rows[0]["rule"], "harvest_count_12")

    def test_failure_episode_never_success_track(self):
        rows = scan_success([EP_STORE], [ACT_STORE])   # verdict=failure
        self.assertEqual(rows, [])


class TestDedupeAdd(unittest.TestCase):
    """ExpeL ADD 按子串去重：verified 确认 / candidate 并池 / 无匹配进池。"""

    VERIFIED = {"name": "stardew-store", "state": "verified",
                "evidence": [{"cmd": 'sd raw /store {"id":"Cranberries"}',
                              "out": '{"ok": true, "stored": []}'}]}
    CANDIDATE = {"name": "stardew-buy", "state": "candidate",
                 "evidence": [{"cmd": "sd buy --id 481 --quantity 2",
                               "out": '{"ok":true,"totalCost":4800}'}]}

    def test_duplicate_of_verified_confirms(self):
        row = {"cmd": 'sd raw /store {"id":"Cranberries"}',
               "out": '{"ok": true, "stored": []}'}
        confirmed, pooled, fresh = dedupe_add([row], [self.VERIFIED])
        self.assertEqual(confirmed, {"stardew-store": 1})
        self.assertEqual(fresh, [])

    def test_duplicate_of_candidate_pools_not_confirms(self):
        # 「未验证的文本不许被未验证的证据确认」——candidate 只并池
        row = {"cmd": "sd buy --id 481 --quantity 2",
               "out": '{"ok":true,"totalCost":4800}'}
        confirmed, pooled, fresh = dedupe_add([row], [self.CANDIDATE])
        self.assertEqual(confirmed, {})
        self.assertEqual(pooled, {"stardew-buy"})

    def test_fresh_evidence_goes_to_pool(self):
        row = {"cmd": "sd raw /fish", "out": '{"bite": true}'}
        confirmed, pooled, fresh = dedupe_add([row], [self.VERIFIED])
        self.assertEqual(confirmed, {})
        self.assertEqual(pooled, set())
        self.assertEqual(len(fresh), 1)

    def test_tiny_fragment_never_matches(self):
        # 8 字下限：碰瓷式短串不算「同一经验再次发生」
        row = {"cmd": "sd raw /store", "out": "{}"}
        confirmed, pooled, fresh = dedupe_add([row], [self.VERIFIED])
        self.assertEqual(confirmed, {})
        self.assertEqual(len(fresh), 1)


class TestInductSpecs(unittest.TestCase):
    """AWM 整份重写：两套卡段 / 薄池闸 / confirmed 台账操作。"""

    def _three_failures(self):
        eps, acts = [], []
        for i, n in enumerate(("Cranberries", "Cranberries", "Parsnips")):
            eps.append({"episode_id": "ep%d" % i, "verdict": "failure",
                        "failure_class": "noop",
                        "outcome": {"rule": "stored_empty", "action": "a%d" % i,
                                    "attribution": "segment"},
                        "game_day": "冬2%d" % (i + 5)})
            acts.append({"id": "a%d" % i, "verb": "raw", "raw_path": "/store",
                         "segment": 'sd raw /store {"id":"%s"}' % n,
                         "out_head": '{"ok": true, "stored": []}'})
        return eps, acts

    def test_full_file_induction_with_both_tracks(self):
        eps, acts = self._three_failures()
        # 再给一条成功轨证据（同 tool_key）
        eps.append({"episode_id": "ep_ok", "verdict": "success", "probe_status": "ok",
                    "actions": ["a9"], "game_day": "冬28"})
        acts.append({"id": "a9", "verb": "raw", "raw_path": "/store",
                     "segment": 'sd raw /store {"id":"Parsnips"}',
                     "out_head": '{"ok": true, "stored": [{"id":"Parsnips","count":2}]}'})
        specs, ops, dropped = induct_specs(eps, acts)
        self.assertEqual(len(specs), 1)
        s = specs[0]
        self.assertEqual(s["tool_key"], "stardew.store")
        self.assertEqual((s["stats"]["failure"], s["stats"]["success"]), (3, 1))
        self.assertIn("## 成功轨", s["body"])
        self.assertIn("## 失败轨", s["body"])
        self.assertIn("stored_nonempty", s["body"])       # 成功段有内容
        self.assertIn("（noop）", s["body"])               # 失败段带形态
        self.assertEqual(s["state"], "candidate")

    def test_thin_pool_gate_holds_sparse_keys(self):
        # GMemory 阈值起步：1 条失败证据的键不开卡（空对空归纳）
        eps, acts = self._three_failures()
        specs, ops, _ = induct_specs(eps[:1], acts[:1])
        self.assertEqual(specs, [])
        self.assertEqual(ops["held_thin"], 1)

    def test_thin_pool_gate_counts_occurrences_not_rows(self):
        # intel 复审：同一剧集重试 3 次 ≠ 三次发生——行数够但全在一集 → 仍扣
        eps, acts = self._three_failures()
        for i in range(3):
            eps[i] = dict(eps[i], episode_id="ep_same", game_day="冬25")
        specs, ops, _ = induct_specs(eps, acts)
        self.assertEqual(specs, [])
        self.assertEqual(ops["held_thin"], 3)

    def test_stats_rows_is_honest(self):
        # 字段名不再骗人：rows=证据行数，episodes=去重集数
        eps, acts = self._three_failures()
        specs, _, _ = induct_specs(eps, acts)
        self.assertEqual(specs[0]["stats"]["rows"], 3)      # 3 条失败证据行
        self.assertEqual(specs[0]["stats"]["episodes"], 3)  # 三个不同剧集

    def test_verified_duplicates_become_confirmations(self):
        eps, acts = self._three_failures()
        existing = [{"name": "stardew-store", "state": "verified",
                     "evidence": [{"cmd": 'sd raw /store {"id":"Cranberries"}',
                                   "out": '{"ok": true, "stored": []}'}]}]
        specs, ops, _ = induct_specs(eps, acts, existing_fronts=existing)
        # Cranberries 两条与 verified 卡互为子串 → 确认；Parsnips 1 条 fresh（<3 薄）
        self.assertEqual(ops["confirmed"], {"stardew-store": 2})
        self.assertEqual(specs, [])
        self.assertEqual(ops["held_thin"], 1)

    def test_body_passes_body_gate(self):
        # 正文模板必须能过 skills.lint_body（evidence 里的坐标/金额不进正文）
        from expmem.skills import lint_body
        eps, acts = self._three_failures()
        acts[0]["segment"] = "sd warp Beach 44 35; sd raw /store"
        eps[0]["game_day"] = ""
        specs, _, _ = induct_specs(eps, acts)
        lint_body(specs[0]["body"])     # 不抛 = 模板+消毒有效

    def test_agpl_guard_applies_to_body(self):
        eps, acts = self._three_failures()
        acts[0]["segment"] = "sd state  # see viking://agent/skills"
        specs, _, dropped = induct_specs(eps, acts)
        for s in specs:
            self.assertNotIn("viking://", s["body"])


if __name__ == "__main__":
    unittest.main()
