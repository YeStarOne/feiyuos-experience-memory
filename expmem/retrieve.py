# -*- coding: utf-8 -*-
"""retrieve_skills 实现（S2）：词法 + 元数据，第 0 级阶梯（方案 §7-7/7-10/7-11）。

三闸（OpenViking 对齐）：
  ① 硬阈值——低于不注入，weak hit 宁可不递（Voyager 无阈值的教训）；且**闸与排序
     分家**：过闸只吃命中证据（命中数/多字 token），confidence 与 state 只进排序；
  ② 不静默截断——候选超预算走**两级降级**（带 when → 只留名字 → 退单行），
     最坏有界、永不空手；调用方拿 incomplete 标志自行选级；
  ③ 多层命中折一条（按 name 去重保最高分）。
呈现（MemU 对齐）：
  - refs 只检 candidate/observed/verified（11-12 下一批：pending_review 是
    「有人得先人工看一眼」的卡——矛盾/证据存疑，注入她等于把未裁决的答案
    递上去；它们只进冲突对与 total_active，不进目录行）；**deprecated 不进
    召回、不挡新卡**（门语料口径）；
  - 同 tool_key 互指矛盾的两张卡**成对呈现**+「未确认哪版为准」，不替模型
    裁决；11-12 起对子**不要求两边都进 refs**——注入侧带 contradicts 指针，
    对手卡在库里 active 就标（被 cap 截掉的单边不再静默失配）；
  - domain 缺省=通用放行（向后兼容）；跨域默认过滤、显式点名放行。
包装语纪律（Suzu/neo4j 对齐）：目录行是「回忆依据不是命令」；追问只接受本轮
已交付的 name；「摘要待核，未提及≠不存在」由调用方话术层承担，本模块不掺。
"""
import glob
import json
import math
import os
import re

# 预算 frozen 常量（方案 §7-11-6），不散落魔法数
LIMITS = {
    "max_inject": 3,          # 目录行最多几条
    "when_max_chars": 80,     # 单行 when 截断
    "catalog_token_budget": 600,  # 注入 token 预算（实测调）
    # 过闸：至少命中几个**多字** token（纯单字堆叠不算证据）。
    # 11-12 夜真库判别力实测（她 1001 个真实回合，158 有动作 / 843 纯聊天）：
    #   1 → 判别力 20.2pp（有动作 75.3% vs 聊天 55.2%，聊天回合 1296 个噪声槽位）
    #   2 → 判别力 36.2pp（有动作 53.8% vs 聊天 17.6%，噪声槽位 248）
    # 聊天回合是「她随口说话」的回合，那种回合递卡 100% 是噪声。要 2 个多字
    # 命中把噪声压掉 5 倍，同时真该有卡的回合仍留 54%（不是把闸关死）。
    # 注意：单字召回本身**不能**当杠杆——实测关掉 CJK 单字（只留 bigram）
    # 判别力一点没变，因为 bigram 已经把该匹配的都匹配上了。
    "min_multi_hits": 2,
}
ACTIVE_STATES = ("candidate", "observed", "verified", "pending_review")
# 进目录行（refs）的状态——pending_review 只进冲突对/total_active（11-12 下一批）
REF_STATES = ("candidate", "observed", "verified")
_STATE_BONUS = {"verified": 2.0, "observed": 1.0, "candidate": 0.0, "pending_review": 0.0}
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
# intel 实测：阈值 1.0 + 单字召回叠加后，中文功能字单字会让任何卡片过闸
# （「我今天是不是该收菜了」→ 我/是/不/了 全是噪声却计分）。功能字不进单字召回，
# bigram 与实词单字照旧。
STOPCHARS = frozenset("的了吗呢是我你他这那不有在就都也还")


def estimate_tokens(s: str) -> int:
    """粗估：CJK/全角 ≈1.5 token/字（cmb 实测口径），ASCII ≈0.3 token/字符。"""
    wide = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff"
               or "\u3000" <= ch <= "\u303f" or "\uff00" <= ch <= "\uffef")
    return int(wide * 1.5 + (len(s) - wide) * 0.3) + 1


