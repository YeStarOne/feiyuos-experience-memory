# -*- coding: utf-8 -*-
"""S1 outcome：给剧集判成败。observe-only——证据全部来自日志内已有的
`out_head`（sd 一行摘要 / 原始返回头 160 字），不碰桥、不碰她。

规则优先级（episode 级，先命中先判）：
  interrupted > blocked > 探针可用性 > noop(假成功) > unknown
  - blocked：连接层失败（refused/reset/curl 7|28|56/超时）——不需要读探针，直接判；
    不是她的错（cmb-A），bench 分母排除、单列计数
  - 探针可用性（cmb-B）：无任何带文本的读探针 → unavailable；探针活着但字段整片
    000（11-10 主持客户端卡死的真实形态 `farm_report:000 crop_seasons:000 …`）
    也算 unavailable——探针不可用 ≠ 失败，不许记成她的 noop
  - 余额差不判扣款（11-11 主人纠正）：星露谷钱包是共享的（主持那边也在赚/花），
    卖货的钱隔日才结算——「买前后余额差」不能当扣款证据，旧的 wrong_arg 双倍扣钱
    规则已退役；买只认返回 JSON 的 totalCost/charge（consolidate 成功轨同源）。
  - noop：`/store` 返 stored:[]；ready>0 而 收/收菜 0 件（day-loop 空转判据同源）
  - success（S4，保守机械版）：goal_hint 非空 ∧ 写动作带正效果证据（stored 非空/
    收获≥1）∧ 写后同回合有读探针——三条件闭环才判，缺一 unknown。

probe_status：ok | unavailable。unavailable → verdict=unknown，不进 bench 分母。
已知限制（S2 consolidator 接手）：stale / partial 需要跨动作语义，机械规则不做；
success 的 hint↔动作语义匹配（做的是不是被问的那件事）留给以后，机械版只认闭环。
"""
import re

BLOCKED_RE = re.compile(
    r"connection\s+(refused|reset)|ECONN|timed?\s*out|超时|curl:\s*\((7|28|56)\)", re.I)
BLOCKED_HINTS = ("sd", "stardew", "17844", "47844", "47870", "100.98", "60.204",
                 "curl", "tailnet")
DEAD_PROBE_RE = re.compile(r"[:=]000\b")     # 字段整片 000（≥2 处才算，1 处可能是巧合）
STORED_EMPTY_RE = re.compile(r"stored\"?\s*:\s*\[\s*\]")
HARVEST_ZERO_RE = re.compile(r"(收|harvest|collect)[^\d]{0,12}0|(?<!\d)0\s*(?:件|个|株)", re.I)
READY_RE = re.compile(r"[\"']?ready[\"']?\s*[:=]\s*(\d+)")
# 扣款只认 buy 返回 JSON 的显式字段；「钱142538」是共享钱包余额不是 charge
# （11-11 主人纠正：主持也赚/花、卖货隔日结算——余额差一律不作证据）
CHARGE_JSON_RE = re.compile(r"\"(?:charge|cost|spent|totalCost)\"\s*:\s*(\d+)", re.I)

# 判据版本（11-12 下一批）：v2 = S4 引入 success verdict（写效果+写后探针闭环）。
# api.replay 把它落进 out_dir/meta.json；版本不一致的重放直接拒绝——
# AppendOnly 只追加不改历史，混着 v1 行和 v2 行的数据集没法当同一把尺用。
OUTCOME_V = 2

PROBE_VERBS = frozenset("state inv near farm".split())
PROBE_RAW_PREFIX = ("/state", "/farm_report", "/inv", "/animals")

# S4 success 共享件（consolidate 从这里 import，防双定义漂移）
STORED_NONEMPTY_RE = re.compile(r"stored\"?\s*:\s*\[\s*[^ \]]")
HARVEST_COUNT_RE = re.compile(r"(?:收|harvest|collect)[^\d]{0,12}(\d+)", re.I)
WRITE_VERBS = frozenset("warp move act use water plant harvest dig sleep buy "
                        "sell key wait raw".split())


def is_probe(a) -> bool:
    return a.verb in PROBE_VERBS or a.raw_path.startswith(PROBE_RAW_PREFIX)


def _charge(h: str):
    m = CHARGE_JSON_RE.search(h)
    return int(m.group(1)) if m else None


