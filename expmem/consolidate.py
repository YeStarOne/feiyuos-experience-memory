# -*- coding: utf-8 -*-
"""consolidate（S2 收尾，AWM 整份重写版）：从剧集归纳候选卡。

11-11 intel 四仓裁定落地：
  - **AWM 整份重写**：归纳 = 按意图（tool_key）聚合全部证据（失败轨+成功轨）
    每次全量重算整卡 spec——旧的「合并进旧卡 / merge 二审 / supersedes 提案」
    delta 机制整个删除（合并阈值问题被「重写」消解，不是被调参解决）；
  - **成功轨/失败轨两套卡段**：同一 tool_key 的成功证据（机械可验证效果）与
    失败证据落同一张卡的两个段；拆优先于合的纪律不变——不同 tool_key 永不合；
  - **ExpeL ADD 按子串去重**：新证据与既有 verified 卡的 evidence 文本互为
    子串（≥8字）= 同一经验的再次发生 → counter+1（确认），不产新 spec；
    candidate/observed 卡的重复证据只并池（未验证的文本不许被未验证的证据
    「确认」）；
  - evidence 必须点得回：cmd/out 直接取自 action 行本体，episode_id 必须在
    episodes 里存在——点不回就丢这条并计数，宁缺毋滥（原纪律不动）；
  - AGPL 闸：任何产出文本过 BLOCKED_SUBSTRINGS。
兼容：scan_candidates（失败轨草稿源）保留原样，induct_specs 内部复用它。"""
import json
import os
import re

from .outcome import _charge
from .skills import sanitize_for_body

# OpenViking 原文特征串（从其 prompts 的独有措辞取样；命中即拒收）。
# 维护约定：发现新的疑似原文特征串就追加到这里，grep 闸永不嫌多。
BLOCKED_SUBSTRINGS = (
    "context_type=\"skill\"",
    "viking://",
    "OPENVIKING_SKILL_CATALOG",
)


def _tool_key(action_row: dict) -> str:
    verb = action_row.get("verb") or ""
    if verb.startswith("other:"):
        return "stardew." + verb[len("other:"):]
    if verb and verb != "raw":
        return "stardew." + verb
    rp = action_row.get("raw_path") or ""
    if rp.startswith("RAW "):
        rp = rp[4:]
    head = rp.lstrip("/").split("/")[0] if rp else ""
    return "stardew." + (head or "unknown")


def _clean(s: str) -> str:
    return s if not any(b in s for b in BLOCKED_SUBSTRINGS) else None


