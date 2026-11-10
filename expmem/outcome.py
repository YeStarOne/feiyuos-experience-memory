# -*- coding: utf-8 -*-
"""S1 outcome：给剧集判成败。observe-only——证据全部来自日志内已有的
`out_head`（sd 一行摘要 / 原始返回头 160 字），不碰桥、不碰她。

规则优先级（episode 级，先命中先判）：
  interrupted > blocked > 探针可用性 > wrong_arg(双倍扣钱) > noop(假成功) > unknown
  - blocked：连接层失败（refused/reset/curl 7|28|56/超时）——不需要读探针，直接判；
    不是她的错（cmb-A），bench 分母排除、单列计数
  - 探针可用性（cmb-B）：无任何带文本的读探针 → unavailable；探针活着但字段整片
    000（11-10 主持客户端卡死的真实形态 `farm_report:000 crop_seasons:000 …`）
    也算 unavailable——探针不可用 ≠ 失败，不许记成她的 noop
  - wrong_arg：/buy 不带 price 按售价×2 扣钱——只在证据齐全（买前后钱差 + 扣款额）
    时才判，缺一样就放弃（宁 unknown 不错判）
  - noop：`/store` 返 stored:[]；ready>0 而 收/收菜 0 件（day-loop 空转判据同源）

probe_status：ok | unavailable。unavailable → verdict=unknown，不进 bench 分母。
已知限制（S2 consolidator 接手）：stale / partial 需要跨动作语义，机械规则不做；
success 判定需要 goal_hint，本阶段一律 unknown——bench 的成功率先留 null。
"""
import re

BLOCKED_RE = re.compile(
    r"connection\s+(refused|reset)|ECONN|timed?\s*out|超时|curl:\s*\((7|28|56)\)", re.I)
DEAD_PROBE_RE = re.compile(r"[:=]000\b")     # 字段整片 000（≥2 处才算，1 处可能是巧合）
STORED_EMPTY_RE = re.compile(r"stored\"?\s*:\s*\[\s*\]")
HARVEST_ZERO_RE = re.compile(r"(收|harvest|collect)[^\d]{0,12}0|(?<!\d)0\s*(?:件|个|株)", re.I)
READY_RE = re.compile(r"[\"']?ready[\"']?\s*[:=]\s*(\d+)")
MONEY_RE = re.compile(r"(\d[\d,]*)\s*g\b")
CHARGE_JSON_RE = re.compile(r"\"(?:charge|cost|spent)\"\s*:\s*(\d+)")

PROBE_VERBS = frozenset("state inv near farm".split())
PROBE_RAW_PREFIX = ("/state", "/farm_report", "/inv", "/animals")


def is_probe(a) -> bool:
    return a.verb in PROBE_VERBS or a.raw_path.startswith(PROBE_RAW_PREFIX)


def _charge(h: str):
    m = CHARGE_JSON_RE.search(h)
    if m:
        return int(m.group(1))
    m = MONEY_RE.search(h)
    return int(m.group(1).replace(",", "")) if m else None


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

    # blocked：连接层失败，任何动作的返回都算数，不需要读探针
    for a, h in heads:
        if BLOCKED_RE.search(h):
            ep.update(verdict="failure", failure_class="blocked",
                      outcome={"rule": "blocked", "action": a.id, "signal": h[:80]})
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

    # wrong_arg：/buy 双倍扣钱（证据齐全才判）
    money = []
    for a, h in probes:
        m = MONEY_RE.search(h)
        if m:
            money.append((a.ts, int(m.group(1).replace(",", ""))))
    for a, h in heads:
        if a.verb != "buy" and a.raw_path != "/buy":
            continue
        charge = _charge(h)
        if charge is None:
            continue
        before = [v for t, v in money if t < a.ts]
        after = [v for t, v in money if t > a.ts]
        if before and after:
            delta = max(before) - min(after)
            if delta >= 1.8 * charge > 0:
                ep.update(verdict="failure", failure_class="wrong_arg",
                          outcome={"rule": "buy_double_charge", "action": a.id,
                                   "delta": delta, "charge": charge})
                return ep

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

    ep["verdict"] = "unknown"
    return ep
