# -*- coding: utf-8 -*-
"""adapter 口径测试：链式命令 / 子 shell / heredoc / 白名单 / 端点归一。"""
import unittest

from expmem.adapters import stardew_sd as sd


class TestVerbCalls(unittest.TestCase):
    def test_chained(self):
        cmd = "cd ~/qq-workspace && sd use; sleep 3; ~/qq-workspace/bin/sd state"
        self.assertEqual([v for v, _ in sd.verb_calls(cmd)], ["use", "state"])

    def test_flags_allowed(self):
        self.assertEqual([v for v, _ in sd.verb_calls("sd state --json")], ["state"])

    def test_relative_path_prefix(self):
        cmd = "~/qq-workspace/bin/sd.py warp Beach 44 35"
        self.assertEqual([v for v, _ in sd.verb_calls(cmd)], ["warp"])

    def test_subshell_verb_anchored_now(self):
        # P2-10 反转 11-10 的口径：`(` 也是锚点——(sd state)/$(sd state) 是真调用，
        # 静默丢=白名单同款损失。raw 路径两把尺重叠是有意的（episode 层按 path 去重）
        cmd = "x=$(sd raw /menu_close); (sd state); sd warp A 1 2"
        self.assertEqual([v for v, _ in sd.verb_calls(cmd)], ["raw", "state", "warp"])
        self.assertEqual(sd.raw_paths(cmd), ["/menu_close"])

    def test_shot_endpoint_not_state(self):
        # P2-10：/shot 是截图端点，此前落进「GET /state」误归一
        self.assertEqual(sd.endpoint_for("shot"), "GET /shot")

    def test_heredoc_excluded(self):
        cmd = "cat > f <<'EOF'\nsd state\nEOF\nsd warp A 1 2"
        self.assertEqual([v for v, _ in sd.verb_calls(cmd)], ["warp"])
        self.assertEqual(sd.raw_paths(cmd), [])

    def test_unknown_verb_kept_not_dropped(self):
        # cmb-D1：白名单已废除——sd.py 有通用 cmd_action 分发器，
        # face/select/interact/heal 都是真调用，静默丢=基线缺 18.6%
        toks = [t for t, _ in sd.verb_calls("sd face right; sd interact; sd select_tool X")]
        self.assertEqual(toks, ["face", "interact", "select_tool"])

    def test_quoted_doc_text_not_counted(self):
        # 日记正文里出现 "sd state" 不能算动作（宽松正则 957 的教训）
        cmd = "echo '今天学了 sd state 的用法'"
        self.assertEqual(sd.verb_calls(cmd), [])

    def test_timeout_prefixed_calls_seen(self):
        # ⭐925-928 实锤（11-11）：桥死后她学会给每条 sd 调用加 `timeout N`——
        # 锚点不认 timeout 前缀 ⇒ 她整个「带超时的谨慎调用」时代对采集层隐形
        # （turn 928 二十条调用 0 动作、turn 926 三条 state 探针全丢）
        self.assertEqual([v for v, _ in sd.verb_calls(
            "cd ~/qq-workspace && timeout 60 ./bin/sd state 2>&1|tail -1")],
            ["state"])
        self.assertEqual([v for v, _ in sd.verb_calls(
            "timeout 50 ./bin/sd warp Farm 64 17 2>&1|tail -1; "
            "sleep 5; timeout 50 ./bin/sd near 10")],
            ["warp", "near"])
        self.assertEqual([v for v, _ in sd.verb_calls(
            "nohup ./bin/sd fishbot_enable; nice -n 5 ./bin/sd heal")],
            ["fishbot_enable", "heal"])
        # raw 路径两把尺都照常（RAW_RE 本来就不看前缀）
        self.assertEqual(sd.raw_paths(
            "timeout 50 ./bin/sd raw /store '{\"x\":63}' 2>&1"), ["/store"])
        self.assertEqual(sd.anchored_raw_paths(
            "x=$(timeout 30 ./bin/sd raw /store '{}')"), ["/store"])


class TestEndpoint(unittest.TestCase):
    def test_reads(self):
        self.assertEqual(sd.endpoint_for("state"), "GET /state")
        self.assertEqual(sd.endpoint_for("near"), "GET /state")

    def test_raw(self):
        self.assertEqual(sd.endpoint_for("raw", "/menu_close"), "RAW /menu_close")

    def test_write_defaults_post(self):
        self.assertEqual(sd.endpoint_for("buy"), "POST")
        self.assertEqual(sd.endpoint_for("warp"), "POST")


if __name__ == "__main__":
    unittest.main()
