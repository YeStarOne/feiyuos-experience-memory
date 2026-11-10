# -*- coding: utf-8 -*-
"""S1 outcome 规则测试：夹具全部取自真实踩过的坑（方案 §1.5 / §5-1）。

必须判 failure 的三类假成功：
  /store 返 stored:[]（§84）、ready=34 而收菜 0 件（day-loop 空转）、
  /buy 不带 price 按售价×2 扣钱（shared-memory 2026-11-11 记账）。
外加两条守卫：探针不可用≠失败（cmb-B）、interrupted 不判成败（cmb-C）。"""
import unittest

from expmem.models import Action
from expmem.outcome import classify_episode


def act(aid, verb="", raw_path="", ts=1000, out=""):
    return Action(id=aid, ts=ts, session="s", turn=1, step=1, tool="bash",
                  adapter="stardew_sd", call_id="c_" + aid, sub_idx=0,
                  verb=verb, raw_path=raw_path, out_head=out)


class TestNoop(unittest.TestCase):
    def test_store_stored_empty(self):
        ep = {"verdict": "unknown"}
        classify_episode(ep, [act("a1", verb="state", out="钱 1000 g"),
                              act("a2", raw_path="/store", out='{"stored":[],"ok":true}')])
        self.assertEqual(ep["verdict"], "failure")
        self.assertEqual(ep["failure_class"], "noop")
        self.assertEqual(ep["outcome"]["rule"], "stored_empty")

    def test_harvest_zero_with_ready(self):
        ep = {"verdict": "unknown"}
        classify_episode(ep, [
            act("p1", raw_path="/farm_report", out='{"ready":34,"crops":32}'),
            act("a1", raw_path="/interact", out="收菜完成0件")])
        self.assertEqual(ep["verdict"], "failure")
        self.assertEqual(ep["failure_class"], "noop")
        self.assertEqual(ep["outcome"]["rule"], "harvest_zero_with_ready")
        self.assertEqual(ep["outcome"]["ready"], 34)

    def test_harvest_zero_without_ready_is_not_failure(self):
        # ready=0 而收 0 件 = 本来就没活，不是她的失败
        ep = {"verdict": "unknown"}
        classify_episode(ep, [
            act("p1", raw_path="/farm_report", out='{"ready":0}'),
            act("a1", raw_path="/interact", out="收菜完成0件")])
        self.assertEqual(ep["verdict"], "unknown")

    def test_ten_items_not_zero(self):
        # "10 件" 不能被 (?<!\d)0 误判成 0 件
        ep = {"verdict": "unknown"}
        classify_episode(ep, [
            act("p1", raw_path="/farm_report", out='{"ready":34}'),
            act("a1", raw_path="/interact", out="收菜完成10件")])
        self.assertEqual(ep["verdict"], "unknown")


class TestBlocked(unittest.TestCase):
    def test_connection_refused(self):
        ep = {"verdict": "unknown"}
        classify_episode(ep, [act("a1", verb="warp", out="curl: (7) Connection refused")])
        self.assertEqual(ep["verdict"], "failure")
        self.assertEqual(ep["failure_class"], "blocked")

    def test_money_with_thousands_separator_not_blocked(self):
        # "100,000 g" 里的 000 不许触发 blocked（旧版裸 000 规则的教训）
        ep = {"verdict": "unknown"}
        classify_episode(ep, [act("p1", verb="state", out="钱 100,000 g")])
        self.assertEqual(ep["verdict"], "unknown")
        self.assertEqual(ep["probe_status"], "ok")


