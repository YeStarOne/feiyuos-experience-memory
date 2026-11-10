# -*- coding: utf-8 -*-
"""四个接口的唯一实现（方案 §2）。S0 只落 replay（采集），其余接口有签名有桩。

record_action / commit_episode —— replay 里真实在写（append-only）
retrieve_skills / record_skill_feedback —— S2/S3 才有真身，这里只留签名
"""
import hashlib
import json
import os

from .store import AppendOnly
from .sources.session_log import session_files, classify, iter_events
from .episode import build_session
from .bench import bench

ACTION_FIELDS = ("success", "failure", "unknown", "interrupted")


def _sha_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def replay(root: str, out_dir: str) -> dict:
    """只读重放：sessions root → actions.jsonl / episodes.jsonl / metrics.json。

    幂等：同目录重跑不产生重复行（AppendOnly 按 id 去重）。
    验收判据（方案 §4-S0）都落在 metrics 里。"""
    os.makedirs(out_dir, exist_ok=True)
    a_store = AppendOnly(os.path.join(out_dir, "actions.jsonl"))
    e_store = AppendOnly(os.path.join(out_dir, "episodes.jsonl"), key="episode_id")
    m = {
        "sessions_total": 0, "sessions_owner": 0, "sessions_automation": 0,
        "sessions_other_uuid": 0,
        "tool_calls_total": 0, "bash_calls": 0,
        "bash_with_sd": 0, "bash_with_sd_verb": 0,
        "sd_actions_total": 0, "sd_verb_actions_total": 0, "sd_other_actions_total": 0,
        "sd_raw_paths_total": 0, "sd_raw_actions_total": 0,
        "sd_in_automation": 0, "orphan_results_total": 0,
        "turns_nonautomation": 0, "game_turns": 0,
        "job_backgrounded_total": 0, "job_recovered_total": 0,
        "job_pending_total": 0, "job_killed_total": 0, "digest_added_total": 0,
        "game_day_ambiguous_total": 0,
        "actions_written": 0, "episodes_written": 0,
        "automation_rows_in_outputs": 0,
    }
    ep_rows, act_rows = [], []
    for name, path in session_files(root):
        cls = classify(name)
        m["sessions_total"] += 1
        m["sessions_" + {"owner": "owner", "automation": "automation",
                         "other-uuid": "other_uuid"}[cls]] += 1
        actions, episodes, st = build_session(iter_events(path), name)
        for k in ("tool_calls", "bash", "bash_with_sd", "bash_with_sd_verb",
                  "sd_verb_actions", "sd_other_actions", "sd_actions_total",
                  "sd_raw_paths", "sd_raw_actions", "sd_in_automation", "orphan_results",
                  "job_backgrounded", "job_recovered", "job_pending", "job_killed",
                  "digest_added", "game_day_ambiguous"):
            m[_MAP[k]] += st[k]
        if cls == "owner":
            m["turns_nonautomation"] += st["turns"]
            m["game_turns"] += st["game_turns"]
            for a in actions:
                row = a.row()
                act_rows.append(row)
                a_store.append(row)
            for e in episodes:
                e_store.append(e)  # build_session 返回的就是 row dict（已含 S1 outcome 分类）
                ep_rows.append(e)
            m["automation_rows_in_outputs"] += sum(
                1 for a in actions if classify(a.session) != "owner")
    a_store.close()
    e_store.close()
    if m["sessions_total"] == 0:
        # 空集不许当通过（11-11 实踩：root 指到 ~/.dsh/sessions，但真实结构还有一层
        # workspace 目录 → sessions_total=0、metrics 全 0、rc=0，一片假绿）
        raise ValueError(
            "replay 没找到任何会话文件：root 应指向 workspace 目录"
            "（~/.dsh/sessions/<--workspace-->），不是 ~/.dsh/sessions 本身")
    if m["sessions_owner"] == 0:
        raise ValueError("replay 找到 %d 个会话但 0 个属于她（owner 前缀不匹配）：%s"
                         % (m["sessions_total"], root))
    m["actions_written"] = len(a_store)
    m["episodes_written"] = len(e_store)
    # 守门断言落在**写出来的文件**上，不落在内存清单上（内存恒 0 没有鉴别力）
    with open(os.path.join(out_dir, "actions.jsonl"), encoding="utf-8") as f:
        m["automation_rows_in_outputs"] = sum(
            1 for line in f
            if classify(json.loads(line).get("session", "")) != "owner")
    m["sha_actions"] = _sha_file(os.path.join(out_dir, "actions.jsonl"))
    m["sha_episodes"] = _sha_file(os.path.join(out_dir, "episodes.jsonl"))
    with open(os.path.join(out_dir, "metrics.json"), "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, sort_keys=True, indent=1)
    b = bench(ep_rows, act_rows)
    with open(os.path.join(out_dir, "bench.json"), "w", encoding="utf-8") as f:
        json.dump(b, f, ensure_ascii=False, sort_keys=True, indent=1)
    m["bench"] = b
    return m


_MAP = {
    "tool_calls": "tool_calls_total",
    "bash": "bash_calls",
    "bash_with_sd": "bash_with_sd",
    "bash_with_sd_verb": "bash_with_sd_verb",
    "sd_verb_actions": "sd_verb_actions_total",
    "sd_other_actions": "sd_other_actions_total",
    "sd_actions_total": "sd_actions_total",
    "sd_raw_paths": "sd_raw_paths_total",
    "sd_raw_actions": "sd_raw_actions_total",
    "sd_in_automation": "sd_in_automation",
    "orphan_results": "orphan_results_total",
    "job_backgrounded": "job_backgrounded_total",
    "job_recovered": "job_recovered_total",
    "job_pending": "job_pending_total",
    "job_killed": "job_killed_total",
    "digest_added": "digest_added_total",
    "game_day_ambiguous": "game_day_ambiguous_total",
}


def record_action(act) -> str:
    """S3 前 live_tap 不存在；接口签名先冻结（方案 §2）。"""
    raise NotImplementedError("observe-only 阶段：采集只走 replay")


def commit_episode(ep) -> str:
    raise NotImplementedError("observe-only 阶段：剧集只走 replay")


def _skills_dir() -> str:
    import os as _os
    return _os.environ.get("EXPMEM_SKILLS_DIR") or _os.path.join(
        _os.path.expanduser("~"), ".dsh", "skills")


def retrieve_skills(query, top_k: int = None, domain: str = None) -> dict:
    """真身（S2 收尾）：扫 ~/.dsh/skills（EXPMEM_SKILLS_DIR 可覆盖）→ 检索。
    返回 {"refs","incomplete","dropped_weak","conflict_pairs","total_active",
    "lines","level","hint"}——lines/level 是 catalog_lines 的注入文本与降级级别，
    hint 是 refs=∅ 时的「另有 N 条」。"""
    from .retrieve import catalog_lines, empty_hint, load_catalog as _load
    from .retrieve import retrieve_skills as _rs
    result = _rs(query, _load(_skills_dir()), domain=domain, top_k=top_k)
    lines, level = catalog_lines(result)
    result["lines"], result["level"] = lines, level
    result["hint"] = empty_hint(result)
    return result


def record_skill_feedback(fb, shown_ids=()) -> dict:
    """真身（S2 收尾）：对 ~/.dsh/skills/<name>/SKILL.md 落 ExpeL 台账。
    A-MEM 三守卫在 feedback.record_feedback 里（shown_ids=本轮展示过的卡名）。
    只动台账不动正文——不触发 SkillWeaver 清零。返回 {"applied", "reason", "front"}。"""
    from . import feedback as _fb
    from .skills import load_skill, save_skill
    name = ""
    try:
        name = (_fb.parse_feedback(fb) or {}).get("name", "")
    except ValueError as e:
        return {"applied": False, "reason": "parse_rejected: %s" % e, "front": None}
    path = os.path.join(_skills_dir(), name, "SKILL.md")
    if not os.path.exists(path):
        return {"applied": False, "reason": "card_not_found: %s" % name, "front": None}
    front, body = load_skill(path)
    new_front, applied, reason = _fb.record_feedback(front, fb, shown_ids=shown_ids)
    if applied:
        save_skill(path, new_front, body)   # 正文原样；save_skill 再过一次正文闸
    return {"applied": applied, "reason": reason, "front": new_front}