def _tokens(s: str):
    """cmb-P0：空白分词+整 token 相等在中文上零召回（「怎么买种子」整串一个
    token，与 hay 永不相交，连弱命中都拿不到）。

    修法（Suzu 同款加宽）：复合 token 按 [-_./] 再拆一层（11-12 夜加 '/'：
    卡里 warp/move/face 斜杠连体和查询里图片路径 [图片:/home/...] 都会产出
    'e/''nt''t/' 碎片乱匹配——路径分隔符两侧都必须切开）；破折号族（—–―─－）
    同理切成空格：它是**标点不是词**，留在串里既自己当证据、又把邻字粘成
    '手—''—不''天—' 这种垃圾 bigram（真库实测 8 卡里有 4 张的 description
    写了「——」，聊天回合噪声 top2 全靠它）；CJK 串出 **单字+bigram**
    （单字管召回，bigram 管精度），英文原样。"""
    out = set()
    s = re.sub(r"[\u2014\u2013\u2015\u2500\uff0d]+", " ", (s or "").lower())
    for t in re.split(r"[\s，,。？！?!；;、（）()]+", s):
        if not t:
            continue
        for sub in re.split(r"[-_./]+", t):
            if not sub:
                continue
            # 中英混排必须先按字符族切开再切 bigram（11-12 夜修）。旧写法对含
            # 中文的整段统一切 bigram，跨界处必然造垃圾：`restart` → es/ta/ar/st、
            # `yestar` → ye/es、`读state校验` → 读s/e校。这些碎片**跨卡通用**
            # （凡卡里提过 restart/state 就都产同一批），真库实测
            # `stardew-persist-fish-guard` 靠 es+ta 两个碎片命中 103/158 个
            # **动作回合**和 71/844 个聊天回合——不是它相关，是碎片撞上了。
            # 切开后：CJK 段照旧单字+bigram（召回不变），ASCII 段整段当词
            # （`restart` 仍能与查询里的 `restart` 精确对上，只是不再吐碎片）。
            for run in re.finditer(r"[\u4e00-\u9fff]+|[^\u4e00-\u9fff]+", sub):
                seg = run.group()
                if _CJK_RE.search(seg):
                    out.update(ch for ch in seg if ch not in STOPCHARS)  # 单字管召回（功能字除外）
                    if len(seg) >= 2:
                        # bigram 管精度；但**纯功能字 bigram**（不是/是不/了么）不算证据
                        # ——cmb 实测：卡里写「不是」时 query「是不是」就会命中它
                        out.update(seg[i:i + 2] for i in range(len(seg) - 1)
                                   if not all(ch in STOPCHARS for ch in seg[i:i + 2]))
                else:
                    out.add(seg)
    return out


def load_catalog(skills_dir: str):
    """扫 SKILL.md / *.md 的 frontmatter，返回 active 卡列表。

    两类不进：deprecated（打脸不删卡但不再召回）；counter≤0（ExpeL REMOVE——
    台账清零的卡移出书，文件留盘。缺省 counter=基线 1，旧卡不受影响）。"""
    fronts = []
    paths = sorted(glob.glob(os.path.join(skills_dir, "**", "*.md"), recursive=True))
    for p in paths:
        try:
            with open(p, encoding="utf-8") as f:
                text = f.read()
        except Exception:
            continue
        if not text.startswith("---"):
            continue
        end = text.find("\n---", 3)
        if end < 0:
            continue
        try:
            front = json.loads(text[3:end].strip())
        except Exception:
            continue  # 坏卡跳过但留给 lint 报（这里不抛，检索不许被一张坏卡打死）
        if front.get("state") not in ACTIVE_STATES:
            continue
        try:
            counter = int(front.get("counter", 1) or 0)
        except Exception:
            counter = 1
        if counter <= 0:
            continue
        front["_path"] = p
        fronts.append(front)
    return fronts