def _match(heads, sig_re, keywords):
    """在命中信号的 heads 里挑归因动作：优先 segment 含关键词的那条。

    同一条 bash 链里所有动作共享同一段返回文本——11-11 实跑教训：stored:[]
    的归因被链首 warp 抢走，正确动作是后面的 /store。
    返回 (action, attribution)：segment 命中→"segment"（可炼卡）；
    退回首个命中→"fallback"（调用可能藏在 python heredoc 里，点不回 sd 动作，
    consolidate 按「宁缺毋滥」丢弃）。"""
    hits = [(a, h) for a, h in heads if sig_re.search(h)]
    for a, _ in hits:
        seg = (getattr(a, "segment", "") or "").lower()
        if any(k in seg for k in keywords):
            return a, "segment"
    return (hits[0][0], "fallback") if hits else (None, "fallback")


def classify_episode(ep: dict, actions) -> dict:
    """就地改 ep（row dict），返回它。actions 是本集的 Action 列表。"""
    if ep.get("verdict") == "interrupted":
        ep["probe_status"] = "unavailable"
        return ep

    heads = [(a, a.out_head) for a in actions if (getattr(a, "out_head", "") or "").strip()]

    # blocked：连接层失败（cmb-P2-8：也要归因——对别处 curl 超时不许整集判 blocked）
    hit, attr = _match(heads, BLOCKED_RE, BLOCKED_HINTS)
    if hit is not None and attr == "segment":
        ep.update(verdict="failure", failure_class="blocked",
                  outcome={"rule": "blocked", "action": hit.id,
                           "attribution": "segment",
                           "signal": (hit.out_head or "")[:80]})
        return ep

    probes = [(a, h) for a, h in heads if is_probe(a)]
    if not probes:
        ep["probe_status"] = "unavailable"
        ep["verdict"] = "unknown"
        return ep

    dead = sum(1 for _, h in probes if DEAD_PROBE_RE.search(h))
    if dead >= 2 or dead == len(probes):
        # 探针活着但字段整片 000：客户端层卡死（11-10 实录）——不是她的失败
        ep["probe_status"] = "unavailable"
        ep["verdict"] = "unknown"
        return ep
    ep["probe_status"] = "ok"

    # 11-11 起不再从余额差判扣款：钱包是共享的（主持也在赚/花），卖货隔日结算
    # → 余额变动无法归因到这一单。买只认 JSON totalCost/charge（consolidate 用）。

    # noop：假成功（归因动作优先 segment 含关键词者，见 _match）
    ready = None
    for a, h in probes:
        m = READY_RE.search(h)
        if m:
            v = int(m.group(1))
            ready = v if ready is None else max(ready, v)
    hit, attr = _match(heads, STORED_EMPTY_RE, ("store",))
    if hit is not None:
        ep.update(verdict="failure", failure_class="noop",
                  outcome={"rule": "stored_empty", "action": hit.id,
                           "attribution": attr,
                           "signal": (hit.out_head or "")[:80]})
        return ep
    if ready is not None and ready > 0:
        hit, attr = _match(heads, HARVEST_ZERO_RE, ("收", "harvest", "collect", "interact"))
        if hit is not None:
            ep.update(verdict="failure", failure_class="noop",
                      outcome={"rule": "harvest_zero_with_ready", "action": hit.id,
                               "attribution": attr,
                               "signal": (hit.out_head or "")[:80], "ready": ready})
            return ep

    # S4 success（保守机械版，宁空勿猜）：goal_hint 非空（本回合真有任务）
    # ∧ 写动作自带正效果证据（stored 非空 / 收获≥1）∧ 写后同回合有读探针——
    # 证据闭环才算 success；hint 与动作的语义匹配（她做的是不是被问的那件事）
    # 留给以后，机械版只认日志内的闭环证据，成功不许灌水。
    hint = (ep.get("goal_hint") or "").strip()
    if hint:
        for a, h in heads:
            if (getattr(a, "verb", "") or "") not in WRITE_VERBS:
                continue
            m = HARVEST_COUNT_RE.search(h)
            positive = (STORED_NONEMPTY_RE.search(h) is not None
                        or (m is not None and int(m.group(1)) >= 1))
            if not positive:
                continue
            if any(p.ts > a.ts for p, _ in probes):   # 写后有读探针=当场对账
                ep.update(verdict="success",
                          outcome={"rule": "write_effect_probe_closed",
                                   "action": a.id,
                                   "signal": h[:80]})
                return ep
            break          # 写动作没带后续探针：只试第一个带证据的写动作

    ep["verdict"] = "unknown"
    return ep
