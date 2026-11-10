# -*- coding: utf-8 -*-
"""双向 orphan：card↔action↔episode 三向点得回（每向都有已知该抓的孤儿样本）。"""
import unittest

from expmem.orphan import orphan_report

ACT = {"id": "a1", "session": "s", "call_id": "call9", "sub_idx": 0, "verb": "raw", "raw_path": "/store",
       "segment": "sd raw /store", "out_head": '{"stored": []}'}
EP = {"episode_id": "ep1", "session": "s", "actions": ["a1"], "verdict": "failure"}
CARD = {"name": "c1", "evidence": [{"episode": "ep1", "action": "a1",
                                    "cmd": "sd raw /store", "out": "{}"}]}


class TestCleanFixture(unittest.TestCase):
    def test_complete_chain_has_zero_orphans(self):
        r = orphan_report(cards=[CARD], actions=[ACT], episodes=[EP])
        self.assertEqual(r["counts"]["orphans_total"], 0)
        for k in ("card_ghost_action", "card_cross_episode",
                  "episode_ghost_action", "action_without_episode"):
            self.assertEqual(r[k], [], k)

    def test_empty_world_is_trivially_green(self):
        r = orphan_report()
        self.assertEqual(r["counts"]["orphans_total"], 0)


class TestEachDirection(unittest.TestCase):
    def test_card_pointing_to_ghost_action(self):
        card = {"name": "c1", "evidence": [{"episode": "ep1", "action": "ghost"}]}
        r = orphan_report(cards=[card], actions=[ACT], episodes=[EP])
        self.assertEqual(r["card_ghost_action"][0]["card"], "c1")

    def test_card_pointing_to_unknown_episode(self):
        # 动作能解析而 episode id 不存在 = 跨版本重编号的良性陈旧锚（不算孤儿）
        card = {"name": "c1", "evidence": [{"episode": "ep_ghost", "action": "a1"}]}
        r = orphan_report(cards=[card], actions=[ACT], episodes=[EP])
        self.assertEqual(r["card_ghost_action"], [])
        self.assertEqual(r["card_stale_episode_id"][0]["episode"], "ep_ghost")

    def test_card_cross_episode_reference(self):
        # 编号漂移：action 存在但属于另一个 episode——比缺失更隐蔽，单独一类
        card = {"name": "c1", "evidence": [{"episode": "ep_wrong", "action": "a1"}]}
        ep_wrong = {"episode_id": "ep_wrong", "actions": [], "verdict": "unknown"}
        r = orphan_report(cards=[card], actions=[ACT], episodes=[EP, ep_wrong])
        self.assertEqual(r["card_cross_episode"][0]["in"], ["ep1"])

    def test_episode_listing_ghost_action(self):
        ep = {"episode_id": "ep2", "actions": ["ghost"], "verdict": "unknown"}
        r = orphan_report(cards=[CARD], actions=[ACT], episodes=[EP, ep])
        self.assertEqual(r["episode_ghost_action"][0]["episode"], "ep2")

    def test_action_outside_every_episode(self):
        # turn 外发的 bash 落在所有剧集之外——观测位，不修先报数
        lone = dict(ACT, id="a_lonely")
        r = orphan_report(cards=[CARD], actions=[ACT, lone], episodes=[EP])
        self.assertEqual(r["action_without_episode"], [{"action": "a_lonely"}])

    def test_counts_are_honest(self):
        r = orphan_report(cards=[CARD], actions=[ACT], episodes=[EP])
        self.assertEqual(r["counts"],
                         {"cards": 1, "actions": 1, "episodes": 1,
                          "orphans_total": 0})


class TestStableTripleAnchor(unittest.TestCase):
    """11-11 首跑实锤：跨版本 replay 重编号 episode/action id（gap 切分级联），
    直接 id 锚全变孤儿——evidence 带 (session, call_id, sub_idx) 三元组时按它解。"""

    def test_stale_ids_resolve_via_triple(self):
        card = {"name": "c1", "evidence": [
            {"episode": "old_ep_id", "action": "old_act_id",
             "session": "s", "call_id": "call9", "sub_idx": 0}]}
        r = orphan_report(cards=[card], actions=[ACT], episodes=[EP])
        self.assertEqual(r["card_ghost_action"], [])
        self.assertEqual(r["card_stale_episode_id"],
                         [{"card": "c1", "episode": "old_ep_id",
                           "resolved_action": "a1"}])
        self.assertEqual(r["counts"]["orphans_total"], 0)

    def test_triple_miss_is_still_ghost(self):
        card = {"name": "c1", "evidence": [
            {"episode": "old", "action": "old",
             "session": "s", "call_id": "nope", "sub_idx": 0}]}
        r = orphan_report(cards=[card], actions=[ACT], episodes=[EP])
        self.assertEqual(r["card_ghost_action"][0]["card"], "c1")

    def test_existing_episode_id_not_containing_action_is_cross(self):
        card = {"name": "c1", "evidence": [
            {"episode": "ep1", "action": "old_act_id",
             "session": "s", "call_id": "call9", "sub_idx": 0}]}
        r = orphan_report(cards=[card], actions=[ACT], episodes=[EP])
        # ep1 存在但动作 a1 在里面——等等，a1 确实在 ep1 里，所以不算 cross
        self.assertEqual(r["card_cross_episode"], [])
        self.assertEqual(r["card_stale_episode_id"], [])


class TestInductAnchors(unittest.TestCase):
    """归纳产物的 evidence 锚必须三向点得回（card→action→episode 闭链）。"""
    def test_induct_spec_evidence_resolves(self):
        from expmem.consolidate import induct_specs
        eps, acts = [], []
        for i in range(3):
            eps.append({"episode_id": "ep%d" % i, "verdict": "failure",
                        "failure_class": "noop",
                        "outcome": {"rule": "stored_empty", "action": "a%d" % i,
                                    "attribution": "segment"},
                        "actions": ["a%d" % i], "game_day": "冬25"})
            acts.append({"id": "a%d" % i, "verb": "raw", "raw_path": "/store",
                         "segment": 'sd raw /store {"id":"X%d"}' % i,
                         "out_head": '{"ok": true, "stored": []}'})
        specs, _, dropped = induct_specs(eps, acts)
        self.assertEqual(len(specs), 1)
        r = orphan_report(cards=[specs[0]], actions=acts, episodes=eps)
        self.assertEqual(r["counts"]["orphans_total"], 0)
        self.assertEqual(dropped, [])


if __name__ == "__main__":
    unittest.main()