def audit_unreachable(skills_dir: str) -> dict:
    """哪些卡**永远进不了检索**——静默盲区审计（11-12 夜）。

    起因：真库 21 张卡里 13 张从来没被召回。它们不是被闸挡的（闸挡了会进
    dropped_weak，看得见），而是**根本没进 catalog**：缺 `state`（旧卡手写，
    frontmatter 只有 description/confidence/evidence），或整个文件没有
    frontmatter。这种卡在检索侧与「不存在」等价，而 `load_catalog` 静默
    `continue`——没有任何地方会报出来，她也永远不知道这本错题本有这几页。

    两种盲区分开报（修法完全不同）：
      - `unreachable`：load_catalog 直接跳过（缺 state / 无 frontmatter /
        坏 JSON / counter≤0）——**加字段就能救**；
      - `weak_hay`：进了 catalog，但缺 retrieval_anchor/task_query ⇒ `_hay`
        只剩 name+description，几乎与任何查询零相交——**要重写锚词**，
        补字段救不了（它本来就在库里，只是召回不了）。

    返回 {"total", "loaded", "unreachable": [...], "weak_hay": [...]}。
    reason 只报**第一处**缺失，便于一次修一个（缺 state 的卡补完 state 往往
    还会暴露缺 name，分两轮比一次报五个好改）。"""
    out = {"total": 0, "loaded": 0, "unreachable": [], "weak_hay": []}
    paths = sorted(glob.glob(os.path.join(skills_dir, "**", "*.md"), recursive=True))
    by_path = {f["_path"]: f for f in load_catalog(skills_dir)}
    for p in paths:
        out["total"] += 1
        loaded = by_path.get(p)
        if loaded is not None:
            out["loaded"] += 1
            missing = [k for k in ("retrieval_anchor", "task_query")
                       if not loaded.get(k)]
            if missing:
                out["weak_hay"].append({
                    "path": p,
                    "reason": "缺 %s（在库里但 _hay 只剩 name+description，"
                              "几乎与任何查询零相交）" % "/".join(missing),
                })
            continue
        reason = None
        try:
            with open(p, encoding="utf-8") as f:
                text = f.read()
        except Exception as e:
            out["unreachable"].append({"path": p, "reason": "读不出来: %r" % e})
            continue
        if not text.startswith("---"):
            reason = "没有 frontmatter（整篇都是正文）"
        else:
            end = text.find("\n---", 3)
            if end < 0:
                reason = "frontmatter 未闭合"
            else:
                try:
                    front = json.loads(text[3:end].strip())
                except Exception:
                    reason = "frontmatter 不是合法 JSON"
                else:
                    if front.get("state") not in ACTIVE_STATES:
                        reason = "state=%r 不在 ACTIVE_STATES（旧卡手写缺这个字段）" % (
                            front.get("state"),)
                    else:
                        try:
                            if int(front.get("counter", 1) or 0) <= 0:
                                reason = "counter≤0（ExpeL 移出书）"
                        except Exception:
                            pass
        out["unreachable"].append({"path": p, "reason": reason or "未知（load_catalog 跳过）"})
    return out


def _hay(front: dict) -> str:
    return " ".join([front.get("retrieval_anchor", ""), front.get("task_query", ""),
                     front.get("name", ""), front.get("tool_key", ""),
                     front.get("description", "")])


def _evidence(q_tokens: set, front: dict, domain=None):
    """闸与排序分家（cmb 验收口径）：本函数只算**命中证据**，不掺 confidence/state。

    返回 (hit, matched)。跨域默认过滤→hit=0（显式点名时由调用方换 domain 参数）。
    """
    d = front.get("domain")
    if domain and d and d != domain:
        return 0, set()
    matched = q_tokens & _tokens(_hay(front))
    return len(matched), matched


def _df_cap(n: int) -> int:
    """通用 token 判别阈值：在 ≥cap 张卡的 hay 里出现过的 token 无鉴别力。

    **固定 5，不随卡数缩放**（11-12 夜修正）。旧写法 `max(4, ceil(0.6N))`
    是反的：N 越大 cap 越大 ⇒ 图书馆越大闸越松。实测 N=8 时 cap=5（「这张」
    级模板词被剥），N=20 时 cap=12、N=50 时 cap=30——那时任何词都够不上
    「通用」，闸形同虚设。零假设对照实锤了这一点：往 8 卡里塞 12 张**纯随机
    噪声卡**（df 全是 1），判别力从 36.2pp 涨到 51.5pp、槽位从 161/236 涨到
    306/297——涨的全是噪声，纯粹因为 cap 从 5 抬到了 12。改成固定 5 后，
    同样塞 12 张随机噪声卡：判别力与槽位**一位数都不变**（噪声卡 df=1 < 5，
    照旧当证据，但它们与任何查询都不相交，所以零命中）。

    5 的来源：现网 8 卡实测（长度分层判别力 +21.3pp）与缩放版完全一致，
    是「不改变现网行为」的那个值；固定 3/4 会剥掉真证据（+20.0/+20.7pp），
    固定 8 略松（+22.8pp 但 51-100 字桶更差）。"""
    return max(4, min(5, max(n, 1)))


