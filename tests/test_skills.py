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
        # SkillWeaver 清零后的爬升闸：owner 也只能升「被真实执行过」的卡
        f = dict(FRONT, test_count=1)
        f = transition(f, "observed", actor="owner")
        self.assertEqual(f["state"], "observed")
        self.assertEqual(f["version"], 2)
        f = transition(f, "verified", actor="owner")
        self.assertEqual(f["state"], "verified")

    def test_upgrade_blocked_while_untested(self):
        # 「induction only from verified」的机械等价：test_count=0 爬不了级，
        # owner 也不行——归纳产物一律从 candidate 重新爬
        with self.assertRaises(StateError):
            transition(dict(FRONT), "observed", actor="owner")

    def test_machine_cannot_upgrade(self):
        with self.assertRaises(StateError):
            transition(dict(FRONT, test_count=1), "observed", actor="machine")

    def test_pending_review_lifecycle(self):
        # cmb-P1-2：conflict 卡落 pending_review——机器可打（不是升级），二审撤回/降废
        f = transition(dict(FRONT), "pending_review", actor="machine")
        self.assertEqual(f["state"], "pending_review")
        f = transition(f, "candidate", actor="owner")   # 二审撤回
        self.assertEqual(f["state"], "candidate")
        f = transition(dict(FRONT), "pending_review", actor="machine")
        with self.assertRaises(StateError):
            transition(f, "observed", actor="owner")     # pending 不可直升
        with self.assertRaises(StateError):
            transition(f, "verified", actor="owner")

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


class TestBodyGates(unittest.TestCase):
    """正文机械闸——每个形态都有已知该拒样本（cmb 三档判读：无拒收夹具=假绿）。
    evidence 不在闸内：证据必须逐字保留，坐标/金额出现在 evidence 必须放行。"""

    REJECT_BODIES = (
        ("绝对坐标", "从农场 (65,21) 出发先看体力"),
        ("全角坐标", "从农场（65,21）出发"),
        ("JSON 坐标", '点这里 /click {"x":767,"y":538}'),
        ("shell 转义 JSON 坐标", '存鱼 \\"x\\":52,\\"y\\":13 一条命令写完'),
        ("G 数", "种子大约 50g 一包，买之前看钱差"),
        ("钱数字", "对账时钱142538 是错的基准"),
        ("gold 数字", "gold: 500 才够买"),
    )

    def test_known_reject_fixtures(self):
        from expmem.skills import lint_body
        for label, body in self.REJECT_BODIES:
            with self.assertRaises(StateError, msg=label):
                lint_body(body)

    def test_rules_without_numbers_pass(self):
        from expmem.skills import lint_body
        lint_body("买东西 → sd state 看钱差是否等于 标价×数量；stored:[] = 没存进去")
        lint_body('surroundings 用模板 {"x":<x>,"y":<y>} 传相对偏移')   # 占位符不是具体值
        lint_body("ready>0 而收 0 件 = 空转")          # 小数字不是金额

    def test_evidence_with_coordinates_must_pass(self):
        # cmb 约束①：唯一真卡的 evidence 里就是 chestAt 坐标——闸扫证据=当场拒掉唯一的卡
        d = tempfile.mkdtemp()
        p = os.path.join(d, "c", "SKILL.md")
        save_skill(p, dict(FRONT), BODY)    # FRONT.evidence 无坐标；再造一张有的
        front2 = dict(FRONT, name="coord-evidence-card",
                      evidence=[{"episode": "e", "cmd": "sd raw /store",
                                 "out": '{"stored":[],"chestAt":{"x":65,"y":15}}',
                                 "verdict": "failure"}])
        save_skill(p, front2, BODY)         # 不抛 = evidence 放行
        self.assertEqual(load_skill(p)[0]["name"], "coord-evidence-card")

    def test_sanitize_for_body_strips_concrete_values(self):
        from expmem.skills import sanitize_for_body, lint_body
        raw = "sd warp Beach 44 35 然后 click (65,21) 记得钱142538 与 50g"
        clean = sanitize_for_body(raw)
        lint_body(clean)                    # 消毒后必须过闸
        self.assertNotIn("142538", clean)


