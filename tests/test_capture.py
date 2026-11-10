# -*- coding: utf-8 -*-
"""采集层修定（S3 前）：后台化 bash 的终态回填 + JSON /state 保 time 段。

真日志实测口径（11-11，主会话 30MB）：309 个后台 job / 300 个被她用 job_output
捞回终态；state 类读 1409 条里 165 条被后台化（输出只剩通知文本），另有 5 条
JSON 读的 game_day 落在 160 字头之后。
"""
import json
import unittest

from expmem.episode import build_session

OWNER = "onebot-private-1792612052-test"
NOTICE = ("[still running after 60000ms; moved to background job bash-%s]\n"
          "The command keeps running in the background. You will be notified when it "
          "finishes; read newer output with job_output, stop it with job_kill.")


def _call(seq, t, cid, cmd, turn=1):
    return {"type": "tool/call", "seq": seq, "time": t,
            "data": {"turn": turn, "step": 1, "callId": cid, "name": "bash",
                     "arguments": json.dumps({"command": cmd})}}


def _jo_call(seq, t, cid, jid):
    return {"type": "tool/call", "seq": seq, "time": t,
            "data": {"turn": 1, "step": 2, "callId": cid, "name": "job_output",
                     "arguments": json.dumps({"job_id": jid, "wait": True})}}


def _result(seq, t, cid, text, is_error=False):
    return {"type": "tool/result", "seq": seq, "time": t,
            "data": {"turn": 1, "step": 1,
                     "message": {"toolCallId": cid, "isError": is_error,
                                 "content": [{"type": "text", "text": text}]}}}


def _registered(seq, t, jid, cid):
    return {"type": "job/registered", "seq": seq, "time": t,
            "data": {"jobId": jid, "kind": "bash", "toolCall": {"callId": cid, "rootCallId": cid}}}


def _settled(seq, t, jid, status="completed", fin=None):
    d = {"jobId": jid, "status": status, "detail": "exit code: 0"}
    if fin is not None:
        d["finishedAt"] = fin
    return {"type": "job/settled", "seq": seq, "time": t, "data": d}


def _turn(seq, t, turn=1, kind=None):
    if kind is None:
        return {"type": "turn/start", "seq": seq, "time": t, "data": {"turn": turn}}
    return {"type": "turn/end", "seq": seq, "time": t,
            "data": {"turn": turn, "reason": {"kind": kind}}}


STATE_JSON = json.dumps({"ok": True, "blob": "x" * 300,
                         "state": {"season": "winter", "dayOfMonth": 25,
                                   "time": 1740, "gold": 142538}})