def _df_map(catalog) -> dict:
    """token → 出现在几张卡的 hay 里（机械闸先跑真产物命中数教训的产物：
    真库 8 卡全含「这张卡」，任何带「这张」的聊天回合能命中全部卡再靠
    confidence 瞎选——手工黑名单追不完模板词，语料自己会招供）。"""
    df = {}
    for f in catalog:
        for t in _tokens(_hay(f)):
            df[t] = df.get(t, 0) + 1
    return df


def _passes(hit: int, matched: set) -> bool:
    """过闸判据——只许吃命中证据，confidence 与 state bonus 一律不进（否则零相关卡
    只要 conf 高就能混进目录行，阈值形同虚设）：

    ① 必须至少命中一个**多字** token（bigram/英文词有形状）。纯单字堆叠不算证据——
       cmb 实测「一下」在真卡上 matched=['一','下'] hit=2 就过闸了，而「一下」
       是她最常说的话；
    ② hit≥min_hits：多个词同时打中，噪声概率极低；
    ③ hit==1 时同样要满足①（单字唯一命中，例如「一」「我」，直接不递）。
    """
    if hit == 0:
        return False
    # 证据 = 至少一个多字命中（bigram/英文词有形状）；纯单字堆叠一律不算
    return sum(1 for t in matched if len(t) >= 2) >= LIMITS["min_multi_hits"]


def _rank(hit: int, front: dict) -> float:
    """排序分（可以吃 confidence/state/counter——它决定先后，不决定进不进目录）。
    cmb 夹具③：同等命中下 conf 高的排前面，conf=0 的卡照样过闸。
    ExpeL：counter 降序=注入序——同等证据命中下台账厚的排前面。intel 复审：
    封顶 20（+1.0）=白送一记命中且后续证据压不下去 → 封顶 6（+0.3），
    证据 hit 永远压过台账。"""
    try:
        counter = int(front.get("counter", 1) or 0)
    except Exception:
        counter = 1
    return round(hit + _STATE_BONUS.get(front.get("state", "candidate"), 0.0)
                 + float(front.get("confidence", 0) or 0)
                 + 0.05 * min(max(counter, 0), 6), 2)


