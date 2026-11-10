# -*- coding: utf-8 -*-
"""S1 bench：Game Skill Benchmark 基线（改前先拍，改后同一把尺）。

分母守卫（cmb-B / SWE-bench 坑）：只有 probe_status=ok 且 verdict ∈
{success, failure} 的剧集进分母；interrupted / unavailable 一律排除；
空分母 → "denominator": "empty"，不许算出 100%/0% 的假数字。
success 判定需要 goal_hint（S2），本阶段 success_rate 恒为 null。
"""
import statistics


def bench(episodes, actions) -> dict:
    eps = episodes or []
    # 分母：probe ok 且 verdict ∈ {success,failure}，且不是 blocked——
    # blocked 不是她的错（cmb-A），单列在 failure_classes 里看
    judged = [e for e in eps
              if e.get("probe_status") == "ok"
              and e.get("verdict") in ("success", "failure")
              and e.get("failure_class") != "blocked"]
    interrupted = sum(1 for e in eps if e.get("verdict") == "interrupted")
    game_eps = [e for e in eps if e.get("game")]

    out = {
        "episodes_total": len(eps),
        "verdicts": _count(eps, "verdict"),
        "probe_status": _count(eps, "probe_status"),
        "failure_classes": _count([e for e in eps if e.get("failure_class")], "failure_class"),
        "denominator": len(judged) if judged else "empty",
        "success_rate": None,  # 需 S2 goal_hint，不许编
        "episodes_interrupted": interrupted,
        "game_episodes": len(game_eps),
    }
    if game_eps:
        acts = [e.get("act_count_poll_free", e.get("act_count", 0)) for e in game_eps]
        dur = [(e["t_to"] - e["t_from"]) / 1000.0 for e in game_eps
               if e.get("t_to") is not None and e.get("t_from") is not None]
        out["game_actions_per_episode"] = _p50_p90(acts)
        out["game_episode_seconds"] = _p50_p90(dur) if dur else None
    # cmb-G1：game_day 回填率（桥侧历史回填留给 S2+，这里如实报）
    out["game_day_filled"] = sum(1 for e in eps if e.get("game_day"))
    out["gap_splits"] = sum(1 for e in eps if e.get("outcome", {}).get("rule") == "gap_split") \
        if eps else 0

    # 工具层错误率（对照：isError 几乎没鉴别力）
    err = sum(1 for a in actions if a.get("is_error"))
    out["actions_total"] = len(actions)
    out["actions_tool_error"] = err

    # verdict 覆盖率：被机械规则判掉的剧集占比（S2 之后看它降多少）
    out["episodes_judged_failure"] = sum(1 for e in eps if e.get("verdict") == "failure")
    return out


def _count(rows, key):
    d = {}
    for r in rows:
        d[r.get(key, "")] = d.get(r.get(key, ""), 0) + 1
    return d


def _p50_p90(xs):
    xs = sorted(xs)
    if not xs:
        return None
    return {"p50": round(float(statistics.median(xs)), 2),
            "p90": round(xs[min(len(xs) - 1, int(len(xs) * 0.9))], 2)}
