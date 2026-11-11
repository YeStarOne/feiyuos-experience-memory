# -*- coding: utf-8 -*-
"""11-12 下一批验收：
1) AppendOnly 跨进程幂等（两实例/外部直写/崩溃残行）
2) replay meta.json 版本闸（拒绝混合重放）+ source 落盘
3) 执行/基准证据分离（bench 行不进两轨）
4) pending_review 不进 refs、只进冲突对；冲突对不要求双边进 refs
5) settled 台账压实（同键留 max(v)，.bak 留档）
跑法：python -m pytest tests/test_next_batch.py -q（或 unittest）。
"""
import json
import os
import shutil
import tempfile
import unittest

from expmem import api, consolidate, retrieve
from expmem.store import AppendOnly


# ---------- 1) AppendOnly 跨进程 ----------

class TestAppendOnlyCrossProcess(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = os.path.join(self.d, "actions.jsonl")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _lines(self):
        out = []
        with open(self.p, encoding="utf-8") as f:
            for x in f:
                x = x.strip()
                if not x:
                    continue
                try:
                    out.append(json.loads(x))
                except Exception:
                    out.append(None)      # 残行/坏行：留档但不许破坏新行
        return out

    def test_two_instances_same_id_dedupes(self):
        a = AppendOnly(self.p)
        b = AppendOnly(self.p)          # 模拟另一个进程：各自 _seen
        self.assertTrue(a.append({"id": "x", "v": 1}))
        self.assertFalse(b.append({"id": "x", "v": 2}))   # B 吸收后认账
        self.assertTrue(b.append({"id": "y"}))
        a.close(); b.close()
        rows = self._lines()
        self.assertEqual([r["id"] for r in rows], ["x", "y"])
        self.assertEqual(rows[0]["v"], 1)   # 先到先得，后来者不覆盖

    def test_absorbs_external_writes(self):
        a = AppendOnly(self.p)
        a.append({"id": "a"})
        # 别家不经 AppendOnly 直写（如旧版本程序/手工补行）
        with open(self.p, "a", encoding="utf-8") as f:
            f.write(json.dumps({"id": "b"}) + "\n")
        a.append({"id": "c"})
        self.assertEqual([r["id"] for r in self._lines()], ["a", "b", "c"])
        self.assertEqual(len(a), 3)
        self.assertFalse(a.append({"id": "b"}))   # 吸收过的 id 不重复写
        a.close()

    def test_partial_line_repaired_not_absorbed(self):
        a = AppendOnly(self.p)
        a.append({"id": "a"})
        with open(self.p, "ab") as f:
            f.write(b'{"id":"crash-debris')      # 写方崩在半行（无换行）
        self.assertTrue(a.append({"id": "c"}))   # 补换行终止残行再追加
        rows = self._lines()
        self.assertEqual([(r or {}).get("id") for r in rows], ["a", None, "c"])
        self.assertTrue(a.append({"id": "c"}) is False)  # 幂等仍成立
        a.close()
        # 残行留档不删（历史一字不改）
        with open(self.p, "rb") as f:
            self.assertIn(b"crash-debris", f.read())


# ---------- 2) meta 版本闸 + replay 落 source ----------

def _mk_session_root(d):
    """最小 owner 会话：一回合一次 sd state 读。返回 root。"""
    root = os.path.join(d, "ws", "onebot-private-1000000001-test")
    os.makedirs(root, exist_ok=True)
    ev = [
        {"type": "turn/start", "seq": 1, "time": 1000, "data": {"turn": 1}},
        {"type": "tool/call", "seq": 2, "time": 1005, "data": {
            "turn": 1, "step": 1, "callId": "c1", "name": "bash",
            "arguments": json.dumps({"command": "sd raw /state"})}},
        {"type": "tool/result", "seq": 3, "time": 1010, "data": {
            "message": {"toolCallId": "c1", "isError": False,
                        "content": [{"type": "text", "text":
                                     json.dumps({"state": {"season": "winter",
                                                           "dayOfMonth": 25,
                                                           "time": 1740}})}]}}},
        {"type": "turn/end", "seq": 4, "time": 1100,
         "data": {"turn": 1, "reason": {"kind": "completed"}}},
    ]
    with open(os.path.join(root, "session.v4.jsonl"), "w", encoding="utf-8") as f:
        for e in ev:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return os.path.join(d, "ws")


class TestReplayMetaGate(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.root = _mk_session_root(self.d)
        self.out = os.path.join(self.d, "out")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_replay_writes_meta_and_source(self):
        m = api.replay(self.root, self.out, source="execution")
        self.assertEqual(m["actions_written"], 1)
        meta = json.load(open(os.path.join(self.out, "meta.json"), encoding="utf-8"))
        self.assertEqual(meta["parser_v"], 1)
        self.assertEqual(meta["outcome_v"], 2)
        self.assertEqual(meta["source"], "execution")
        with open(os.path.join(self.out, "episodes.jsonl"), encoding="utf-8") as f:
            rows = [json.loads(x) for x in f if x.strip()]
        self.assertTrue(rows)
        self.assertTrue(all(r.get("source") == "execution" for r in rows))

    def test_replay_idempotent_rerun(self):
        api.replay(self.root, self.out)
        m2 = api.replay(self.root, self.out)
        self.assertEqual(m2["actions_written"], 1)   # 同目录重跑不重复写
        with open(os.path.join(self.out, "actions.jsonl"), encoding="utf-8") as f:
            self.assertEqual(sum(1 for x in f if x.strip()), 1)

    def test_version_mismatch_rejected(self):
        api.replay(self.root, self.out)
        meta_path = os.path.join(self.out, "meta.json")
        meta = json.load(open(meta_path, encoding="utf-8"))
        meta["parser_v"] = 99
        json.dump(meta, open(meta_path, "w", encoding="utf-8"))
        with self.assertRaises(ValueError) as cm:
            api.replay(self.root, self.out)
        self.assertIn("版本不一致", str(cm.exception))
        meta["parser_v"] = 1
        meta["outcome_v"] = 1                        # 判据版本也要对齐
        json.dump(meta, open(meta_path, "w", encoding="utf-8"))
        with self.assertRaises(ValueError):
            api.replay(self.root, self.out)

    def test_legacy_dir_without_meta_passes(self):
        os.makedirs(self.out, exist_ok=True)
        with open(os.path.join(self.out, "actions.jsonl"), "w") as f:
            f.write("{}\n")                          # 旧产物没有 meta
        api.replay(self.root, self.out)              # 不抛；且补写了 meta
        self.assertTrue(os.path.exists(os.path.join(self.out, "meta.json")))


# ---------- 3) 执行/基准分离 ----------

def _ep_row(verdict, eid="e1", **kw):
    row = {"episode_id": eid, "session": "s1", "turn": 1, "t_from": 0, "t_to": 1,
           "game_day": "", "goal_hint": "", "actions": [], "verdict": verdict,
           "probe_status": "ok", "failure_class": "", "act_count": 0, "game": True,
           "outcome": {}, "skill_refs": []}
    row.update(kw)
    return row


def _act_row(aid, out, verb="raw", call_id="c1", session="s1"):
    return {"id": aid, "ts": 0, "session": session, "turn": 1, "step": 1,
            "tool": "bash", "adapter": "stardew_sd", "call_id": call_id,
            "sub_idx": 0, "verb": verb, "raw_path": "/store", "segment": "sd raw /store",
            "endpoint": "RAW /store", "is_error": False, "latency_ms": 1, "cwd": "",
            "out_head": out, "out_sha": "", "poll_repeat": False, "out_source": "",
            "job_id": "", "job_status": "", "job_exit": None, "job_done_ts": None}


class TestSourceGate(unittest.TestCase):
    def test_bench_episode_not_in_failure_track(self):
        eps = [_ep_row("failure", eid="b1", source="bench",
                       failure_class="noop",
                       outcome={"action": "a1", "attribution": "segment"})]
        acts = [_act_row("a1", 'stored:[]')]
        specs, dropped = consolidate.scan_candidates(eps, acts)
        self.assertEqual(specs, [])
        self.assertTrue(any(x.get("reason") == "non_execution_source"
                            for x in dropped))

    def test_bench_episode_not_in_success_track(self):
        eps = [_ep_row("success", eid="b1", source="fixture", goal_hint="存仓库")]
        acts = [_act_row("a1", '{"stored":["x"],"totalCost":0}')]
        stats = {}
        rows = consolidate.scan_success(eps, acts, stats=stats)
        self.assertEqual(rows, [])
        self.assertEqual(stats.get("excluded_non_execution"), 1)

    def test_missing_source_defaults_execution(self):
        eps = [_ep_row("failure", eid="e1", failure_class="noop",
                       outcome={"action": "a1", "attribution": "segment"})]
        acts = [_act_row("a1", 'stored:[]')]
        specs, dropped = consolidate.scan_candidates(eps, acts)
        self.assertEqual(len(specs), 1)              # 旧行无 source = 执行经验
        self.assertEqual(dropped, [])


# ---------- 4) pending_review 检索策略 ----------

class TestPendingPolicy(unittest.TestCase):
    def _cat(self, fronts):
        import expmem.retrieve as R
        return [dict(f) for f in fronts]

    def test_pending_not_in_refs_but_in_pairs(self):
        cat = self._cat([
            {"name": "buy-with-price", "description": "买东西必带价格", "state": "observed",
             "tool_key": "stardew.buy", "version": 2, "contradicts": ["buy-blind"],
             "retrieval_anchor": "buy price charge", "task_query": "买东西必带价格"},
            {"name": "buy-blind", "description": "买东西不带价格也能成", "state": "pending_review",
             "tool_key": "stardew.buy", "version": 1, "contradicts": ["buy-with-price"],
             "retrieval_anchor": "buy price charge", "task_query": "买东西不带价格"},
        ])
        r = retrieve.retrieve_skills("buy price charge", cat)
        names = [x["name"] for x in r["refs"]]
        self.assertIn("buy-with-price", names)
        self.assertNotIn("buy-blind", names)         # pending 不进目录行
        self.assertEqual(r["pending_hidden"], 1)
        self.assertEqual(r["conflict_pairs"], [["buy-blind", "buy-with-price"]])
        text, _ = retrieve.catalog_lines(r)
        self.assertIn("未确认哪版为准", text)

    def test_pair_survives_cap_single_side(self):
        cat = self._cat([
            {"name": "buy-with-price", "description": "买东西必带价格", "state": "verified",
             "tool_key": "stardew.buy", "version": 3, "contradicts": ["buy-blind"],
             "retrieval_anchor": "buy price charge", "task_query": "买东西必带价格"},
            {"name": "buy-blind", "description": "买东西不带价格也能成", "state": "pending_review",
             "tool_key": "stardew.buy", "version": 1, "contradicts": [],
             "retrieval_anchor": "buy price charge", "task_query": "买东西不带价格"},
        ])
        r = retrieve.retrieve_skills("buy price charge", cat, top_k=1)
        self.assertEqual([x["name"] for x in r["refs"]], ["buy-with-price"])
        # buy-blind 没过闸也没进 refs，但注入侧的 contradicts 指着它——照标
        self.assertEqual(r["conflict_pairs"], [["buy-blind", "buy-with-price"]])

    def test_deprecated_partner_not_a_pair(self):
        cat = self._cat([
            {"name": "buy-with-price", "description": "买东西必带价格", "state": "verified",
             "tool_key": "stardew.buy", "version": 3, "contradicts": ["buy-blind"],
             "retrieval_anchor": "buy price charge", "task_query": "买东西必带价格"},
            {"name": "buy-blind", "description": "旧版", "state": "deprecated",
             "tool_key": "stardew.buy", "version": 1, "contradicts": [],
             "retrieval_anchor": "buy price charge", "task_query": "旧"},
        ])
        r = retrieve.retrieve_skills("buy price charge", cat)
        self.assertEqual(r["conflict_pairs"], [])    # 降废的旧版不算悬案

    def test_df_cap_does_not_scale_with_catalog_size(self):
        # 11-12 夜零假设实锤的 bug：旧 df_cap=max(4,ceil(.6N)) 让闸随图书馆变大
        # 而变松（N=8→5，N=20→12，N=50→30），到 20 张时任何词都够不上「通用」。
        # 零假设对照：往 8 卡里塞 12 张**纯随机噪声卡**，旧规则下判别力从
        # 36.2pp 假涨到 51.5pp、槽位 161/236→306/297（涨的全是噪声）；
        # 固定 5 后塞同样的噪声卡，命中一位数都不变。
        from expmem.retrieve import _df_cap
        self.assertEqual(_df_cap(8), 5)          # 现网口径不变
        self.assertEqual(_df_cap(20), 5)         # 卡变多不许变松
        self.assertEqual(_df_cap(50), 5)
        self.assertEqual(_df_cap(3), 4)          # 小语料下限仍是不剥（向后兼容）
        # 行为层：造一个 df=6 的模板词（在 6/20 张卡里出现）。旧规则 N=20 →
        # cap=12，6<12 ⇒ 当证据放行；新规则 cap=5，6>=5 ⇒ 剥掉。这才是缩放
        # bug 的真实后果，只比「加随机噪声」是抓不到的（噪声卡 df=1，两种
        # 规则下都照旧当证据，而它与查询不相交所以零命中）。
        shared = [{"name": "sh-%d" % i, "description": "公共词模板", "state": "candidate",
                   "tool_key": "sh.%d" % i,
                   "retrieval_anchor": "对账 公共词 uq%d" % i,
                   "task_query": "公共词 对账"} for i in range(6)]
        others = [{"name": "ot-%d" % i, "description": "别的", "state": "candidate",
                   "tool_key": "ot.%d" % i, "retrieval_anchor": "ot%d zz%d" % (i, i),
                   "task_query": ""} for i in range(14)]
        r = retrieve.retrieve_skills("公共词", self._cat(shared + others))
        # 「公共词」出现在 6/20 张卡里 = 模板词，剥完这 6 张只剩单字/无证据
        self.assertEqual([x["name"] for x in r["refs"]], [])
        self.assertEqual(r["generic_only"], 6)   # 剥掉而不是静默消失

    def test_generic_token_stripped_on_real_size_catalog(self):
        # 11-12 夜真库反事实实锤：8 张卡的默认 task_query 全含「这张卡」，
        # 聊天回合「把这张图发给小主人」曾命中全部卡。真库量级(≥7)下
        # 通用 token 必须剥掉；剥完空的不进 refs 也不进 dropped_weak。
        # 11-13 深夜：证据面收成 anchor+标识符后，模板词必须放进 **anchor**
        # 才测得到 df 剥离（留在 task_query 里现在连证据都不是，测的就成了
        # 字段边界而不是 df_cap）。
        cat = self._cat([
            {"name": "card-%d" % i, "description": "星露谷 工具%d" % i,
             "state": "candidate", "tool_key": "stardew.t%d" % i,
             "retrieval_anchor": "tool%d zig%d 这张卡" % (i, i),
             "task_query": "对 stardew.t%d 的结果负责时先查这张卡" % i}
            for i in range(8)
        ])
        r = retrieve.retrieve_skills("把这张图发给小主人", cat)
        self.assertEqual([x["name"] for x in r["refs"]], [])
        self.assertEqual(r["generic_only"], 8)       # 全部是模板词命中
        self.assertEqual(r["dropped_weak"], 0)
        r2 = retrieve.retrieve_skills("tool3 zig3", cat)   # 唯一实词照常命中
        self.assertEqual([x["name"] for x in r2["refs"]], ["card-3"])
        self.assertEqual(r2["generic_only"], 0)

    def test_mixed_script_segment_yields_no_cross_script_bigrams(self):
        """11-12 夜真库实锤：中英混排段统一切 bigram 会造跨界碎片。

        `restart` → es/ta/ar/st、`yestar` → ye/es、`读state校验` → 读s/e校。
        这些碎片**跨卡通用**（凡提过 restart/state 的卡都产同一批），于是
        `stardew-persist-fish-guard` 靠 es+ta 命中了 103/158 个**动作回合**
        与 71/844 个聊天回合——不是它相关，是碎片撞上了。
        """
        from expmem.retrieve import _tokens
        junk = {"es", "ta", "ar", "st", "ye", "re", "rt", "at", "te"}
        for s in ("8s让位restart-fish", "来自yestar/stardew-host",
                  "读state校验", "V1.6.15-fish 路径"):
            got = _tokens(s)
            self.assertFalse(got & junk, "%s → %s" % (s, sorted(got & junk)))
            # 跨界的单字/双字碎片也不许有（碎片里含中文的那个方向）
            self.assertFalse({t for t in got if any("\u4e00" <= c <= "\u9fff" for c in t)
                              and any(not ("\u4e00" <= c <= "\u9fff") for c in t)},
                             "%s → %s" % (s, sorted(got)))
        # 切开后整段 ASCII 仍是完整词：精确对得上，只是不再吐碎片
        self.assertIn("restart", _tokens("8s让位restart-fish"))
        self.assertIn("fish", _tokens("8s让位restart-fish"))
        self.assertIn("state", _tokens("读state校验"))
        self.assertIn("校验", _tokens("读state校验"))
        # 中文段的单字+bigram 召回不受影响
        self.assertTrue({"让位", "位", "让"} <= _tokens("8s让位restart-fish"))

    def test_mixed_script_junk_no_longer_drives_false_recall(self):
        # 行为层：卡里写 restart，查询里写「重启」+另一个无关词时不该靠 es/ta 撞上
        cat = self._cat([
            {"name": "persist-guard", "description": "8s让位restart-fish 判活",
             "state": "candidate", "tool_key": "stardew.persist",
             "retrieval_anchor": "8s让位restart-fish 判活 进程 路径",
             "task_query": "按可执行路径判活"},
        ])
        # 「restart」与「state」这两个词的碎片 es/ta 曾是通用撞车点
        self.assertEqual(retrieve.retrieve_skills("读state校验", cat)["refs"], [])
        # 真提到 restart 仍能召回
        self.assertEqual([x["name"] for x in
                          retrieve.retrieve_skills("restart 判活", cat)["refs"]],
                         ["persist-guard"])

    def test_small_catalog_untouched(self):
        # 小语料（<7 张）cap>N 等于不剥——夹具/小目录向后兼容。
        # 查询给「这张卡」而不是「这张」：min_multi=2 下单个词只算一条证据，
        # 这里要测的是「没被当模板词剥掉」，不是闸的宽度。
        # 11-13 深夜：「这张卡」从 task_query 挪进 anchor——证据面现在只认
        # anchor+标识符，模板词留在叙述句里就测不到 df 剥离了。
        cat = self._cat([
            {"name": "a", "description": "甲", "state": "candidate",
             "tool_key": "stardew.a", "retrieval_anchor": "alpha 这张卡",
             "task_query": "先查这张卡"},
            {"name": "b", "description": "乙", "state": "candidate",
             "tool_key": "stardew.b", "retrieval_anchor": "beta 这张卡",
             "task_query": "先查这张卡"},
        ])
        r = retrieve.retrieve_skills("这张卡", cat)
        self.assertEqual(len(r["refs"]), 2)          # 两张都能过（旧行为保留）

    def test_slash_split_hygiene_and_recall(self):
        # 11-12 夜真库实锤：'/' 不切 → 卡里 warp/move/face 斜杠连体与查询里
        # 图片路径 [图片:/home/...] 产出 'e/''nt''t/' 碎片乱匹配（sd-act 噪声）
        # 11-13 深夜：斜杠连体搬进 anchor（证据面只认 anchor+标识符）。
        cat = self._cat(
            [{"name": "sd-act", "description": "星露谷 act", "state": "candidate",
              "tool_key": "stardew.act",
              "retrieval_anchor": "act warp/move/face/use/select/interact",
              "task_query": "发一个改状态的动作"}]
            + [{"name": "card-%d" % i, "description": "星露谷 工具%d" % i,
                "state": "candidate", "tool_key": "stardew.t%d" % i,
                "retrieval_anchor": "tool%d zig%d" % (i, i),
                "task_query": "对 stardew.t%d 的结果负责时" % i}
               for i in range(7)]
        )
        r = retrieve.retrieve_skills("[图片:/home/agent/.dsh/media/media_1.jpg]", cat)
        self.assertEqual([x["name"] for x in r["refs"]], [])   # 路径碎片不再命中
        # 连体词拆开可召回：用两个拆出来的词（min_multi=2，单词「warp」只算一条证据）
        r2 = retrieve.retrieve_skills("warp move", cat)
        self.assertEqual([x["name"] for x in r2["refs"]], ["sd-act"])

    def test_default_task_query_template_clean(self):
        # 归纳模板不许再产出「先查这张卡」级口语泛词（检索噪声源，见真库反事实）
        from expmem.consolidate import DEFAULT_TASK_QUERY
        self.assertNotIn("这张", DEFAULT_TASK_QUERY % "stardew.buy")
        self.assertNotIn("先查", DEFAULT_TASK_QUERY % "stardew.buy")

    def test_dash_is_separator_not_evidence(self):
        # 11-12 夜真库实锤：破折号族是**标点不是词**。不切则两重污染——
        # ①它自己当证据（聊天回合噪声 top2 全靠它，8 卡里 4 张 description 写了
        #   「——」）；②把邻字粘成 '手—''—不''天—' 垃圾 bigram，卡与查询两侧
        #   都中招。真库上 35 条「只因破折号才过闸」的命中**全是 [RN 长信、全都
        #   ≥80 字**，她自己的短动作查询一条没变（16.0%→16.0%）。
        toks = retrieve._tokens("看看画面——怎么样")
        self.assertFalse([t for t in toks if "—" in t], toks)
        self.assertIn("画面", toks)          # 破折号两侧的字各自成词
        self.assertIn("怎么", toks)
        # 卡侧同理：带破折号的 anchor 不再产出破折号 token
        cat = self._cat([{"name": "d", "description": "甲——乙", "state": "candidate",
                          "tool_key": "x.d", "retrieval_anchor": "做完了——再对账",
                          "task_query": "对账"}])
        r = retrieve.retrieve_skills("—— ——", cat)
        self.assertEqual([x["name"] for x in r["refs"]], [])   # 破折号单独出现不构成证据


# ---------- 5) settled 台账压实 ----------

class TestSettleCompaction(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = os.path.join(self.d, ".settled.jsonl")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _write(self, rows):
        with open(self.p, "w", encoding="utf-8", newline="\n") as f:
            for s, t, v in rows:
                f.write(json.dumps({"session": s, "turn": t, "v": v,
                                    "reason": "complete"}) + "\n")

    def _read(self):
        with open(self.p, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]

    def test_keeps_max_v_per_key(self):
        self._write([("s1", 1, 1), ("s1", 1, 2), ("s2", 2, 2)])
        n = api._compact_settled(self.p)
        self.assertEqual(n, 2)
        rows = self._read()
        self.assertEqual([(r["session"], r["turn"], r["v"]) for r in rows],
                         [("s1", 1, 2), ("s2", 2, 2)])
        self.assertTrue(os.path.exists(self.p + ".bak"))    # 旧账留档
        self.assertEqual(len(open(self.p + ".bak", encoding="utf-8").readlines()), 3)

    def test_no_duplicate_keys_untouched(self):
        self._write([("s1", 1, 2), ("s2", 2, 2)])
        n = api._compact_settled(self.p)
        self.assertEqual(n, 2)
        self.assertFalse(os.path.exists(self.p + ".bak"))   # 无压空间不动文件

    def test_missing_file_zero(self):
        self.assertEqual(api._compact_settled(self.p), 0)

    def test_settle_result_carries_compacted(self):
        # 接口层冒烟：空集 settle 也带 compacted 键（消费者不用 getattr 探测）
        sdir = tempfile.mkdtemp()
        try:
            out = api.settle_auto_marks([], [], skills_dir=sdir)
            self.assertEqual(out["compacted"], 0)
        finally:
            shutil.rmtree(sdir, ignore_errors=True)


class TestUnreachableAudit(unittest.TestCase):
    """11-12 夜真库发现：21 张卡里 13 张**永远进不了检索**，而没有任何地方报出来。

    它们不是被闸挡的（闸挡了会进 dropped_weak，看得见），而是根本没进 catalog：
    缺 state / 缺检索必需字段 / 没有 frontmatter。检索侧与「不存在」等价，
    load_catalog 静默 continue——她也永远不知道错题本有这几页。
    """

    def _mk(self, name, front, body="- 正文"):
        import tempfile, os, json as J
        d = tempfile.mkdtemp()
        p = os.path.join(d, name)
        os.makedirs(p)
        text = ("---\n%s\n---\n%s" % (J.dumps(front, ensure_ascii=False), body)
                if front is not None else body)
        with open(os.path.join(p, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(text)
        return d

    def test_counts_and_reasons(self):
        import tempfile, shutil
        good = {"name": "good", "state": "candidate", "tool_key": "a.b",
                "retrieval_anchor": "对账 state", "task_query": "核对"}
        no_state = {"name": "nostate", "description": "x", "confidence": 0.9,
                    "retrieval_anchor": "对账", "task_query": "核对"}
        no_anchor = {"name": "noanchor", "state": "candidate", "tool_key": "a.c",
                     "description": "x"}
        dirs = [self._mk("good", good), self._mk("nostate", no_state),
                self._mk("noanchor", no_anchor),
                self._mk("nofm", None, "# 只有正文")]
        try:
            # 合成一个卡库：把四个临时目录的 SKILL.md 挪到一个根下
            root = tempfile.mkdtemp()
            for d in dirs:
                for nm in os.listdir(d):
                    shutil.copytree(os.path.join(d, nm), os.path.join(root, nm))
            from expmem.retrieve import audit_unreachable
            rep = audit_unreachable(root)
            self.assertEqual(rep["total"], 4)
            # noanchor 能进库（load_catalog 只认 state），但 _hay 只剩
            # name+description ⇒ 归 weak_hay，不是 unreachable
            self.assertEqual(rep["loaded"], 2)
            self.assertEqual(len(rep["unreachable"]), 2)
            self.assertEqual(len(rep["weak_hay"]), 1)
            reasons = {os.path.basename(os.path.dirname(r["path"])): r["reason"]
                       for r in rep["unreachable"]}
            self.assertIn("state", reasons["nostate"])
            self.assertIn("frontmatter", reasons["nofm"])
            weak = {os.path.basename(os.path.dirname(r["path"])): r["reason"]
                    for r in rep["weak_hay"]}
            self.assertIn("retrieval_anchor", weak["noanchor"])
        finally:
            for d in dirs + [root]:
                shutil.rmtree(d, ignore_errors=True)

    def test_cli_exit_code_is_a_real_gate(self):
        # `audit` 的退出码就是闸：有卡进不去 → 非零。曾经 __main__ 里直接
        # 调 main() 会把这个 return 丢掉（闸恒 0 = 假绿）。
        import tempfile, shutil, io, contextlib
        from expmem import cli
        good = {"name": "g", "state": "candidate", "tool_key": "a.b",
                "retrieval_anchor": "对账", "task_query": "核对"}
        d = self._mk("g", good)
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc_ok = cli.main(["audit", "--skills", d])
            self.assertEqual(rc_ok, 0)
            bad = self._mk("bad", {"name": "b", "description": "x"})
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    rc_bad = cli.main(["audit", "--skills", bad])
                self.assertEqual(rc_bad, 1)
            finally:
                shutil.rmtree(bad, ignore_errors=True)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestFunctionWordStripping(unittest.TestCase):
    """虚字剥离（11-12 深夜）：真库噪声 88/757→59/757、误召 1→0、召回零损失。

    鉴别特征是**两侧都虚才算虚**——「一下」「什么」是句法，「浇水」「对账」
    是内容，后者必须存活（已逐条验证）。
    """

    def test_pure_function_bigrams_are_dropped(self):
        from expmem.retrieve import _tokens
        # 只有**两侧都虚**的 bigram 才被拦（「好吧」里「好」是实词，故不在此列）
        for s in ("发一下", "买什么", "什么鱼", "么么", "一下"):
            toks = _tokens(s)
            for junk in ("一下", "什么", "么么", "一", "下", "什", "么"):
                self.assertNotIn(junk, toks, "「%s」里的虚词 %r 不该进 token" % (s, junk))

    def test_content_bigrams_survive(self):
        # 零误伤：这些是实词，剥虚字绝不能碰到
        from expmem.retrieve import _tokens
        for s, must in (("浇水", "浇水"), ("钓鱼", "钓鱼"), ("对账", "对账"),
                        ("菜熟了", "菜熟"), ("菜熟了", "熟了"), ("种地", "种地"),
                        ("夜市", "夜市"), ("下矿", "下矿"), ("个人", "个人"),
                        ("下面", "下面"), ("好吧", "好吧")):
            self.assertIn(must, _tokens(s), "实词 %r 被误剥了" % must)

    def test_single_content_char_survives(self):
        # 「下矿」的单字「下」被剥（它是虚字），但「矿」必须留——单字管召回
        from expmem.retrieve import _tokens
        self.assertIn("矿", _tokens("下矿"))
        self.assertIn("鱼", _tokens("什么鱼"))

    def test_social_chatter_no_longer_pulls_card(self):
        # 头号案发现场：她随口一句「哈哈，鱼鱼发一下今天的黑盒文章草稿吧」
        # 曾靠 `一下`+`发一` 两个纯虚 bigram 命中 sd-act。
        # 这里卡锚**只写虚词**（模拟靠模板词吸流量的卡）：剥离后必须零命中。
        import tempfile, shutil, os
        from expmem import retrieve as R
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "c", "SKILL.md")
            os.makedirs(os.path.dirname(p))
            with open(p, "w", encoding="utf-8") as f:
                f.write('---\n%s\n---\n\n# x\n' % json.dumps({
                    "name": "c", "state": "candidate", "tool_key": "sd.act",
                    "retrieval_anchor": "一下 什么 一个 好吧",
                    "task_query": "一下 什么",
                }, ensure_ascii=False))
            cat = R.load_catalog(d)
            got = R.retrieve_skills(
                "哈哈，鱼鱼发一下今天的黑盒文章草稿吧，有意思的都写进去", cat)
            self.assertEqual(got["refs"], [],
                             "纯虚 bigram 又当上证据了：%r" % got["refs"])
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestEvidenceFieldBoundary(unittest.TestCase):
    """证据面 = anchor + 标识符；叙述句（task_query/description）只进排序。

    11-13 深夜真库实测（34 动作 / 757 纯聊天 / 22 标注）：
        证据面=全字段  动作 3/34=8.8%  噪声 57/757=7.5%  ⇒ 判别力 +1.3pp
        证据面=anchor  动作 4/34=11.8% 噪声 42/757=5.5%  ⇒ 判别力 +6.2pp
    反向对照（把 _evidence_hay 改回 _hay）精确回到 3/34 · 57/757 ⇒ 真因确认。
    噪声靠的 token 大半只出现在 task_query 的叙述句里（自己/之前/完成/一步/确认）。
    """

    def _cat(self, fronts):
        import tempfile, os, shutil
        from expmem import retrieve as R
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        for front in fronts:
            p = os.path.join(d, front["name"], "SKILL.md")
            os.makedirs(os.path.dirname(p))
            with open(p, "w", encoding="utf-8") as f:
                f.write('---\n%s\n---\n\n# x\n' % json.dumps(front, ensure_ascii=False))
        return R.load_catalog(d)

    def test_prose_in_task_query_is_not_evidence(self):
        # 卡只把「自己/之前/完成」这类叙述词写在 task_query 里 ⇒ 聊天句不许靠它过闸
        from expmem import retrieve as R
        cat = self._cat([{
            "name": "probe", "state": "candidate", "tool_key": "sd.state",
            "retrieval_anchor": "对账 存仓库 收菜",
            "task_query": "完成写操作之后自己确认一下，之前的一步也要看",
            "description": "确认成败再继续",
        }])
        for q in ("你现在回复怎么改", "我之前做完了", "自己看看完成了没"):
            self.assertEqual(R.retrieve_skills(q, cat)["refs"], [], q)
        # 真锚词照旧召回（两个多字证据，min_multi=2）
        self.assertEqual([x["name"] for x in
                          R.retrieve_skills("对账 收菜", cat)["refs"]], ["probe"])

    def test_identifier_still_enters_evidence(self):
        # 标识符（卡名/工具键）不是散文，`probe after` 这类查询只能从卡名进来
        from expmem import retrieve as R
        cat = self._cat([{
            "name": "stardew-probe-after-action", "state": "candidate",
            "tool_key": "stardew.state",
            "retrieval_anchor": "对账 存仓库",
            "task_query": "完成之后确认",
        }])
        self.assertEqual([x["name"] for x in
                          R.retrieve_skills("probe after", cat)["refs"]],
                         ["stardew-probe-after-action"])

    def test_sorting_still_sees_prose(self):
        # 剥的是「能不能当证据」，不是「还参不参与排序」：when 仍取自 task_query
        from expmem import retrieve as R
        cat = self._cat([{
            "name": "sd-buy", "state": "candidate", "tool_key": "sd.buy",
            "retrieval_anchor": "价格 扣钱",
            "task_query": "买东西必带价格",
        }])
        r = R.retrieve_skills("价格 扣钱", cat)
        self.assertEqual(len(r["refs"]), 1)
        self.assertIn("价格", r["refs"][0]["when"])   # 目录行那句仍来自叙述句


class TestCrossWordFragments(unittest.TestCase):
    """跨词碎片诊断：区分「滑窗造的假词」与「词表缺词」。

    「鱼鱼该浇水了吧」→ 该|浇水 分属两段且浇水是多字词 ⇒ 「该浇」是碎片；
    「鱼鱼钓鱼吗」→ 词表只有「钓鱼开关」，两字都落单字段 ⇒ 不是碎片，
    那是**词表缺口**（卡里没写这个词），不是滑窗造的。
    """

    def test_fragment_detected_when_segments_disagree(self):
        from expmem.retrieve import cross_word_fragments
        lex = {"浇水", "收菜", "钓鱼开关"}
        got = cross_word_fragments("鱼鱼该浇水了吧", lex)
        self.assertIn("该浇", got)          # 该=单字段，浇∈「浇水」⇒ 碎片

    def test_vocabulary_gap_is_not_a_fragment(self):
        from expmem.retrieve import cross_word_fragments
        lex = {"浇水", "收菜", "钓鱼开关"}
        got = cross_word_fragments("鱼鱼钓鱼吗", lex)
        self.assertNotIn("钓鱼", got)       # 词表缺词 ≠ 滑窗造假

    def test_known_words_never_fragments(self):
        # 她的名字不在卡锚里（卡不写自己名字），但它是完整词不是碎片
        from expmem.retrieve import cross_word_fragments
        lex = {"浇水", "收菜"}
        for q in ("鱼鱼浇水了吗", "肥鱼收菜了吗", "主持浇水了"):
            got = cross_word_fragments(q, lex)
            for name in ("鱼鱼", "肥鱼", "主持"):
                self.assertNotIn(name, got, "%s 被误报成碎片" % name)


if __name__ == "__main__":
    unittest.main()