def retrieve_skills(q: str, catalog, domain=None, top_k=None):
    """返回 {"refs": [...], "incomplete": bool, "dropped_weak": int,
    "conflict_pairs": [...], "pending_hidden": int, "generic_only": int,
    "total_active": int}。

    refs 已按 name 去重保最高分；deprecated 不进；pending_review 有命中也不进
    refs（只进冲突对，pending_hidden 计数）；过闸只吃命中证据（见 _passes），
    且命中里**全库通用 token（出现在 ≥df_cap 张卡）先剥离**——模板词「这张」
    级命中不算证据，剥完空的不进 dropped_weak（generic_only 单独计数，
    11-12 夜真库反事实实锤：聊天回合靠「这张」命中全卡再靠 confidence 瞎选）。
    dropped_weak = 有命中但没过闸的卡数（cmb 验收：它必须重新具备鉴别力）。
    total_active = 库里 active 卡总数——refs=∅ 时调用方据此发「另有 N 条」提示
    （少递但不瞒着她，cmb：否则「宁缺毋滥」等价于她永远不知道有这本错题本）。"""
    q_tokens = _tokens(q)
    total_active = len(catalog)
    by_name = {}
    for f in catalog:
        nm = f.get("name", "")
        if nm and nm not in by_name:
            by_name[nm] = f
    if not q_tokens:
        return {"refs": [], "incomplete": False, "dropped_weak": 0,
                "conflict_pairs": [], "pending_hidden": 0, "generic_only": 0,
                "total_active": total_active}
    scored, dropped_weak, pending_hidden, generic_only = [], 0, 0, 0
    df = _df_map(catalog)
    df_cap = _df_cap(total_active)
    pairable = []          # 进冲突对判定的卡：过闸 refs + 过闸 pending
    for f in catalog:
        hit, matched = _evidence(q_tokens, f, domain=domain)
        if hit == 0:
            continue
        # 通用 token 剥离（11-12 夜反事实实锤）：命中全由「这张」级模板词凑出
        # 的卡不算证据——剥完空了连 dropped_weak 都不进（纯聊天回合不该给
        # dropped_weak 灌水），单独计数 generic_only
        real = {t for t in matched if df.get(t, 0) < df_cap}
        if not real:
            generic_only += 1
            continue
        if not _passes(len(real), real):
            dropped_weak += 1
            continue
        if f.get("state") == "pending_review":
            pending_hidden += 1
            pairable.append(f)
            continue
        scored.append((_rank(len(real), f), f, len(real)))
        pairable.append(f)
    scored.sort(key=lambda x: (-x[0], x[1].get("name", "")))
    # 多层命中折一条（同 name 保最高分——scored 已排序，首个即最高）
    seen, refs = set(), []
    for s, f, hit in scored:
        nm = f.get("name", "")
        if nm in seen:
            continue
        seen.add(nm)
        refs.append({
            "name": nm, "tool_key": f.get("tool_key", ""), "state": f.get("state", ""),
            "confidence": f.get("confidence", 0), "version": f.get("version", 1),
            "when": (f.get("task_query") or f.get("description") or "")[:LIMITS["when_max_chars"]],
            "score": s, "hits": hit, "contradicts": f.get("contradicts") or [],
        })
    cap = top_k or LIMITS["max_inject"]
    incomplete = len(refs) > cap
    refs = refs[:cap]
    # conflictCard（MemU）：同 tool_key 且 contradicts 互指的两张卡成对呈现，
    # 不替模型裁决。11-12 起：①对手卡查全库（不要求它也过闸/进 refs——注入侧
    # 的 contradicts 是名字指针，对手在库里 active 就值得亮「有两版」）；
    # ②pending_review 卡参与配对（矛盾卡落 pending 后成对警告不许消失）；
    # ③deprecated 对手不算（降废的旧版不算悬案）。
    pairs, pair_seen = [], set()
    for f in pairable:
        a = f.get("name", "")
        for c in f.get("contradicts") or []:
            partner = by_name.get(c)
            if not partner or partner.get("state") not in ACTIVE_STATES:
                continue
            if partner.get("tool_key", "") != f.get("tool_key", ""):
                continue
            p = tuple(sorted([a, c]))
            if p not in pair_seen:
                pair_seen.add(p)
                pairs.append(list(p))
    return {"refs": refs, "incomplete": incomplete,
            "dropped_weak": dropped_weak, "conflict_pairs": pairs,
            "pending_hidden": pending_hidden, "generic_only": generic_only,
            "total_active": total_active}


def empty_hint(result) -> str:
    """refs=∅ 但库里确实有货时给一句话（少递但不瞒着）；库空则留空。

    调用方拿它当「另有 N 条经验技能可用，动手前先查一次」的落点——Suzu 强制检索
    的 +105 就靠这句把「不知道有错题本」变回「知道但不硬塞」。"""
    if result.get("refs") or not result.get("total_active"):
        return ""
    return "另有 %d 条经验技能可用；动手前先 expmem_search 查一次。" % result["total_active"]


def catalog_lines(result, token_budget=None) -> tuple:
    """两级降级成注入文本（OpenViking 预算循环，最坏有界、永不空手）。
    P2-4：返回 (text, level)，level ∈ 1=完整行/2=只留名字/3=单行退守——
    调用方据此知道 degradation 发生在哪一级，别让「降级」变成静默。"""
    budget = token_budget or LIMITS["catalog_token_budget"]
    refs = result.get("refs") or []
    if not refs:
        return "", 0
    def level1():
        lines = []
        for r in refs:
            lines.append("%s｜%s（%s v%s conf%.2f）" % (
                r["name"], r["when"], r["state"], r["version"], r["confidence"]))
        for a, b in result.get("conflict_pairs") or []:
            lines.append("⚠️ %s 与 %s 同键两版，未确认哪版为准" % (a, b))
        return "\n".join(lines)
    def level2():
        return "\n".join(r["name"] for r in refs)
    def level3():
        return "另有 %d 条经验技能可用；动手前先 expmem_search 查一次。" % len(refs)
    for i, cand in enumerate((level1(), level2(), level3()), start=1):
        if estimate_tokens(cand) <= budget:
            return cand, i
    return level3(), 3
