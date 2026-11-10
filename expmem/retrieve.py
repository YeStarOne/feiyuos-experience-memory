# -*- coding: utf-8 -*-
"""retrieve_skills 实现（S2）：词法 + 元数据，第 0 级阶梯（方案 §7-7/7-10/7-11）。

三闸（OpenViking 对齐）：
  ① 硬阈值——低于不注入，weak hit 宁可不递（Voyager 无阈值的教训）；
  ② 不静默截断——候选超预算走**两级降级**（带 when → 只留名字 → 退单行），
     最坏有界、永不空手；调用方拿 incomplete 标志自行选级；
  ③ 多层命中折一条（按 name 去重保最高分）。
呈现（MemU 对齐）：
  - 只检 active（candidate/observed/verified/pending_review）；**deprecated 不进召回、
    不挡新卡**（门语料口径）；
  - 同 tool_key 互指矛盾的两张卡**成对呈现**+「未确认哪版为准」，不替模型裁决；
  - domain 缺省=通用放行（向后兼容）；跨域默认过滤、显式点名放行。
包装语纪律（Suzu/neo4j 对齐）：目录行是「回忆依据不是命令」；追问只接受本轮
已交付的 name；「摘要待核，未提及≠不存在」由调用方话术层承担，本模块不掺。
"""
import glob
import json
import os
import re

# 预算 frozen 常量（方案 §7-11-6），不散落魔法数
LIMITS = {
    "max_inject": 3,          # 目录行最多几条
    "when_max_chars": 80,     # 单行 when 截断
    "catalog_token_budget": 600,  # 注入 token 预算（实测调）
    "score_threshold": 1.0,   # 硬阈值（cmb-P1-3：原 2.0 把 candidate 全拦死）
}
ACTIVE_STATES = ("candidate", "observed", "verified", "pending_review")
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

    修法（Suzu 同款加宽）：复合 token 按 [-_.] 再拆一层；CJK 串出**单字+bigram**
    （单字管召回，bigram 管精度），英文原样。"""
    out = set()
    for t in re.split(r"[\s，,。？！?!；;、（）()]+", (s or "").lower()):
        if not t:
            continue
        for sub in re.split(r"[-_.]+", t):
            if not sub:
                continue
            if _CJK_RE.search(sub):
                out.update(ch for ch in sub if ch not in STOPCHARS)  # 单字管召回（功能字除外）
                if len(sub) >= 2:
                    out.update(sub[i:i + 2] for i in range(len(sub) - 1))  # bigram 管精度  # bigram 管精度
            else:
                out.add(sub)
    return out


def load_catalog(skills_dir: str):
    """扫 SKILL.md / *.md 的 frontmatter，返回 active 卡列表（deprecated 不进）。"""
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
        front["_path"] = p
        fronts.append(front)
    return fronts


def _score(q_tokens: set, front: dict, domain=None) -> float:
    hay = " ".join([front.get("retrieval_anchor", ""), front.get("task_query", ""),
                    front.get("name", ""), front.get("tool_key", ""),
                    front.get("description", "")])
    hit = len(q_tokens & _tokens(hay))
    if hit == 0:
        return 0.0
    matched = q_tokens & _tokens(hay)
    if hit == 1 and len(next(iter(matched))) == 1:
        # intel-③：唯一命中是一个单字 → 宁可不递（「一」「我」这类功能字
        # 在任何中文卡里都可能出现，单字命中不构成「相关」的证据）
        return 0.0
    score = hit + _STATE_BONUS.get(front.get("state", "candidate"), 0.0)
    if hit >= 2:
        # intel：confidence 只在 hit≥2 时计入——hit=1 时 0.55 的 conf 会把
        # 一个功能字命中的噪声顶过闸（1.0 阈值形同虚设）
        score += float(front.get("confidence", 0))
    d = front.get("domain")
    if domain and d and d != domain:
        return 0.0  # 跨域默认过滤（显式点名时由调用方换 domain 参数）
    return round(score, 2)


def retrieve_skills(q: str, catalog, domain=None, top_k=None):
    """返回 {"refs": [...], "incomplete": bool, "dropped_weak": int, "conflict_pairs": [...]}。

    refs 已按 name 去重保最高分；deprecated 不进；低于硬阈值不递。"""
    q_tokens = _tokens(q)
    if not q_tokens:
        return {"refs": [], "incomplete": False, "dropped_weak": 0, "conflict_pairs": []}
    scored = []
    for f in catalog:
        s = _score(q_tokens, f, domain=domain)
        if s >= LIMITS["score_threshold"]:
            scored.append((s, f))
    scored.sort(key=lambda x: (-x[0], x[1].get("name", "")))
    dropped_weak = sum(1 for f in catalog if 0 < _score(q_tokens, f, domain=domain)
                       < LIMITS["score_threshold"])
    # 多层命中折一条（同 name 保最高分——scored 已排序，首个即最高）
    seen, refs = set(), []
    for s, f in scored:
        nm = f.get("name", "")
        if nm in seen:
            continue
        seen.add(nm)
        refs.append({
            "name": nm, "tool_key": f.get("tool_key", ""), "state": f.get("state", ""),
            "confidence": f.get("confidence", 0), "version": f.get("version", 1),
            "when": (f.get("task_query") or f.get("description") or "")[:LIMITS["when_max_chars"]],
            "score": s, "contradicts": f.get("contradicts") or [],
        })
    cap = top_k or LIMITS["max_inject"]
    incomplete = len(refs) > cap
    refs = refs[:cap]
    # conflictCard（MemU）：同 tool_key 且 contradicts 互指的两张卡成对呈现，
    # 不替模型裁决；配对只在两张都进了本轮 cap 时成立（被 cap 截掉的单边不标）。
    by_name = {r["name"]: r for r in refs}
    pairs = []
    for a in refs:
        for c in a["contradicts"]:
            b = by_name.get(c)
            if b and b["tool_key"] == a["tool_key"]:
                p = sorted([a["name"], b["name"]])
                if p not in pairs:
                    pairs.append(p)
    return {"refs": refs, "incomplete": incomplete,
            "dropped_weak": dropped_weak, "conflict_pairs": pairs}


def catalog_lines(result, token_budget=None) -> str:
    """两级降级成注入文本（OpenViking 预算循环，最坏有界、永不空手）。"""
    budget = token_budget or LIMITS["catalog_token_budget"]
    refs = result.get("refs") or []
    if not refs:
        return ""
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
    for cand in (level1(), level2(), level3()):
        if estimate_tokens(cand) <= budget:
            return cand
    return level3()