def scan_candidates(episodes, actions, now_game_day=""):
    """episodes=episodes.jsonl 行；actions=actions.jsonl 行（按 call_id 索引过也行，这里自建）。

    返回 (specs, dropped)：spec={
      name, tool_key, failure_class, state:"candidate", version:1,
      evidence:[{episode, cmd, out, verdict:"failure"}], stats:{episodes, game_days}}
    """
    act_by_id = {a.get("id"): a for a in (actions or [])}
    ep_ids = {e.get("episode_id") for e in (episodes or [])}
    groups = {}
    dropped = []
    for e in (episodes or []):
        if e.get("verdict") != "failure":
            continue
        fc = e.get("failure_class") or ""
        if fc not in ("noop", "wrong_arg", "blocked"):
            continue  # stale/partial 留给 S2+；blocked 是环境错，单列不炼卡（§7-10-1）
        if fc == "blocked":
            continue
        aid = (e.get("outcome") or {}).get("action")
        if (e.get("outcome") or {}).get("attribution") != "segment":
            # 归因是 fallback（调用藏在 python heredoc 等，点不回 sd 动作）→ 宁缺毋滥
            dropped.append({"episode": e.get("episode_id"), "reason": "attribution_fallback"})
            continue
        a = act_by_id.get(aid)
        if a is None:
            dropped.append({"episode": e.get("episode_id"), "reason": "action_unresolvable"})
            continue
        if e.get("episode_id") not in ep_ids:
            dropped.append({"episode": e.get("episode_id"), "reason": "episode_missing"})
            continue
        cmd = _clean(a.get("segment") or "")
        # 证据优先用 outcome.signal（判定时命中的那 80 字），比 out_head 头 120 字
        # 更贴信号本体——首跑实锤：stored:[] 常在 JSON 中段，硬切头会切掉信号。
        out = _clean((e.get("outcome") or {}).get("signal")
                     or a.get("out_head") or "")
        if cmd is None or out is None:
            dropped.append({"episode": e.get("episode_id"), "reason": "agpl_guard"})
            continue
        key = (fc, _tool_key(a))
        g = groups.setdefault(key, {
            "name": "stardew-%s-%s" % (fc, _tool_key(a)[len("stardew."):]),
            "tool_key": _tool_key(a), "failure_class": fc, "state": "candidate",
            "version": 1, "evidence": [], "stats": {"rows": 0, "game_days": []},
        })
        g["evidence"].append({
            "episode": e.get("episode_id"), "action": aid,   # cmb-P2-6：点回 actions.jsonl 的锚
            # 11-11 首跑实测：episode/action id 在跨版本 replay 里会重编号
            # （gap 切分号变化级联），直接 id 锚会全部变孤儿——补稳定三元组
            # (session, call_id, sub_idx) 做真锚（id 只是快照）。
            "session": a.get("session") or "", "call_id": a.get("call_id") or "",
            "sub_idx": a.get("sub_idx", 0),
            "cmd": cmd[:120], "out": out[:120], "verdict": "failure",
        })
        g["stats"]["rows"] += 1
        gd = e.get("game_day") or now_game_day
        if gd and gd not in g["stats"]["game_days"]:
            g["stats"]["game_days"].append(gd)
    specs = sorted(groups.values(), key=lambda g: (-g["stats"]["rows"], g["name"]))
    return specs, dropped


def write_candidates(specs, dropped, out_path: str) -> int:
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        for s in specs:
            f.write(json.dumps(s, ensure_ascii=False, sort_keys=True) + "\n")
        if dropped:
            f.write(json.dumps({"_dropped": dropped}, ensure_ascii=False,
                               sort_keys=True) + "\n")
    return len(specs)


# ---------- 成功轨（机械可验证效果：outcome.py 三条失败规则的干净反演） ----------

STORED_NONEMPTY_RE = re.compile(r"stored\"?\s*:\s*\[\s*[^ \]]")
HARVEST_COUNT_RE = re.compile(r"(?:收|harvest|collect)[^\d]{0,12}(\d+)", re.I)
# 首跑实锤（11-11）：买对不能拿状态行前缀的「钱N」当扣款——那是余额不是 charge，
# 「Missing parameter: id」的失败买被误标成功。扣款只认 JSON 字段。
from .outcome import CHARGE_JSON_RE as _BUY_CHARGE_RE  # noqa: E402

# GMemory 阈值 5 起步的机械版（intel 11-11 交付）：证据太薄的键不开卡——
# 「卡册里没有含对账的成功轨之前，归纳机器不开工，避免空对空归纳」。
# 他们的单位是任务数（5），我们的是证据行数（3 行 ≈ 一次发生+一次复读+一次变体）。
MIN_SPEC_EVIDENCE = 3

# 首跑实锤（11-11）：同一条 bash 链里所有子动作共享同一段返回文本——读动作
# （inv/state）会「白捡」写动作的效果证据，把成功轨记到 stardew.inv 名下。
# 归因规则：同一 call 内优先写动作；call 内全是读才轮到读。
_WRITE_VERBS = frozenset("warp move act use water plant harvest dig sleep buy "
                         "sell key wait raw".split())


