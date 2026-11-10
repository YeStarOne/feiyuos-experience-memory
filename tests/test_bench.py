# -*- coding: utf-8 -*-
"""bench 分母守卫：空分母不许算通过；interrupted/unavailable 不进分母。"""
import unittest

from expmem.bench import bench


def ep(v, probe="ok", game=False, acts=2, cls="", t0=1000, t1=2000):
    return {"verdict": v, "probe_status": probe, "failure_class": cls,
            "game": game, "act_count": acts, "t_from": t0, "t_to": t1}


class TestDenominator(unittest.TestCase):
    def test_empty_denominator_guard(self):
        # 全是 unknown/unavailable → 分母空，不许算出 0%/100%
        b = bench([ep("unknown", probe="unavailable")], [])
        self.assertEqual(b["denominator"], "empty")
        self.assertIsNone(b["success_rate"])

    def test_interrupted_excluded(self):
        b = bench([ep("interrupted"), ep("failure", cls="noop", t0=0, t1=0)], [])
        self.assertEqual(b["denominator"], 1)
        self.assertEqual(b["episodes_interrupted"], 1)
        self.assertEqual(b["failure_classes"].get("noop"), 1)


class TestGameMetrics(unittest.TestCase):
    def test_game_stats(self):
        eps = [ep("unknown", game=True, acts=3, t0=0, t1=10_000),
               ep("unknown", game=True, acts=1, t0=0, t1=20_000)]
        b = bench(eps, [])
        self.assertEqual(b["game_episodes"], 2)
        self.assertEqual(b["game_actions_per_episode"]["p50"], 2)
        self.assertEqual(b["game_episode_seconds"]["p50"], 15.0)


if __name__ == "__main__":
    unittest.main()
