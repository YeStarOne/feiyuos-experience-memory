# -*- coding: utf-8 -*-
"""SkillRegistry：往返一致 + 状态机闸门（机器禁升/逐级/降级留盘）。"""
import os
import tempfile
import unittest

from expmem.skills import (
    StateError, load_skill, parse_skill, render_skill, save_skill, transition,
)

FRONT = {
    "name": "stardew-probe-after-action",
    "description": "测试卡",
    "tool_key": "stardew.state",
    "state": "candidate",
    "version": 1,
    "valid_from": {"date": "2026-11-11", "game_day": "冬25"},
    "evidence": [{"episode": "ep_x", "cmd": "sd state", "out": "ok", "verdict": "failure"}],
    "confidence": 0.55,
    "contradicts": [],
    "superseded_by": None,
    "retrieval_anchor": "对账 存进去没 买到没",
    "task_query": "写操作后同回合 sd state 核对",
}
BODY = "# 动作后当场对账\n\n## 步骤\n1. sd state"


class TestRoundtrip(unittest.TestCase):
    def test_render_parse_roundtrip(self):
        text = render_skill(FRONT, BODY)
        front2, body2 = parse_skill(text)
        self.assertEqual(front2, FRONT)
        self.assertEqual(body2.strip(), BODY.strip())
        self.assertTrue(text.startswith("---\n{"))  # JSON frontmatter，dsh 的 YAML 解析器可读

    def test_missing_required_field_rejected(self):
        bad = {k: v for k, v in FRONT.items() if k != "description"}
        with self.assertRaises(StateError):
            render_skill(bad, BODY)


class TestStateMachine(unittest.TestCase):
    def test_upgrade_path_legal(self):
        f = dict(FRONT)
        f = transition(f, "observed", actor="owner")
        self.assertEqual(f["state"], "observed")
        self.assertEqual(f["version"], 2)
        f = transition(f, "verified", actor="owner")
        self.assertEqual(f["state"], "verified")

    def test_machine_cannot_upgrade(self):
        with self.assertRaises(StateError):
            transition(dict(FRONT), "observed", actor="machine")

    def test_skip_level_rejected(self):
        with self.assertRaises(StateError):
            transition(dict(FRONT), "verified", actor="owner")

    def test_degrade_from_any_state(self):
        for st in ("candidate", "observed", "verified"):
            f = transition(dict(FRONT, state=st), "deprecated", actor="machine")
            self.assertEqual(f["state"], "deprecated")
            self.assertIn("superseded_by", f)

    def test_deprecated_cannot_revive(self):
        f = transition(dict(FRONT), "deprecated", actor="machine")
        with self.assertRaises(StateError):
            transition(f, "observed", actor="owner")

    def test_deprecated_stays_on_disk(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "stardew-probe-after-action", "SKILL.md")
        save_skill(p, dict(FRONT), BODY)
        f = transition(load_skill(p)[0], "deprecated", actor="machine")
        save_skill(p, f, BODY)          # 同名覆盖：旧版本被覆盖，但卡留盘不再可检索
        front, _ = load_skill(p)
        self.assertEqual(front["state"], "deprecated")
        self.assertTrue(os.path.exists(p))


if __name__ == "__main__":
    unittest.main()
