# -*- coding: utf-8 -*-
"""双向 orphan 审计（S2 收尾，双向 orphan 测试的真身）：

三向点得回是整条证据链的底线——
  card→action：卡 evidence 的 (episode, action) 两键都必须存在，且该 action
               确实属于那个 episode（跨集引用=编号漂移，比缺失更隐蔽）；
  episode→action：episode.actions 列表里的 id 必须能在 actions.jsonl 里对上；
  action→episode：actions.jsonl 里每条 owner 动作必须归属 ≥1 个 episode。
               turn 外发的 bash 目前会静默落在所有剧集之外——本计数就是
               这个静默类的观测位（replay 侧不修，先如实报数）。

审计是纯读函数；调用方决定 fail 还是计数。"""
import os


def orphan_report(cards=(), actions=(), episodes=()) -> dict:
    """cards=卡 front 列表（含 evidence）；actions/episodes = 两个 jsonl 的行。

    id 锚会随跨版本 replay 重编号（11-11 首跑实锤：gap 切分号级联改了全部
    episode id）——card evidence 带 (session, call_id, sub_idx) 稳定三元组时，
    先按 id，miss 再按三元组解析；都失败才算真孤儿。episode id 缺失但动作
    能解析 = stale_episode_id（良性：重编号，链仍在）；episode id 存在但
    不含该动作 = cross_episode（真漂移，单独一类）。"""
    act_by_id = {a.get("id"): a for a in (actions or [])}
    act_by_triple = {}
    for a in (actions or []):
        key = (a.get("session"), a.get("call_id"), a.get("sub_idx", 0))
        if a.get("call_id"):
            act_by_triple.setdefault(key, a)
    ep_by_id = {e.get("episode_id"): e for e in (episodes or [])}
    ep_ids_of_action = {}
    for e in (episodes or []):
        for aid in e.get("actions") or []:
            ep_ids_of_action.setdefault(aid, []).append(e.get("episode_id"))

    def _resolve_action(ev):
        aid = ev.get("action")
        if aid and aid in act_by_id:
            return act_by_id[aid]
        key = (ev.get("session"), ev.get("call_id"),
               ev.get("sub_idx", 0) if ev.get("sub_idx") is not None else 0)
        if ev.get("call_id"):
            return act_by_triple.get(key)
        return None

    ghost_action, ghost_episode, cross_episode, stale_episode = [], [], [], []
    for f in (cards or []):
        name = f.get("name", "?")
        for ev in f.get("evidence") or []:
            aid, eid = ev.get("action"), ev.get("episode")
            a = _resolve_action(ev)
            if a is None:
                ghost_action.append({"card": name, "action": aid})
                continue
            aid = a.get("id")
            if eid and eid in ep_by_id:
                if eid not in ep_ids_of_action.get(aid, []):
                    cross_episode.append({"card": name, "action": aid,
                                          "episode": eid,
                                          "in": ep_ids_of_action.get(aid, [])})
            elif eid:
                # episode id 不存在但动作解析成功：跨版本重编号的良性陈旧锚
                stale_episode.append({"card": name, "episode": eid,
                                      "resolved_action": aid})

    ep_ghost_action = []
    for e in (episodes or []):
        for aid in e.get("actions") or []:
            if aid not in act_by_id:
                ep_ghost_action.append({"episode": e.get("episode_id"),
                                        "action": aid})

    no_episode = [{"action": aid} for aid in act_by_id
                  if aid not in ep_ids_of_action]

    return {
        "card_ghost_action": ghost_action,
        "card_ghost_episode": ghost_episode,
        "card_cross_episode": cross_episode,
        "card_stale_episode_id": stale_episode,
        "episode_ghost_action": ep_ghost_action,
        "action_without_episode": no_episode,
        "counts": {
            "cards": len(list(cards or [])),
            "actions": len(act_by_id),
            "episodes": len(ep_by_id),
            "orphans_total": (len(ghost_action) + len(cross_episode)
                              + len(ep_ghost_action) + len(no_episode)),
        },
    }


def audit_dirs(cards_dir: str, actions_path: str, episodes_path: str) -> dict:
    """从目录/文件直接审计（cli/服务器脚本用）：cards_dir 扫 SKILL.md，
    actions/episodes 读 replay 产物 jsonl。"""
    import json

    from .retrieve import load_catalog

    def _jsonl(path):
        rows = []
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if ln:
                        try:
                            rows.append(json.loads(ln))
                        except Exception:
                            pass
        return rows

    fronts = []
    if os.path.isdir(cards_dir):
        for front in load_catalog(cards_dir):
            fronts.append(front)
        # deprecated/counter≤0 的卡不在 load_catalog 里，但 evidence 链仍要审计
        import glob
        from .skills import parse_skill
        for p in sorted(glob.glob(os.path.join(cards_dir, "**", "SKILL.md"),
                                  recursive=True)):
            try:
                with open(p, encoding="utf-8") as f:
                    front, _ = parse_skill(f.read())
            except Exception:
                continue
            if front not in fronts:
                fronts.append(front)
    return orphan_report(cards=fronts,
                         actions=_jsonl(actions_path),
                         episodes=_jsonl(episodes_path))