class TestJobOutputCapture(unittest.TestCase):
    def test_backgrounded_state_read_recovered_from_job_output(self):
        jid = "bash-4a04ca95724c446d"
        ev = [
            _turn(1, 1000),
            _call(2, 1005, "c1", "sd raw /state"),
            _registered(3, 1006, jid, "c1"),
            _result(4, 61005, "c1", NOTICE % "4a04ca95724c446d"),
            _turn(5, 61100, kind="completed"),
            # 下一回合她才把终态捞回来
            _turn(6, 62000),
            _jo_call(7, 62005, "j1", jid),
            _result(8, 62010, "j1", STATE_JSON + "\n[status: completed, exit code: 0]"),
            _turn(9, 62100, kind="completed"),
        ]
        actions, episodes, st = build_session(ev, OWNER)
        a = [x for x in actions if x.verb == "raw"][0]
        self.assertEqual(a.job_id, jid)
        self.assertEqual(a.job_status, "completed")
        self.assertEqual(a.job_exit, 0)
        self.assertEqual(a.out_source, "job_output")
        self.assertIn('"dayOfMonth":25', a.out_head)
        # 通知文本不许冒充输出：终态回来前那条剧集不能算「探针可用」
        self.assertEqual(st["job_backgrounded"], 1)
        self.assertEqual(st["job_recovered"], 1)
        # 回填后重判：终态文本回来了，但它属于 job 完成的那个时刻——
        # 本夹具没给 finishedAt（拿不到=不可采信，cmb 时间判据），日不填
        self.assertEqual(episodes[0]["game_day"], "")

    def test_unrecovered_notice_is_not_a_usable_probe(self):
        jid = "bash-deadbeef00000001"
        ev = [
            _turn(1, 1000),
            _call(2, 1005, "c1", "sd raw /state; sd inv"),
            _registered(3, 1006, jid, "c1"),
            _result(4, 61005, "c1", NOTICE % "deadbeef00000001"),
            _turn(5, 61100, kind="completed"),
        ]
        actions, episodes, st = build_session(ev, OWNER)
        for a in actions:
            self.assertEqual(a.out_head, "")     # 通知文本不是输出
            self.assertEqual(a.out_sha, "")
            self.assertEqual(a.job_id, jid)
        self.assertEqual(episodes[0]["probe_status"], "unavailable")
        self.assertEqual(episodes[0]["verdict"], "unknown")
        self.assertEqual(st["job_recovered"], 0)
        self.assertEqual(st["job_pending"], len(actions))

    def test_killed_job_not_backfilled(self):
        jid = "bash-c32d3a728b004b58"
        ev = [
            _turn(1, 1000),
            _call(2, 1005, "c1", "sd raw /store"),
            _registered(3, 1006, jid, "c1"),
            _result(4, 61005, "c1", NOTICE % "c32d3a728b004b58"),
            _turn(5, 61100, kind="completed"),
            _jo_call(6, 62005, "j1", jid),
            _result(7, 62010, "j1", '{"ok": true, "stored": []}\n[status: killed, exit code: -1]'),
        ]
        actions, _, st = build_session(ev, OWNER)
        a = [x for x in actions if x.verb == "raw"][0]
        self.assertEqual(a.job_status, "killed")
        self.assertEqual(a.out_head, "")          # 半截输出不当证据
        self.assertEqual(a.out_source, "")
        self.assertEqual(st["job_killed"], 1)
        self.assertEqual(st["job_recovered"], 0)

    def test_notice_fallback_when_no_job_registered(self):
        # job/registered 缺失（旧日志）也要能从通知文本里抓到 job id
        jid = "bash-ffffffffffffffff"
        ev = [
            _turn(1, 1000), _call(2, 1005, "c1", "sd state"),
            _result(3, 61005, "c1", NOTICE % "ffffffffffffffff"),
            _turn(4, 61100, kind="completed"),
            _jo_call(5, 62005, "j1", jid),
            _result(6, 62010, "j1", "Beach(44,35) 冬25 winter 17:40 | 体力102/270\n"
                                   "[status: completed, exit code: 0]"),
        ]
        actions, episodes, st = build_session(ev, OWNER)
        self.assertEqual(st["job_recovered"], 1)
        self.assertIn("冬25 winter", actions[0].out_head)
        # cmb P2：job_output 的**文本**日不进 game_day 提取（可能来自窗口外的另一
        # 天，实锤=春16窗口捞到春17早晨的迟到 state）——宁空勿猜；JSON 摘要兜底
        # 不受限（那是命令自身执行时的结构化状态）
        self.assertEqual(episodes[0]["game_day"], "")


