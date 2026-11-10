# -*- coding: utf-8 -*-
"""剧集切分：按文件顺序切、result 回填、interrupted 四值、归属闸。"""
import unittest

from expmem.episode import build_session
from expmem.sources.session_log import classify

OWNER = "onebot-private-1792612052-test"
AUTO = "dsh-automation-session-test"


def _call(seq, t, cid, cmd, turn=1, step=1):
    import json
    return {"type": "tool/call", "seq": seq, "time": t,
            "data": {"turn": turn, "step": step, "callId": cid, "name": "bash",
                     "arguments": json.dumps({"command": cmd})}}


def _result(seq, t, cid, is_error=False):
    return {"type": "tool/result", "seq": seq, "time": t,
            "data": {"turn": 1, "step": 1,
                     "message": {"toolCallId": cid, "isError": is_error,
                                 "content": [{"type": "text", "text": "ok"}]},
                     "meta": {"cwd": "/home/agent/qq-workspace"}}}


class TestOwnerSession(unittest.TestCase):
    def _events(self, extra_end=None):
        ev = [
            {"type": "turn/start", "seq": 1, "time": 1000, "data": {"turn": 1}},
            _call(2, 1005, "c1", "sd warp Beach 44 35; sleep 40; sd state"),
            _result(3, 3005, "c1"),
            {"type": "turn/end", "seq": 4, "time": 4000,
             "data": {"turn": 1, **(extra_end or {})}},
        ]
        return ev

    def test_split_and_backfill(self):
        actions, episodes, st = build_session(self._events(), OWNER)
        self.assertEqual([a.verb for a in actions], ["warp", "state"])
        self.assertEqual(actions[0].endpoint, "POST")
        self.assertEqual(actions[1].endpoint, "GET /state")
        for a in actions:
            self.assertEqual(a.latency_ms, 2000)
            self.assertFalse(a.is_error)
        self.assertEqual(len(episodes), 1)
        self.assertEqual(len(episodes[0]["actions"]), 2)
        self.assertEqual(episodes[0]["verdict"], "unknown")
        self.assertEqual(st["game_turns"], 1)

    def test_turn_number_reuse_does_not_merge(self):
        # 回合号重开会撞号：两次 turn 1 按文件顺序必须切成两个剧集
        ev = [
            {"type": "turn/start", "seq": 1, "time": 1000, "data": {"turn": 1}},
            _call(2, 1005, "c1", "sd state"), _result(3, 1100, "c1"),
            {"type": "turn/end", "seq": 4, "time": 1200, "data": {"turn": 1}},
            {"type": "turn/start", "seq": 5, "time": 2000, "data": {"turn": 1}},
            _call(6, 2005, "c2", "sd inv"), _result(7, 2100, "c2"),
            {"type": "turn/end", "seq": 8, "time": 2200, "data": {"turn": 1}},
        ]
        actions, episodes, st = build_session(ev, OWNER)
        self.assertEqual(len(episodes), 2)
        self.assertNotEqual(episodes[0]["episode_id"], episodes[1]["episode_id"])
        self.assertEqual(st["game_turns"], 2)

    def test_unknown_verb_maps_to_other_with_act_endpoint(self):
        # cmb-D1：未知 verb 落 other:<token>，endpoint=ACT <token>，不丢
        ev = [
            {"type": "turn/start", "seq": 1, "time": 1000, "data": {"turn": 1}},
            _call(2, 1005, "c1", "sd face right; sd state"),
            _result(3, 1100, "c1"),
            {"type": "turn/end", "seq": 4, "time": 1200, "data": {"turn": 1}},
        ]
        actions, episodes, st = build_session(ev, OWNER)
        self.assertEqual([a.verb for a in actions], ["other:face", "state"])
        self.assertEqual(actions[0].endpoint, "ACT face")
        self.assertEqual(st["sd_other_actions"], 1)
        self.assertEqual(st["game_turns"], 1)  # other:face 也算游戏动作

    def test_gap_split_long_wait(self):
        # cmb-G2：同一回合内相邻动作间隔 > 600s 必须切成两个剧集
        ev = [
            {"type": "turn/start", "seq": 1, "time": 1000, "data": {"turn": 1}},
            _call(2, 1000, "c1", "sd warp Beach 44 35"), _result(3, 1100, "c1"),
            _call(4, 1000 + 700_000, "c2", "sd state"), _result(5, 1000 + 700_100, "c2"),
            {"type": "turn/end", "seq": 6, "time": 1000 + 700_200, "data": {"turn": 1}},
        ]
        actions, episodes, st = build_session(ev, OWNER)
        self.assertEqual(len(episodes), 2)
        self.assertEqual(st["gap_splits"], 1)
        self.assertNotEqual(episodes[0]["episode_id"], episodes[1]["episode_id"])

    def test_poll_repeat_flagged(self):
        # cmb-G2：同 verb 同命令段 ≤30s 标 poll_repeat，保留但可从 calls-to-goal 剔除
        ev = [
            {"type": "turn/start", "seq": 1, "time": 1000, "data": {"turn": 1}},
            _call(2, 1000, "c1", "sd state"),
            _call(4, 1000 + 20_000, "c2", "sd state"),
            _result(3, 1050, "c1"), _result(5, 1000 + 20_050, "c2"),
            {"type": "turn/end", "seq": 6, "time": 3000, "data": {"turn": 1}},
        ]
        actions, episodes, st = build_session(ev, OWNER)
        self.assertEqual([a.poll_repeat for a in actions], [False, True])
        self.assertEqual(episodes[0]["act_count"], 2)
        self.assertEqual(episodes[0]["act_count_poll_free"], 1)

    def test_game_day_filled_from_summary(self):
        # cmb-G1 实测样式：一行摘要 `Beach(44,35) 冬25 winter 17:40 | …` → 冬25
        ev = [
            {"type": "turn/start", "seq": 1, "time": 1000, "data": {"turn": 1}},
            _call(2, 1005, "c1", "sd state"), _result(3, 1100, "c1"),
            {"type": "turn/end", "seq": 4, "time": 1200, "data": {"turn": 1}},
        ]
        ev[2] = dict(ev[2])
        ev[2]["data"]["message"]["content"] = [
            {"type": "text", "text":
             "Beach(44,35) 冬25 winter 17:40 | 体力102/270 HP100 | 钱142538 | 手:Iridium Rod | 包33/36"}]
        actions, episodes, st = build_session(ev, OWNER)
        self.assertEqual(episodes[0]["game_day"], "冬25")

    def test_game_day_from_json_fallback(self):
        # JSON 兜底：time.season + time.dayOfMonth
        ev = [
            {"type": "turn/start", "seq": 1, "time": 1000, "data": {"turn": 1}},
            _call(2, 1005, "c1", "sd raw /state"), _result(3, 1100, "c1"),
            {"type": "turn/end", "seq": 4, "time": 1200, "data": {"turn": 1}},
        ]
        ev[2] = dict(ev[2])
        ev[2]["data"]["message"]["content"] = [
            {"type": "text", "text": '{"time":{"season":"fall","dayOfMonth":25}}'}]
        actions, episodes, st = build_session(ev, OWNER)
        self.assertEqual(episodes[0]["game_day"], "秋25")

    def test_game_day_short_format_no_english_suffix(self):
        # cmb-D：49% 的 state 读是短格式「Mountain(42,11) 春16 14:20」——
        # 旧正则必选英文季后缀，把它们全漏了（天花板 0.9% 是被正则低估的假象）
        ev = [
            {"type": "turn/start", "seq": 1, "time": 1000, "data": {"turn": 1}},
            _call(2, 1005, "c1", "sd state"), _result(3, 1100, "c1"),
            {"type": "turn/end", "seq": 4, "time": 1200, "data": {"turn": 1}},
        ]
        ev[2] = dict(ev[2])
        ev[2]["data"]["message"]["content"] = [
            {"type": "text", "text": "Mountain(42,11) 春16 14:20 | 体力88/270"}]
        actions, episodes, st = build_session(ev, OWNER)
        self.assertEqual(episodes[0]["game_day"], "春16")

    def test_game_day_multi_day_window_dropped(self):
        # cmb-D 既定规则：一集窗口出现两个不同游戏日（跨日回合）→ 宁空勿猜
        ev = [
            {"type": "turn/start", "seq": 1, "time": 1000, "data": {"turn": 1}},
            _call(2, 1005, "c1", "sd state; sleep 1; sd state"), _result(3, 1100, "c1"),
            {"type": "turn/end", "seq": 4, "time": 1200, "data": {"turn": 1}},
        ]
        ev[2] = dict(ev[2])
        ev[2]["data"]["message"]["content"] = [
            {"type": "text", "text": "Farm(64,15) 春16 06:00\nFarm(64,15) 春17 06:10"}]
        actions, episodes, st = build_session(ev, OWNER)
        self.assertEqual(episodes[0]["game_day"], "")

    def test_game_day_missing_when_no_date_fields(self):
        ev = [
            {"type": "turn/start", "seq": 1, "time": 1000, "data": {"turn": 1}},
            _call(2, 1005, "c1", "sd warp Beach 44 35"), _result(3, 1100, "c1"),
            {"type": "turn/end", "seq": 4, "time": 1200, "data": {"turn": 1}},
        ]
        actions, episodes, st = build_session(ev, OWNER)
        self.assertEqual(episodes[0]["game_day"], "")

    def test_subshell_raw_becomes_action(self):
        # 11-11 实跑教训：$(sd raw /store …) 子 shell 调用是真动作，必须进 actions，
        # 否则 stored:[] 的归因会被链首 warp 抢走
        ev = [
            {"type": "turn/start", "seq": 1, "time": 1000, "data": {"turn": 1}},
            _call(2, 1005, "c1", "x=$(sd raw /menu_close); sd state"),
            _result(3, 1100, "c1"),
            {"type": "turn/end", "seq": 4, "time": 1200, "data": {"turn": 1}},
        ]
        actions, episodes, st = build_session(ev, OWNER)
        # P2-10 后 `(` 也是锚点：verb 扫描先发（子 shell 在串里本就靠前），
        # 且 raw_path 直接取到；锚定 raw 尺按 path 去重不再重复发
        self.assertEqual([(a.verb, a.raw_path) for a in actions],
                         [("raw", "/menu_close"), ("state", "")])
        self.assertEqual(actions[0].endpoint, "RAW /menu_close")
        self.assertEqual(st["sd_verb_actions"], 2)   # 两把尺重叠后归 verb 尺
        self.assertEqual(st["sd_raw_actions"], 0)    # 「只剩锚定尺抓到」的语义如实归零

    def test_orphan_result_tolerated_and_counted(self):
        ev = [
            {"type": "turn/start", "seq": 1, "time": 1000, "data": {"turn": 1}},
            _result(9, 1050, "ghost_id"),   # 没有对应 call 的孤儿 result
            _call(2, 1005, "c1", "sd state"), _result(3, 1100, "c1"),
            {"type": "turn/end", "seq": 4, "time": 1200, "data": {"turn": 1}},
        ]
        actions, episodes, st = build_session(ev, OWNER)
        self.assertEqual(st["orphan_results"], 1)
        self.assertEqual(len(episodes), 1)  # 不崩、不重复

    def test_interrupted_not_unknown(self):
        actions, episodes, st = build_session(
            self._events(extra_end={"reason": {"kind": "interrupted"}}), OWNER)
        self.assertEqual(episodes[0]["verdict"], "interrupted")


class TestOwnershipGate(unittest.TestCase):
    def test_automation_session_writes_nothing(self):
        ev = [
            {"type": "turn/start", "seq": 1, "time": 1000, "data": {"turn": 1}},
            _call(2, 1005, "c1", "sd state && sd warp Farm 64 15"),
            _result(3, 1100, "c1"),
            {"type": "turn/end", "seq": 4, "time": 1200, "data": {"turn": 1}},
        ]
        actions, episodes, st = build_session(ev, AUTO)
        self.assertEqual(actions, [])
        self.assertEqual(episodes, [])
        self.assertEqual(st["tool_calls"], 1)          # 计数照记（守门有信号）
        self.assertEqual(st["sd_in_automation"], 2)    # 但不进产物
        self.assertEqual(classify(AUTO), "automation")


if __name__ == "__main__":
    unittest.main()
