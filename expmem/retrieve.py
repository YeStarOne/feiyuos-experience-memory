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
    'e/''nt''t/' 碎片乱匹配——路径分隔符两侧都必须切开）；CJK 串出
    **单字+bigram**（单字管召回，bigram 管精度），英文原样。"""
    out = set()
    for t in re.split(r"[\s，,。？！?!；;、（）()]+", (s or "").lower()):
        if not t:
            continue
        for sub in re.split(r"[-_./]+", t):
            if not sub:
                continue
            if _CJK_RE.search(sub):
                out.update(ch for ch in sub if ch not in STOPCHARS)  # 单字管召回（功能字除外）
                if len(sub) >= 2:
                    # bigram 管精度；但**纯功能字 bigram**（不是/是不/了么）不算证据
                    # ——cmb 实测：卡里写「不是」时 query「是不是」就会命中它
                    out.update(sub[i:i + 2] for i in range(len(sub) - 1)
                               if not all(ch in STOPCHARS for ch in sub[i:i + 2]))
            else:
                out.add(sub)
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
    cap = max(4, ceil(0.6N))——小语料（<7 张）cap>N 等于不剥（夹具/小目录
    向后兼容），8 张的现网目录剥掉「这张/失败」这类模板词。"""
    return max(4, math.ceil(0.6 * max(n, 1)))


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