class TestWrongArg(unittest.TestCase):
    def test_buy_double_charge(self):
        ep = {"verdict": "unknown"}
        classify_episode(ep, [
            act("p0", verb="state", ts=1000, out="钱 1,000 g"),
            act("b1", raw_path="/buy", ts=2000, out='{"charge":300,"ok":true}'),
            act("p1", verb="state", ts=3000, out="钱 400 g")])
        self.assertEqual(ep["verdict"], "failure")
        self.assertEqual(ep["failure_class"], "wrong_arg")
        self.assertEqual(ep["outcome"]["rule"], "buy_double_charge")
        self.assertEqual(ep["outcome"]["delta"], 600)
        self.assertEqual(ep["outcome"]["charge"], 300)

    def test_buy_correct_charge_not_failure(self):
        ep = {"verdict": "unknown"}
        classify_episode(ep, [
            act("p0", verb="state", ts=1000, out="钱 1,000 g"),
            act("b1", raw_path="/buy", ts=2000, out='{"charge":300}'),
            act("p1", verb="state", ts=3000, out="钱 700 g")])
        self.assertEqual(ep["verdict"], "unknown")

    def test_buy_missing_evidence_stays_unknown(self):
        # 缺钱差证据就放弃——宁 unknown 不错判
        ep = {"verdict": "unknown"}
        classify_episode(ep, [act("b1", raw_path="/buy", out='{"charge":300}')])
        self.assertEqual(ep["verdict"], "unknown")


class TestAttribution(unittest.TestCase):
    def test_store_attribution_not_stolen_by_chain_head(self):
        # 11-11 实跑教训：同一 bash 链 `sd warp …; sd raw /store …` 的所有动作共享
        # 同一段返回文本，stored:[] 的归因曾被链首 warp 抢走 → 必须归给 /store
        import json as J
        from expmem.episode import build_session
        ev = [
            {"type": "turn/start", "seq": 1, "time": 1000, "data": {"turn": 1}},
            {"type": "tool/call", "seq": 2, "time": 1005, "data":
             {"turn": 1, "step": 1, "callId": "c1", "name": "bash",
              "arguments": J.dumps({"command": "sd warp Beach 44 35; sd raw /store {\"id\":\"Cranberries\"}; sd state"})}},
            {"type": "tool/result", "seq": 3, "time": 1100, "data":
             {"turn": 1, "step": 1, "message": {"toolCallId": "c1", "isError": False,
              "content": [{"type": "text", "text": "warp ok {\"ok\": true, \"stored\": []} Beach(44,35) 冬25 winter"}]},
              "meta": {"cwd": "/home/agent/qq-workspace"}}},
            {"type": "turn/end", "seq": 4, "time": 1200, "data": {"turn": 1}},
        ]
        acts = "onebot-private-1792612052-test"
        actions, episodes, st = build_session(ev, acts)
        self.assertEqual([a.verb for a in actions], ["warp", "raw", "state"])
        ep = episodes[0]
        self.assertEqual(ep["verdict"], "failure")
        self.assertEqual(ep["failure_class"], "noop")
        blamed = next(a for a in actions if a.id == ep["outcome"]["action"])
        self.assertEqual(blamed.raw_path, "/store")  # 不是 warp
        self.assertEqual(ep["outcome"]["attribution"], "segment")


class TestGuards(unittest.TestCase):
    def test_no_probe_unavailable(self):
        ep = {"verdict": "unknown"}
        classify_episode(ep, [act("a1", verb="warp", out="ok")])
        self.assertEqual(ep["probe_status"], "unavailable")
        self.assertEqual(ep["verdict"], "unknown")

    def test_probe_200_but_dead_is_unavailable_not_noop(self):
        # /state 200 但字段整片 000 = 探针不可用（11-10 客户端卡死实录），不许记成她的失败
        ep = {"verdict": "unknown"}
        classify_episode(ep, [
            act("p1", raw_path="/state", out="farm_report:000 crop_seasons:000 debris:000"),
            act("a1", raw_path="/interact", out="收菜完成0件")])
        self.assertEqual(ep["probe_status"], "unavailable")
        self.assertEqual(ep["verdict"], "unknown")

    def test_interrupted_untouched(self):
        ep = {"verdict": "interrupted"}
        classify_episode(ep, [act("a1", verb="state", out="curl: (7) Connection refused")])
        self.assertEqual(ep["verdict"], "interrupted")
        self.assertEqual(ep["probe_status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