def scan_success(episodes, actions):
    """成功轨证据：效果能机械核实才收，宁可空轨不硬凑。
    - store 存进：out 含非空 stored:[…]（stored_empty 的反演）
    - 收到：收/harvest/collect 后跟 ≥1（harvest_zero_with_ready 的反演）
    - 买对：buy 输出里 JSON 扣款字段 totalCost/charge（buy_double_charge 的反演；
      状态行前缀的「钱N」是余额不是扣款，绝不采）
    成功轨同样要求动作点得回（episode 里实际执行的那条 action 行）。"""
    act_by_id = {a.get("id"): a for a in (actions or [])}
    cand = []           # (session, call_id) -> 首个匹配（含动词信息）
    rows = []
    for e in (episodes or []):
        if e.get("verdict") == "failure":
            continue            # 失败集（含 blocked/wrong_arg）不进成功轨
        for aid in e.get("actions") or []:
            a = act_by_id.get(aid)
            if a is None or not (a.get("out_head") or "").strip():
                continue
            h = a.get("out_head") or ""
            rule = ""
            if STORED_NONEMPTY_RE.search(h):
                rule = "stored_nonempty"
            else:
                m = HARVEST_COUNT_RE.search(h)
                if m and int(m.group(1)) >= 1:
                    rule = "harvest_count_%s" % m.group(1)
                elif (a.get("verb") == "buy" or a.get("raw_path") == "/buy"):
                    mc = _BUY_CHARGE_RE.search(h)
                    if mc:
                        rule = "buy_charged_%s" % mc.group(1)
            if rule:
                rows.append({"episode": e.get("episode_id"), "action": aid,
                             "session": a.get("session") or "",
                             "call_id": a.get("call_id") or "",
                             "sub_idx": a.get("sub_idx", 0),
                             "verb": a.get("verb") or "",
                             "is_write": (a.get("verb") or "") in _WRITE_VERBS,
                             "cmd": a.get("segment") or "", "out": h,
                             "verdict": "success", "rule": rule,
                             "game_day": e.get("game_day") or ""})
    # 同 call 归因：有写动作时读动作让位（写动作是效果的制造者）
    by_call = {}
    for r in rows:
        by_call.setdefault((r["session"], r["call_id"]), []).append(r)
    out = []
    for group in by_call.values():
        writes = [r for r in group if r["is_write"]]
        keep = writes if writes else group
        out.extend(keep)
    return out


# ---------- AWM 整份重写归纳 ----------

def _spec_name(tool_key: str) -> str:
    return "stardew-" + tool_key[len("stardew."):].replace("_", "-")


def template_body(spec: dict) -> str:
    """两套卡段模板：什么时候用 / 成功轨 / 失败轨。
    cmd 进正文前 sanitize（具体坐标/金额→占位符；数值本体留在 frontmatter
    evidence 里点回 actions.jsonl）。out 不进正文。"""
    ok_lines, fail_lines = [], []
    for ev in spec.get("evidence") or []:
        cmd = sanitize_for_body(_clean(ev.get("cmd") or "") or "")
        if not cmd:
            continue
        if ev.get("verdict") == "success":
            ok_lines.append("- `%s` → 效果已核（%s）" % (cmd, ev.get("rule", "")))
        else:
            fail_lines.append("- `%s` → 失败形态（%s）"
                              % (cmd, ev.get("failure_class") or "failure"))
    head = spec.get("tool_key", "")
    lines = ["# %s 的动手卡" % head, "",
             "## 什么时候用", spec.get("task_query") or ("要对 %s 的结果负责时" % head), ""]
    lines.append("## 成功轨")
    lines.extend(ok_lines or ["（暂无成功轨证据——本卡目前只有失败形态）"])
    lines.append("")
    lines.append("## 失败轨")
    lines.extend(fail_lines or ["（暂无失败轨证据——本卡目前只有成功形态）"])
    lines.append("")
    lines.append("具体数值与完整输出一律见 frontmatter evidence（点回 actions.jsonl），"
                 "正文只写规则不抄数值。")
    return "\n".join(lines)


