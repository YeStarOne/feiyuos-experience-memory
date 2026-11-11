# -*- coding: utf-8 -*-
"""评审 11-12 修复的验收测试（P1-1/P1-2/P1-4 + 四条 P2）。

验收口径按评审第五节修复顺序表：
1) unknown/interrupted 的正向结果不进成功轨；
2) 一条批量命令不能把同一个结果证明成每条子命令的成功；
3) 并发 settle 只结一次、原子写不留半截卡；
4) 错误类型/路径穿越/无展示记录被拒绝。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

from expmem.consolidate import scan_success
from expmem.feedback import parse_feedback
from expmem.skills import (StateError, transition, render_skill, save_skill,
                           load_skill)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FRONT = {
    "name": "stardew-a",
    "description": "测试卡",
    "tool_key": "stardew.store",
    "state": "candidate",
    "version": 1,
    "valid_from": {"date": "2026-11-11", "game_day": "冬25"},
    "evidence": [{"episode": "ep_x", "cmd": "sd state", "out": "ok",
                  "verdict": "failure"}],
    "confidence": 0.55,
    "contradicts": [],
    "superseded_by": None,
    "retrieval_anchor": "对账 存进去没",
    "task_query": "写操作后同回合核对",
}
BODY = "# 动作后当场对账\n\n## 步骤\n1. sd state"


def _ep(verdict, aids, session="s1", turn=1):
    return {"episode_id": "ep_%s_%s" % (verdict, turn), "verdict": verdict,
            "session": session, "turn": turn, "actions": aids, "game_day": "冬25"}


def _act(aid, sub_idx, out, call_id="c1", session="s1", verb="raw",
         raw_path="/store"):
    return {"id": aid, "verb": verb, "raw_path": raw_path,
            "segment": "sd raw /store", "out_head": out,
            "session": session, "call_id": call_id, "sub_idx": sub_idx}


SEG_EMPTY = '{"ok": true, "stored": []}'
SEG_HIT = '{"ok": true, "stored": [{"item": "Bullhead", "count": 39}]}'


class TestSuccessTrackStrict(unittest.TestCase):
    """P1-1：只认 episode success——unknown/interrupted 的正向结果不进成功轨。"""

    def test_unknown_positive_signal_excluded(self):
        ep = _ep("unknown", ["a1"])
        act = _act("a1", 0, SEG_HIT)
        self.assertEqual(scan_success([ep], [act]), [])

    def test_interrupted_positive_signal_excluded(self):
        ep = _ep("interrupted", ["a1"])
        act = _act("a1", 0, SEG_HIT)
        self.assertEqual(scan_success([ep], [act]), [])

    def test_success_still_included(self):
        ep = _ep("success", ["a1"])
        act = _act("a1", 0, SEG_HIT)
        rows = scan_success([ep], [act])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rule"], "stored_nonempty")


class TestBatchAttribution(unittest.TestCase):
    """P1-2：一条 bash 链的合并输出不能冒充每个子命令各自的证据。"""

    def _three_store_call(self, verdict="success"):
        shared = "  ".join([SEG_EMPTY, SEG_HIT, SEG_EMPTY]) + " 尾部杂散"
        acts = [_act("a0", 0, shared), _act("a1", 1, shared), _act("a2", 2, shared)]
        return [_ep(verdict, ["a0", "a1", "a2"])], acts

    def test_segment_attribution_picks_own_object(self):
        eps, acts = self._three_store_call()
        rows = scan_success(eps, acts)
        self.assertEqual([r["action"] for r in rows], ["a1"])
        self.assertEqual(rows[0]["out"], SEG_HIT)
        self.assertEqual(rows[0]["attribution"], "segment")

    def test_unsegmentable_batch_parked(self):
        shared = SEG_HIT          # 2 个子动作只有 1 段 JSON：对不了号
        acts = [_act("a0", 0, shared), _act("a1", 1, shared)]
        stats = {}
        rows = scan_success([_ep("success", ["a0", "a1"])], acts, stats=stats)
        self.assertEqual(rows, [])
        self.assertEqual(stats.get("parked_success_rows"), 2)

    def test_same_action_multi_episode_dedupes(self):
        shared = SEG_HIT
        act = _act("a1", 0, shared)
        eps = [_ep("success", ["a1"], turn=1), _ep("success", ["a1"], turn=2)]
        rows = scan_success(eps, [act])
        self.assertEqual(len(rows), 1)            # 同一动作=同一份证据，只记一次
        self.assertEqual(rows[0]["attribution"], "single")


class TestFeedbackStrictBool(unittest.TestCase):
    """P2：executed 必须严格布尔——字符串 "false"/1 不算执行过。"""

    def test_string_false_rejected(self):
        with self.assertRaises(ValueError):
            parse_feedback({"name": "x", "kind": "strong", "verdict": "success",
                            "executed": "false"})

    def test_int_one_rejected(self):
        with self.assertRaises(ValueError):
            parse_feedback({"name": "x", "kind": "strong", "verdict": "success",
                            "executed": 1})

    def test_true_accepted(self):
        fb = parse_feedback({"name": "x", "kind": "strong", "verdict": "success",
                             "executed": True})
        self.assertEqual(fb["verdict"], "success")


class TestTransitionActorAllowlist(unittest.TestCase):
    """P2：升级确认主体走白名单，自由文本不算外部确认。"""

    def test_free_text_actor_rejected(self):
        with self.assertRaises(StateError):
            transition(dict(FRONT, test_count=1), "observed", actor="robot")

    def test_machine_still_rejected(self):
        with self.assertRaises(StateError):
            transition(dict(FRONT, test_count=1), "observed", actor="machine")

    def test_owner_allowed(self):
        f = transition(dict(FRONT, test_count=1), "observed", actor="owner")
        self.assertEqual(f["state"], "observed")


def _mk_card(sdir, name="stardew-a", tool_key="stardew.store"):
    os.makedirs(os.path.join(sdir, name), exist_ok=True)
    save_skill(os.path.join(sdir, name, "SKILL.md"),
               dict(FRONT, name=name, tool_key=tool_key), BODY)


class TestRecordFeedbackGuards(unittest.TestCase):
    """P2：卡名白名单防路径穿越；空展示上下文必须显式二选一。"""

    def setUp(self):
        self.sdir = tempfile.mkdtemp()
        os.environ["EXPMEM_SKILLS_DIR"] = self.sdir
        _mk_card(self.sdir)

    def tearDown(self):
        os.environ.pop("EXPMEM_SKILLS_DIR", None)

    def test_traversal_name_rejected(self):
        from expmem import api
        r = api.record_skill_feedback({"name": "../evil", "kind": "weak_negative"},
                                      shown_ids=("stardew-a",))
        self.assertFalse(r["applied"])
        self.assertFalse(os.path.exists(os.path.join(self.sdir, "..", "evil")))

    def test_empty_shown_rejected_without_manual_review(self):
        from expmem import api
        r = api.record_skill_feedback({"name": "stardew-a", "kind": "weak_negative"})
        self.assertFalse(r["applied"])
        self.assertIn("no_shown_context", r["reason"])

    def test_manual_review_allows_empty_shown(self):
        from expmem import api
        r = api.record_skill_feedback({"name": "stardew-a", "kind": "weak_negative"},
                                      manual_review=True)
        self.assertTrue(r["applied"])


class TestAtomicSave(unittest.TestCase):
    """P1-4：写盘中途失败不留半截卡，临时文件清理。"""

    def test_fsync_failure_keeps_old_card(self):
        import expmem.skills as sk
        d = tempfile.mkdtemp()
        p = os.path.join(d, "SKILL.md")
        save_skill(p, FRONT, BODY)
        old = open(p, encoding="utf-8").read()
        real = os.fsync

        def boom(fd):
            raise OSError("disk gone")

        os.fsync = boom
        try:
            with self.assertRaises(OSError):
                save_skill(p, dict(FRONT, version=2), BODY + "x")
        finally:
            os.fsync = real
        self.assertEqual(open(p, encoding="utf-8").read(), old)
        self.assertEqual([f for f in os.listdir(d) if f.endswith(".tmp")], [])


class TestConcurrentSettle(unittest.TestCase):
    """P1-4：两进程并发 settle 同一回合——只结一次、计数不翻倍。"""

    def test_two_processes_settle_once(self):
        from expmem import api
        sdir = tempfile.mkdtemp()
        _mk_card(sdir)
        eps = [_ep("success", ["a1"], session="s1", turn=5)]
        acts = [_act("a1", 0, '{"ok": true}', call_id="c9", session="s1")]
        mf_path = os.path.join(sdir, "mf.json")
        json.dump({"s1|5": ["stardew-a"]}, open(mf_path, "w"))
        eps_path = os.path.join(sdir, "eps.json")
        acts_path = os.path.join(sdir, "acts.json")
        json.dump(eps, open(eps_path, "w"))
        json.dump(acts, open(acts_path, "w"))
        script = (
            "import sys, json; sys.path.insert(0, %r);"
            "from expmem import api;"
            "eps=json.load(open(%r)); acts=json.load(open(%r));"
            "mf={(k.split('|')[0], int(k.split('|')[1])): v for k, v in json.load(open(%r)).items()};"
            "print(json.dumps(api.settle_auto_marks(eps, acts, skills_dir=%r,"
            " extra_manifest=mf)))"
        ) % (REPO, eps_path, acts_path, mf_path, sdir)
        procs = [subprocess.Popen([sys.executable, "-c", script],
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE) for _ in range(2)]
        outs = []
        for p in procs:
            o, e = p.communicate(timeout=60)
            if p.returncode != 0:
                self.fail("settle 子进程失败: %s" % e.decode("utf-8", "replace"))
            outs.append(json.loads(o.decode("utf-8")))
        total_marked = sum(sum(r.get("marked", {}).values()) for r in outs)
        self.assertEqual(total_marked, 1)      # 卡计数只 +1
        settled_path = os.path.join(sdir, ".settled.jsonl")
        rows = [json.loads(ln) for ln in open(settled_path, encoding="utf-8")
                if ln.strip()]
        mine = [r for r in rows if r.get("session") == "s1" and r.get("turn") == 5]
        self.assertEqual(len(mine), 1)         # 台账只记一次


if __name__ == "__main__":
    unittest.main()