class TestJobTimeRule(unittest.TestCase):
    """cmb finishedAt 实锤（11-11）：job 文本采信拿事件时间判——
    窗口内完成=窗口内证据（debca 形）；窗口内完成的 job 引入第二日=真跨日
    窗口→歧义（01ef 形，防「用回填把跨日窗口填成假单日」）；窗口外完成→不采信。"""

    def _run(self, fin, job_text):
        jid = "bash-ab12cd34ef567788"
        ev = [
            _turn(1, 1000),
            _call(2, 1005, "c1", "sd raw /state"),
            _registered(3, 1006, jid, "c1"),
            _result(4, 61005, "c1", NOTICE % "ab12cd34ef567788"),
            # 同回合再来一条**直读**（不经 job），窗口内本地的唯一日证据
            _call(5, 61010, "c2", "sd state"),
            _result(6, 61060, "c2", "Farm(52,16) 冬25 19:40 | 体力100/270"),
            _turn(7, 61100, kind="completed"),
            _turn(8, 62000),
            _jo_call(9, 62005, "j1", jid),
            _result(10, 62010, "j1", job_text),
            _settled(11, 62011, jid, "completed", fin=fin),
            _turn(12, 62100, kind="completed"),
        ]
        return build_session(ev, OWNER)

    def test_in_window_job_sole_day_fills(self):
        # debca 形：job 在窗口内完成（51000 ∈ [1000,61100]）→ 唯一日采信
        jtext = "Farm(52,16) 冬25 20:10 | 体力99/270\n[status: completed, exit code: 0]"
        _, episodes, st = self._run(51000, jtext)
        self.assertEqual(episodes[0]["game_day"], "冬25")
        self.assertEqual(st["game_day_ambiguous"], 0)

    def test_in_window_job_second_day_is_ambiguous(self):
        # 01ef 形：窗口内完成的 job 带来第二日（春17）→ 真跨日窗口，宁空勿猜，
        # 不许用回填把跨日窗口填成假单日（按来源一刀切就会填成 冬25=错）
        jtext = "Farm(52,16) 春17 07:40 | 体力220/270\n[status: completed, exit code: 0]"
        _, episodes, st = self._run(51000, jtext)
        self.assertEqual(episodes[0]["game_day"], "")
        self.assertEqual(st["game_day_ambiguous"], 1)

    def test_out_of_window_job_not_counted(self):
        # 窗口外完成（1794059131151 >> 61100）→ 不采信，窗口本地日照常用
        jtext = "Farm(52,16) 春17 07:40 | 体力220/270\n[status: completed, exit code: 0]"
        _, episodes, st = self._run(1794059131151, jtext)
        self.assertEqual(episodes[0]["game_day"], "冬25")
        self.assertEqual(st["game_day_ambiguous"], 0)

    def test_recovered_text_without_finished_at_not_counted(self):
        # 拿不到 finishedAt 的回填：不可采信（宁空勿猜的字面执行）
        jtext = "Farm(52,16) 春17 07:40 | 体力220/270\n[status: completed, exit code: 0]"
        _, episodes, _ = self._run(None, jtext)
        self.assertEqual(episodes[0]["game_day"], "冬25")   # 直读的 冬25 仍在


class TestHeadDigest(unittest.TestCase):
    def test_json_day_beyond_160_chars_is_preserved(self):
        # P2-11：season/dayOfMonth 落在 160 字之后，只截头就丢游戏日
        ev = [
            _turn(1, 1000), _call(2, 1005, "c1", "sd raw /state"),
            _result(3, 1100, "c1", STATE_JSON),
            _turn(4, 1200, kind="completed"),
        ]
        actions, episodes, st = build_session(ev, OWNER)
        self.assertNotIn("dayOfMonth", " ".join(STATE_JSON.split())[:160])  # 头里确实没有
        self.assertIn('"dayOfMonth":25', actions[0].out_head)
        self.assertEqual(episodes[0]["game_day"], "冬25")
        self.assertEqual(st["digest_added"], 1)

    def test_plain_text_head_untouched(self):
        one_line = "Beach(44,35) 冬25 winter 17:40 | 体力102/270 | 钱142538"
        ev = [
            _turn(1, 1000), _call(2, 1005, "c1", "sd state"),
            _result(3, 1100, "c1", one_line),
            _turn(4, 1200, kind="completed"),
        ]
        actions, _, st = build_session(ev, OWNER)
        self.assertEqual(actions[0].out_head, one_line)
        self.assertEqual(st["digest_added"], 0)

    def test_prose_with_braces_does_not_fake_a_digest(self):
        prose = "she said {\"ready\": 3} then went home"
        ev = [
            _turn(1, 1000), _call(2, 1005, "c1", "sd state"),
            _result(3, 1100, "c1", prose),
            _turn(4, 1200, kind="completed"),
        ]
        actions, _, _ = build_session(ev, OWNER)
        self.assertNotIn("⟨", actions[0].out_head)
        self.assertEqual(actions[0].out_head, prose)


if __name__ == "__main__":
    unittest.main()