def _norm_text(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").lower())


def dedupe_add(evidence_rows, existing_fronts, min_frag: int = 8):
    """ExpeL ADD 按子串去重（失败轨+成功轨证据都过这道）。对每条新证据找既有卡
    的 evidence 文本（cmd/out）互为子串（较短方 ≥min_frag 字）者：
      - verified 卡 → counter+1（同一经验的再次发生=确认；不改内容、不触发清零）
      - candidate/observed 卡 → pooled（重复证据不进新 spec——重写只会产出同样
        的内容；不许「未验证确认未验证」）
      - 无匹配 → 该证据照常进归纳池
    返回 (confirmed: {name: n}, pooled_names: set, fresh: [未命中的证据行])"""
    confirmed, pooled = {}, set()
    fresh = []
    fronts = [f for f in (existing_fronts or []) if isinstance(f, dict)]
    for row in evidence_rows or []:
        frag = _norm_text((row.get("cmd") or "") + " " + (row.get("out") or ""))
        hit = None
        for f in fronts:
            for ev in f.get("evidence") or []:
                base = _norm_text((ev.get("cmd") or "") + " " + (ev.get("out") or ""))
                if not base or not frag:
                    continue
                shorter = frag if len(frag) <= len(base) else base
                if len(shorter) >= min_frag and (frag in base or base in frag):
                    hit = f
                    break
            if hit is not None:
                break
        if hit is None:
            fresh.append(row)
        elif hit.get("state") == "verified":
            confirmed[hit.get("name", "")] = confirmed.get(hit.get("name", ""), 0) + 1
        else:
            pooled.add(hit.get("name", ""))
    return confirmed, pooled, fresh


_TASK_QUERY = {
    "stardew.state": "写操作后同回合核对结果",
    "stardew.store": "存仓库后当场确认 stored 非空",
}


def _mechanical_anchor(tool_key: str, evs) -> str:
    """机械 anchor：tool_key 各段 + 证据 cmd 里的英文 token。中文同义词
    （存仓库/收菜…）机械方法造不出来——candidate 卡的 anchor 由后续人工/LLM
    重写补全（那是一次内容改动，SkillWeaver 清零自动生效）。"""
    toks = set()
    for ev in evs or []:
        for t in re.findall(r"[a-z_]{2,}", (ev.get("cmd") or "").lower()):
            toks.add(t)
    toks.add(tool_key[len("stardew."):])
    parts = tool_key.split(".")
    toks.update(p for p in parts if p and p != "stardew")
    return " ".join(sorted(toks))[:300]


def induct_specs(episodes, actions, existing_fronts=None, now_game_day=""):
    """AWM 整份重写版归纳：返回 (specs, ledger_ops, dropped)。

    specs = 按 tool_key 聚合的整卡 spec（成功轨+失败轨两套卡段、模板正文、
    机械 anchor/description）。ledger_ops = {"confirmed": {name: n},
    "pooled": [names], "held_thin": n}——调用方把 confirmed 套 ExpeL 台账
    （verified 卡 counter+1），held_thin 是 GMemory 薄池闸拦下的证据行数。
    dropped 口径与 scan_candidates 一致。"""
    fail_specs, dropped = scan_candidates(episodes, actions, now_game_day=now_game_day)
    # 失败轨证据摊平后与成功轨一起过 ExpeL ADD 去重
    fail_rows = []
    for s in fail_specs:
        for ev in s["evidence"]:
            fail_rows.append(dict(ev, failure_class=s["failure_class"],
                                  tool_key=s["tool_key"]))
    ok_rows = scan_success(episodes, actions)
    conf_f, pooled_f, fail_fresh = dedupe_add(fail_rows, existing_fronts)
    conf_o, pooled_o, ok_fresh = dedupe_add(ok_rows, existing_fronts)
    confirmed = dict(conf_f)
    for name, n in conf_o.items():
        confirmed[name] = confirmed.get(name, 0) + n
    pooled = set(pooled_f) | set(pooled_o)

    by_key = {}
    act_by_id = {a.get("id"): a for a in (actions or [])}
    for r in fail_fresh:
        g = by_key.setdefault(r["tool_key"], {"fail": [], "ok": []})
        g["fail"].append(r)
    for r in ok_fresh:
        a = act_by_id.get(r.get("action"))
        if a is None:
            dropped.append({"episode": r.get("episode"),
                            "reason": "success_action_unresolvable"})
            continue
        g = by_key.setdefault(_tool_key(a), {"fail": [], "ok": []})
        g["ok"].append(r)

    specs = []
    held_thin = 0
    for k, g in sorted(by_key.items()):
        total = len(g["fail"]) + len(g["ok"])
        # intel 复审（11-11）：薄池闸的单位是「发生数」不是「证据行」——同一回合
        # 重试 3 次 ≠ 三次发生。GMemory 口径=任务数；我们的机械等价=
        # 证据行 ≥MIN_SPEC_EVIDENCE **且** 至少跨 2 个不同剧集/游戏日。
        eps_seen, days_seen = set(), set()
        for r in g["fail"] + g["ok"]:
            if r.get("episode"):
                eps_seen.add(r["episode"])
            if r.get("game_day"):
                days_seen.add(r["game_day"])
        if (total < MIN_SPEC_EVIDENCE
                or (len(eps_seen) < 2 and len(days_seen) < 2)):
            held_thin += total         # GMemory 薄池闸：宁不开卡，不空对空
            continue
        evs, days = [], []
        n_fail = n_ok = 0
        fcs = {}
        for r in g["fail"]:
            evs.append({"episode": r.get("episode"), "action": r.get("action"),
                        "session": r.get("session") or "",
                        "call_id": r.get("call_id") or "",
                        "sub_idx": r.get("sub_idx", 0),
                        "cmd": (r.get("cmd") or "")[:120],
                        "out": (r.get("out") or "")[:120],
                        "verdict": "failure", "failure_class": r["failure_class"]})
            n_fail += 1
            fcs[r["failure_class"]] = fcs.get(r["failure_class"], 0) + 1
            gd = ""
            for e in (episodes or []):
                if e.get("episode_id") == r.get("episode"):
                    gd = e.get("game_day") or ""
                    break
            if gd and gd not in days:
                days.append(gd)
        for r in g["ok"]:
            evs.append({"episode": r["episode"], "action": r["action"],
                        "session": r.get("session") or "",
                        "call_id": r.get("call_id") or "",
                        "sub_idx": r.get("sub_idx", 0),
                        "cmd": r["cmd"][:120], "out": r["out"][:120],
                        "verdict": "success", "rule": r["rule"]})
            n_ok += 1
            gd = r.get("game_day")
            if gd and gd not in days:
                days.append(gd)
        dom_fc = max(fcs, key=fcs.get) if fcs else ""
        spec = {
            "name": _spec_name(k), "tool_key": k, "state": "candidate",
            "version": 1,
            "description": "星露谷 %s：失败 %d 例 / 成功 %d 例%s" % (
                k[len("stardew."):], n_fail, n_ok,
                ("，主失败形态 %s" % dom_fc) if dom_fc else ""),
            "retrieval_anchor": _mechanical_anchor(k, evs),
            "task_query": _TASK_QUERY.get(k, "对 %s 的结果负责时先查这张卡" % k),
            "evidence": evs,
            "stats": {"rows": total, "episodes": len(eps_seen), "days": len(days_seen),
                      "failure": n_fail, "success": n_ok,
                      "game_days": days, "failure_class": dom_fc},
        }
        spec["body"] = template_body(spec)
        specs.append(spec)
    # confirmed 的键序对齐 confirmed 变量（失败轨去重的确认在 conf_f 里——合并）
    return specs, {"confirmed": confirmed, "pooled": sorted(pooled),
                   "held_thin": held_thin}, dropped
