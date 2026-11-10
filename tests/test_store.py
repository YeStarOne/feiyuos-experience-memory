# -*- coding: utf-8 -*-
"""AppendOnly：幂等（重放两次不重行）+ 重开文件不重复计数。"""
import json
import os
import tempfile
import unittest

from expmem.store import AppendOnly


class TestAppendOnly(unittest.TestCase):
    def test_idempotent(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "actions.jsonl")
        s = AppendOnly(p)
        self.assertTrue(s.append({"id": "a1", "verb": "warp"}))
        self.assertFalse(s.append({"id": "a1", "verb": "warp"}))  # 同 id 拒绝
        self.assertEqual(len(s), 1)
        s.close()

        s2 = AppendOnly(p)  # 重开：从盘上恢复已见集合
        self.assertTrue(s2.append({"id": "a2", "verb": "state"}))
        self.assertFalse(s2.append({"id": "a2", "verb": "state"}))
        self.assertEqual(len(s2), 2)
        s2.close()
        rows = [json.loads(l) for l in open(p, encoding="utf-8")]
        self.assertEqual([r["id"] for r in rows], ["a1", "a2"])

    def test_deterministic_bytes(self):
        # 同输入两次（不同目录）→ 字节一致（S0 验收：md5 相同）
        rows = [{"id": "b%d" % i, "verb": "warp", "turn": 3} for i in range(5)]
        hashes = []
        for _ in range(2):
            d = tempfile.mkdtemp()
            p = os.path.join(d, "x.jsonl")
            s = AppendOnly(p)
            for r in rows:
                s.append(r)
            s.close()
            import hashlib
            hashes.append(hashlib.sha256(open(p, "rb").read()).hexdigest())
        self.assertEqual(hashes[0], hashes[1])


if __name__ == "__main__":
    unittest.main()
