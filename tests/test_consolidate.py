# -*- coding: utf-8 -*-
"""consolidate 规则版：拆优先于合、evidence 点得回、blocked 不炼卡、AGPL 闸。"""
import unittest

from expmem.consolidate import scan_candidates

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


if __name__ == "__main__":
    unittest.main()
