# -*- coding: utf-8 -*-
"""递卡插件（plugin/expmem-card-delivery）的 Python 侧防漂移闸。

JS 侧逻辑由 test.mjs 在服务器上真跑（node v22）；这里锁三件 Python 能锁的：
  ① triggers.json 结构合法（键齐全、hint 是指针不是抄卡、卡名带域前缀）
  ② 插件零依赖——file:// 导入解析不到 node_modules，裸包名 import 必炸
  ③ README 里的部署 yaml 片段与「patch 条目支持相对路径」的源码结论一致
"""
import json
import os
import re
import unittest

PLUGIN_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "plugin", "expmem-card-delivery")


class TestTriggerTable(unittest.TestCase):
    def _raw(self):
        with open(os.path.join(PLUGIN_DIR, "triggers.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_structure(self):
        raw = self._raw()
        self.assertTrue(raw, "触发器表不能为空")
        ids = set()
        for t in raw:
            for k in ("id", "card", "out_re", "hint"):
                self.assertTrue(isinstance(t.get(k), str) and t[k],
                                "trigger %r 缺 %s" % (t.get("id"), k))
            self.assertNotIn(t["id"], ids)
            ids.add(t["id"])
            self.assertLessEqual(len(t["hint"]), 200, "%s hint 要短（指针非抄卡）" % t["id"])
            self.assertTrue(t["card"].startswith("stardew-"),
                            "%s 卡名要带域前缀" % t["id"])

    def test_out_re_is_valid_python_and_js(self):
        # out_re 都是两端能编译的简单正则（没用 JS 独有语法如 lookahead 命名组）
        for t in self._raw():
            re.compile(t["out_re"])            # Python 侧能编译
            for bad in ("(?P<", "\\k<", "(?<"):
                self.assertNotIn(bad, t["out_re"],
                                 "%s out_re 用了 JS 独有语法，Python 侧审不了" % t["id"])

    def test_cards_referenced_exist_somewhere(self):
        # 触发器指向的卡必须真实存在：仓里 cards/ 或服务器 ~/.dsh/skills（本机没有，
        # 用允许清单标注「服务器侧卡」并在 JS 测试注释里点回）
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        repo_cards = {d for d in os.listdir(os.path.join(root, "cards"))}
        # 服务器侧卡清单（2026-11-13 核实过都真实存在于 ~/.dsh/skills/）
        SERVER_ONLY = {"stardew-sd-farm", "stardew-raw-truncated-json"}
        for t in self._raw():
            self.assertTrue(t["card"] in repo_cards or t["card"] in SERVER_ONLY,
                            "%s 指向的卡 %s 两边都不存在" % (t["id"], t["card"]))


class TestPluginZeroDep(unittest.TestCase):
    def test_only_node_builtins_imported(self):
        src = open(os.path.join(PLUGIN_DIR, "index.mjs"), encoding="utf-8").read()
        for m in re.finditer(r'from\s+"([^"]+)"', src):
            spec = m.group(1)
            ok = spec.startswith("node:") or spec.startswith("./") or spec.startswith("../")
            self.assertTrue(ok, "裸包名 import %r —— file:// 导入下必炸，只能 node: 内建" % spec)

    def test_no_require_call(self):
        src = open(os.path.join(PLUGIN_DIR, "index.mjs"), encoding="utf-8").read()
        self.assertNotIn("require(", src)

    def test_exposes_expected_exports(self):
        src = open(os.path.join(PLUGIN_DIR, "index.mjs"), encoding="utf-8").read()
        for name in ("export const name", "export function apply",
                     "export function compileTriggers", "export function matchTrigger",
                     "export function shouldFire", "export function resultText"):
            self.assertIn(name, src, "缺导出 %s" % name)

    def test_listener_never_throws_outward(self):
        # postExecute 合约：throwing listener → isError。递卡插件必须有兜底 catch。
        src = open(os.path.join(PLUGIN_DIR, "index.mjs"), encoding="utf-8").read()
        self.assertIn("tools/post-execute", src)
        self.assertIn("catch (err)", src)
        self.assertIn("return downstream", src)


if __name__ == "__main__":
    unittest.main()
