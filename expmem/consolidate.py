# -*- coding: utf-8 -*-
"""consolidate（S2 v1 规则版）：从失败剧集扫出候选卡 spec（不写正文，正文留给
人工/LLM 审核——写入协议照 §7-9-4 delta ops，v1 只产 spec JSON）。

纪律（方案 §7-8-2 / §7-11-5）：
  - 拆优先于合：一个 (failure_class, tool_key) 一张 spec，绝不跨键合并；
  - evidence 必须点得回：cmd/out 直接取自 action 行本体（out_head/out_sha），
    episode_id 必须在 episodes 里存在——点不回就丢这条并计数，宁缺毋滥；
  - 触发信号=cmb 对齐④：机械 failure（noop/wrong_arg/blocked）即 candidate 草稿源；
  - AGPL 闸：产出的任何文本过一遍 BLOCKED_SUBSTRINGS（OpenViking 原文特征串，
    出现即拒收——机制可抄、措辞必须自写）。"""
import json
import os
import re

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
        out = _clean(a.get("out_head") or "")
        if cmd is None or out is None:
            dropped.append({"episode": e.get("episode_id"), "reason": "agpl_guard"})
            continue
        key = (fc, _tool_key(a))
        g = groups.setdefault(key, {
            "name": "stardew-%s-%s" % (fc, _tool_key(a)[len("stardew."):]),
            "tool_key": _tool_key(a), "failure_class": fc, "state": "candidate",
            "version": 1, "evidence": [], "stats": {"episodes": 0, "game_days": []},
        })
        g["evidence"].append({
            "episode": e.get("episode_id"), "action": aid,   # cmb-P2-6：点回 actions.jsonl 的锚
            "cmd": cmd[:120], "out": out[:120], "verdict": "failure",
        })
        g["stats"]["episodes"] += 1
        gd = e.get("game_day") or now_game_day
        if gd and gd not in g["stats"]["game_days"]:
            g["stats"]["game_days"].append(gd)
    specs = sorted(groups.values(), key=lambda g: (-g["stats"]["episodes"], g["name"]))
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