class TestCatalogVisibility(unittest.TestCase):
    """目录可见性闸：进不了她 skill 目录的卡 = 没写。

    11-12 实测事故：`stardew-mine-fight/SKILL.md` 是纯 markdown、没有 frontmatter，
    dsh 的 parseSkillFile 直接跳过它——卡写得多好都进不了目录，**而且没有任何报错**。
    全库 22 张卡里 1 张是这样静默不可见的，靠人工体检才发现。
    这里把三个静默丢弃点都做成闸：
      ① frontmatter 必须是合法 JSON  ② description 非空  ③ description ≤ 500
    """

    def test_card_without_frontmatter_cannot_be_rendered(self):
        # 纯 markdown 卡：连 render 都过不去（不是靠人肉发现）
        from expmem.skills import parse_skill
        with self.assertRaises(StateError):
            parse_skill("# 挖矿打怪卡\n\n## 前置\n1. 先借身\n")

    def test_empty_description_rejected(self):
        from expmem.skills import lint_catalog_visibility
        for bad in ("", "   ", None):
            with self.assertRaises(StateError, msg=repr(bad)):
                lint_catalog_visibility({"name": "x", "description": bad})

    def test_description_over_catalog_cap_rejected(self):
        # 超 500 会被 dsh 静默截断（catalogDescriptionMaxLength 默认 500），
        # 不是报错而是后半句消失——所以必须在产卡时拒
        from expmem.skills import lint_catalog_visibility, CATALOG_DESC_MAX
        ok = {"name": "x", "description": "字" * CATALOG_DESC_MAX}
        lint_catalog_visibility(ok)                       # 正好 500 放行
        over = {"name": "x", "description": "字" * (CATALOG_DESC_MAX + 1)}
        with self.assertRaises(StateError):
            lint_catalog_visibility(over)

    def test_render_skill_enforces_visibility(self):
        # 闸必须挂在产出路径上，不能只当独立函数存在（否则没人调它）
        from expmem.skills import render_skill
        with self.assertRaises(StateError):
            render_skill(dict(FRONT, description=""), BODY)

    def test_real_repo_cards_all_visible(self):
        # 回归：仓里每张真卡都必须能过这道闸（防止以后加卡忘了 description）
        import glob
        from expmem.skills import load_skill, lint_catalog_visibility
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "cards")
        paths = glob.glob(os.path.join(root, "*", "SKILL.md"))
        self.assertTrue(paths, "cards/ 下没找到任何卡，闸等于空转")
        for p in paths:
            front, _ = load_skill(p)          # 解析不抛 = frontmatter 合法
            lint_catalog_visibility(front)    # 不抛 = 能进目录


class TestRewriteCard(unittest.TestCase):
    """AWM 整份重写 + SkillWeaver 改一次验证清零。"""

    def test_content_change_resets_verification(self):
        from expmem.skills import rewrite_card
        old_front = dict(FRONT, state="verified", version=3, test_count=4,
                         counter=7, strong_ok=4)
        nf = dict(FRONT, retrieval_anchor="对账 存进去没 买到没 补充词")
        new = rewrite_card(old_front, BODY, nf, BODY + "\n补一行")
        self.assertEqual(new["version"], 4)          # version+1
        self.assertEqual(new["test_count"], 0)       # 验证清零
        self.assertEqual(new["counter"], 1)          # 台账回基线（旧反馈属于旧文本）
        self.assertEqual(new["state"], "candidate")  # verified 打回重爬

    def test_no_content_change_keeps_ledger(self):
        from expmem.skills import rewrite_card
        old_front = dict(FRONT, state="verified", version=3, test_count=4, counter=7)
        new = rewrite_card(old_front, BODY, dict(FRONT), BODY)
        self.assertEqual(new["version"], 3)
        self.assertEqual(new["test_count"], 4)
        self.assertEqual(new["counter"], 7)
        self.assertEqual(new["state"], "verified")

    def test_deprecated_never_revives_via_rewrite(self):
        from expmem.skills import rewrite_card
        old = dict(FRONT, state="deprecated", version=5,
                   superseded_by="newer-card")
        new = rewrite_card(old, BODY, dict(FRONT, retrieval_anchor="改了"), "新正文")
        self.assertEqual(new["state"], "deprecated")
        self.assertEqual(new["superseded_by"], "newer-card")

    def test_rewrite_rejects_gate_violating_body(self):
        from expmem.skills import rewrite_card
        with self.assertRaises(StateError):
            rewrite_card(dict(FRONT), BODY, dict(FRONT), "去 (65,21) 挖矿")

    def test_ledger_defaults_normalized(self):
        from expmem.skills import normalize_ledger, is_tested
        f = normalize_ledger(dict(FRONT))
        self.assertEqual((f["counter"], f["test_count"], f["strong_ok"],
                          f["strong_fail"]), (1, 0, 0, 0))
        self.assertFalse(is_tested(f))


if __name__ == "__main__":
    unittest.main()
