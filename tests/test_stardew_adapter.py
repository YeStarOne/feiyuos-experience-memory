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

    def test_subshell_not_verb_anchored(self):
        # 段首是 x=$(…)，verb 正则不认 —— 但 raw 路径那把尺子必须认
        cmd = "x=$(sd raw /menu_close); sd warp Beach 44 35"
        self.assertEqual([v for v, _ in sd.verb_calls(cmd)], ["warp"])
        self.assertEqual(sd.raw_paths(cmd), ["/menu_close"])

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
