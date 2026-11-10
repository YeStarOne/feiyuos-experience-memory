# -*- coding: utf-8 -*-
"""feedback：ExpeL 台账（counter/conf/test_count）+ A-MEM 三守卫。
夹具纪律（cmb）：每条规则都有已知该拒/该收的真实形态样本，不许只测 happy path。"""
import unittest

from expmem.feedback import apply_feedback, parse_feedback, record_feedback

FRONT = {"name": "stardew-probe-after-action", "state": "candidate",
         "counter": 1, "test_count": 0, "strong_ok": 0, "strong_fail": 0,
         "confidence": 0.55, "contradicts": ["buy-blind"], "tags": ["对账"]}
SHOWN = ("stardew-probe-after-action",)
NL = chr(10)   # 夹具里 jsonl 行尾（避免源码里转义折行）


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

    def test_harness_names_do_not_block_settling(self):
        # cmb-P1：manifest 里的 harness 名（无目录）必须被忽略——否则 settled
        # 永远写不进去，我们的卡每次 settle 被重标一遍（counter 线性灌水）
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
            with open(os.path.join(d, ".injected.jsonl"), "w",
                      encoding="utf-8") as f:
                f.write(J.dumps({"ts": 0, "session": "s1", "turn": 1,
                                 "names": ["agent-experience",
                                           "cordis-plugin-development",
                                           "stardew-probe-after-action"]}) + NL)
            eps = [{"session": "s1", "turn": 1, "verdict": "success",
                    "episode_id": "e1", "actions": ["a1"]}]
            acts = [{"id": "a1", "verb": "state", "raw_path": "",
                     "segment": "sd state"}]
            out = api.settle_auto_marks(eps, acts, d)
            self.assertEqual(out["marked"], {"stardew-probe-after-action": 1})
            self.assertTrue(os.path.exists(os.path.join(d, ".settled.jsonl")))
            out2 = api.settle_auto_marks(eps, acts, d)   # 幂等不破
            self.assertEqual(out2["marked"], {})
            self.assertGreaterEqual(out2["already_settled"], 1)
        finally:
            del os.environ["EXPMEM_SKILLS_DIR"]

    def test_duplicate_manifest_rows_do_not_double_mark(self):
        # cmb-P3a：同 (session,turn) 多行 retrieve 合并去重，一张卡单轮只标一次
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
            with open(os.path.join(d, ".injected.jsonl"), "w",
                      encoding="utf-8") as f:
                for _ in range(3):
                    f.write(J.dumps({"ts": 0, "session": "s1", "turn": 1,
                                     "names": [
                                         "stardew-probe-after-action"]}) + NL)
            eps = [{"session": "s1", "turn": 1, "verdict": "success",
                    "episode_id": "e1", "actions": ["a1"]}]
            acts = [{"id": "a1", "verb": "state", "raw_path": "",
                     "segment": "sd state"}]
            out = api.settle_auto_marks(eps, acts, d)
            self.assertEqual(out["marked"], {"stardew-probe-after-action": 1})
        finally:
            del os.environ["EXPMEM_SKILLS_DIR"]

    def test_settle_missing_card_keeps_turn_unsettled(self):
        # intel 复审 A：目录在而 SKILL.md 缺（真损）= 该回合不结（下次重试）。
        # 无目录的名字是 harness 噪声，另一条夹具（忽略并结清）。
        import os
        import json as J
        import tempfile
        from expmem import api
        d = tempfile.mkdtemp()
        os.environ["EXPMEM_SKILLS_DIR"] = d
        try:
            os.makedirs(os.path.join(d, "broken-card"))    # 目录在，卡不在
            with open(os.path.join(d, ".injected.jsonl"), "w",
                      encoding="utf-8") as f:
                f.write(J.dumps({"ts": 0, "session": "s1", "turn": 1,
                                 "names": ["broken-card"]}) + "\n")
            eps = [{"session": "s1", "turn": 1, "verdict": "success",
                    "episode_id": "e1", "actions": []}]
            out = api.settle_auto_marks(eps, [], d)
            self.assertEqual(out["marked"], {})
            self.assertFalse(os.path.exists(os.path.join(d, ".settled.jsonl")),
                             "settled 未写=可重试")
        finally:
            del os.environ["EXPMEM_SKILLS_DIR"]

    def test_derive_manifest_production_shape(self):
        # cmb-P0（复审）：生产 user/message.data 没有 text 键，正文在
        # content[0].text——旧回退条件 `not isinstance(text, str)` 被空串
        # 骗过（空串也是 str）→ 生产恒 {}。夹具必须用生产形。
        import os
        import json as J
        import tempfile
        from expmem import api
        d = tempfile.mkdtemp()
        sd = os.path.join(d, "onebot-private-1792612052-test")
        os.makedirs(sd)
        msg = ("Here are your available skills:\n<available_skills>\n"
               "- `stardew-store`: Use when storing items\n"
               "- `agent-experience`: harness noise\n"
               "</available_skills>\n")
        ev = [
            {"type": "turn/start", "time": 1, "data": {"turn": 9}},
            # 生产形：data 只有 content/source/role/id，没有 text
            {"type": "user/message", "time": 2,
             "data": {"role": "user", "id": "m1", "source": {"q": "qq"},
                      "content": [{"type": "text", "text": msg}]}},
        ]
        with open(os.path.join(sd, "session.v4.jsonl"), "w",
                  encoding="utf-8") as f:
            for e in ev:
                f.write(J.dumps(e, ensure_ascii=False) + NL)
        got = api.derive_manifest(d)
        self.assertEqual(got, {("onebot-private-1792612052-test", 9):
                               ["stardew-store", "agent-experience"]})

    def test_settle_aggregates_same_turn_episodes(self):
        # cmb-P1（复审）：同 (session,turn) 多集（gap 切分）——used 必须看
        # 组内全部集动作的并集；verdict=任一集 success 即回合 success。
        # 旧实现只看首集：e1 unknown 早退把 skey 结清 → e2 的动作没人看。
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
            with open(os.path.join(d, ".injected.jsonl"), "w",
                      encoding="utf-8") as f:
                f.write(J.dumps({"ts": 0, "session": "s1", "turn": 1,
                                 "names": ["stardew-probe-after-action"]}) + NL)
            eps = [
                {"session": "s1", "turn": 1, "verdict": "unknown",
                 "episode_id": "e1", "actions": ["a_other"]},
                {"session": "s1", "turn": 1, "verdict": "success",
                 "episode_id": "e2", "actions": ["a1"]},
            ]
            acts = [{"id": "a_other", "verb": "near", "raw_path": "",
                     "segment": "sd near 10"},
                    {"id": "a1", "verb": "state", "raw_path": "",
                     "segment": "sd state"}]
            out = api.settle_auto_marks(eps, acts, d)
            self.assertEqual(out["marked"],
                             {"stardew-probe-after-action": 1})
            self.assertEqual(out["reasons"], {"complete": 1})
            front, _ = __import__("expmem.skills", fromlist=["load_skill"]) \
                .load_skill(os.path.join(d, "stardew-probe-after-action",
                                         "SKILL.md"))
            self.assertEqual(front["counter"], 2)   # used=True 加分，非 not_used
            out2 = api.settle_auto_marks(eps, acts, d)   # 同轮双集只结一次
            self.assertEqual(out2["marked"], {})
            self.assertEqual(out2["already_settled"], 1)
        finally:
            del os.environ["EXPMEM_SKILLS_DIR"]

    def test_settle_version_gate_resettles_old_rows(self):
        # cmb-P1-forward：settled 行带 v+reason；v 落后判据版（含旧格式无 v）
        # = 视为未结算——S4 抬版本后历史「unknown 结清」要能重开。
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
            with open(os.path.join(d, ".injected.jsonl"), "w",
                      encoding="utf-8") as f:
                f.write(J.dumps({"ts": 0, "session": "s1", "turn": 1,
                                 "names": ["stardew-probe-after-action"]}) + NL)
            # 旧格式行：没有 v → 不许封死，重判照跑
            with open(os.path.join(d, ".settled.jsonl"), "w",
                      encoding="utf-8") as f:
                f.write(J.dumps({"session": "s1", "turn": 1}) + NL)
            eps = [{"session": "s1", "turn": 1, "verdict": "success",
                    "episode_id": "e1", "actions": ["a1"]}]
            acts = [{"id": "a1", "verb": "state", "raw_path": "",
                     "segment": "sd state"}]
            out = api.settle_auto_marks(eps, acts, d)
            self.assertEqual(out["marked"],
                             {"stardew-probe-after-action": 1})
            row = J.loads(open(os.path.join(d, ".settled.jsonl"),
                               encoding="utf-8").read().strip().splitlines()[-1])
            self.assertEqual(row["v"], api.SETTLE_VERDICT_V)
            self.assertEqual(row["reason"], "complete")
            out2 = api.settle_auto_marks(eps, acts, d)   # v=当前版 → 幂等照旧
            self.assertEqual(out2["marked"], {})
            self.assertEqual(out2["already_settled"], 1)
            # 模拟 S4 抬版：v=1 的行重新开放结算
            old_v = api.SETTLE_VERDICT_V
            api.SETTLE_VERDICT_V = 2
            try:
                out3 = api.settle_auto_marks(eps, acts, d)
                self.assertEqual(out3["marked"],
                                 {"stardew-probe-after-action": 1})
            finally:
                api.SETTLE_VERDICT_V = old_v
        finally:
            del os.environ["EXPMEM_SKILLS_DIR"]


if __name__ == "__main__":
    unittest.main()
