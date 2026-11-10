# -*- coding: utf-8 -*-
"""剧集切分：turn/start..turn/end 按文件顺序切（回合号重开会撞号，不按号配对）。

11-10 cmb 独立对账后的四条修正：
  D1 未知 verb 不丢：sd.py 有通用 cmd_action 分发器，词表开放——
     未知 token → verb="other:<token>"、endpoint="ACT <token>"，永不静默丢；
  G2 长回合切分：同一回合内相邻动作间隔 > EPISODE_GAP_S（600s）就切成
     多个 episode（她有 160 动作/6.5h 的轮询回合），episode_id 带分片号；
  G2 轮询重复：同 verb 同命令段间隔 ≤ POLL_WINDOW_S（30s）标 poll_repeat，
     保留在 actions 里（p50 照算），但 bench 的 calls-to-goal 剔除；
  G1 game_day 尽力回填：从本集内 state/farm 探针的 out_head 抓 day/season，
     抓不到就空着并如实报填充率（桥侧历史回填留给 S2+）。
另有 orphan_results 计数（压缩/中断后会出孤儿 result，容忍+计数，不当错误抛）。
"""
import hashlib
import json
import re

from .models import Action, Episode
from .adapters import stardew_sd as sd
from .sources.session_log import classify
from .outcome import classify_episode

_ONE_SPACE = re.compile(r"\s+")
EPISODE_GAP_S = 600          # cmb-G2：同一回合内相邻动作间隔超过 10 分钟就切
POLL_WINDOW_S = 30           # cmb-G2：轮询重复窗口
# cmb-G1 实测样式（sd.py cmd_state 146-149 行 + 4 条真实 out_head 核实）：
# 一行摘要 = `Beach(44,35) 冬25 winter 17:40 | 体力102/270 …` → 中英双语锚定
GAME_DAY_RE = re.compile(r"([春夏秋冬])(\d+)\s+(?:spring|summer|fall|winter)", re.I)
SEASON_JSON_RE = re.compile(r'"season"\s*:\s*"(spring|summer|fall|autumn|winter)"', re.I)
DOM_JSON_RE = re.compile(r'"(?:dayOfMonth|day)"\s*:\s*(\d+)')
SEASON_CN = {"spring": "春", "summer": "夏", "fall": "秋", "autumn": "秋", "winter": "冬"}


def _sha(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]


def _game_day(acts) -> str:
    """从本集探针 out_head 抓游戏日，存「冬25」样式（对齐手册 §9 双标）。

    优先双语锚定（一行摘要），JSON season/dayOfMonth 兜底；抓不到如实空着。"""
    for a in acts:
        h = getattr(a, "out_head", "") or ""
        if not h:
            continue
        m = GAME_DAY_RE.search(h)
        if m:
            return m.group(1) + m.group(2)
    for a in acts:
        h = getattr(a, "out_head", "") or ""
        if not h:
            continue
        ms = SEASON_JSON_RE.search(h)
        md = DOM_JSON_RE.search(h)
        if ms and md:
            return SEASON_CN[ms.group(1).lower()] + md.group(1)
    return ""


def build_session(events, session_name: str):
    """喂一个会话的事件流，返回 (actions, episodes, stats)。"""
    is_owner = classify(session_name) == "owner"
    stats = {
        "tool_calls": 0, "bash": 0, "bash_with_sd": 0, "bash_with_sd_verb": 0,
        "sd_verb_actions": 0, "sd_other_actions": 0, "sd_actions_total": 0,
        "sd_raw_paths": 0, "sd_raw_actions": 0, "sd_in_automation": 0,
        "turns": 0, "game_turns": 0, "gap_splits": 0, "orphan_results": 0,
    }
    actions, episodes = [], []
    calls = {}            # callId -> {"ts": ...}（配对用）
    pending = {}          # callId -> [Action]（result 回填用）
    open_turn = None      # 按文件顺序开合，不按回合号
    part = 0              # gap 切分的分片号

    def _close(turn_end_ts, verdict):
        nonlocal open_turn, part
        if open_turn is None:
            return
        acts = open_turn["acts"]
        ep = Episode(
            episode_id=_sha("%s|%s|%d" % (session_name, open_turn["seq"], part)),
            session=session_name, turn=open_turn["turn"],
            t_from=open_turn["t_from"], t_to=turn_end_ts,
            actions=[a.id for a in acts],
            verdict=verdict,
        )
        row = ep.row()
        row["act_count"] = len(acts)
        row["act_count_poll_free"] = sum(1 for a in acts if not a.poll_repeat)
        row["game"] = any(a.verb for a in acts)
        row["game_day"] = _game_day(acts)
        classify_episode(row, acts)  # S1：verdict/probe_status/failure_class
        episodes.append(row)
        if row["game"]:
            stats["game_turns"] += 1

    for ev in events:
        typ = ev.get("type")
        da = ev.get("data") or {}
        ts = ev.get("time", 0)

        if typ == "turn/start":
            stats["turns"] += 1
            if is_owner:
                part = 0
                open_turn = {"seq": ev.get("seq"), "turn": da.get("turn"),
                             "t_from": ts, "acts": [], "last": None}
        elif typ == "turn/end":
            if is_owner:
                reason = da.get("reason")
                kind = ""
                if isinstance(reason, dict):
                    kind = str(reason.get("kind", ""))
                elif isinstance(reason, str):
                    kind = reason
                verdict = "interrupted" if "interrupt" in kind.lower() else "unknown"
                _close(ts, verdict)
                open_turn = None
        elif typ == "tool/call":
            stats["tool_calls"] += 1
            name = da.get("name")
            try:
                args = json.loads(da.get("arguments") or "{}")
            except Exception:
                args = {}
            sd_info = sd.parse(args) if name == "bash" else {"verbs": [], "raw_paths": []}
            cmd = args.get("command") or ""
            if name == "bash":
                stats["bash"] += 1
                if sd_info["verbs"] or sd_info["raw_paths"]:
                    stats["bash_with_sd"] += 1
                    if not is_owner:
                        stats["sd_in_automation"] += len(sd_info["verbs"])
                if sd_info["verbs"]:
                    stats["bash_with_sd_verb"] += 1
            known = sum(1 for tok, _ in sd_info["verbs"] if tok in sd.VERBS)
            stats["sd_verb_actions"] += known
            stats["sd_other_actions"] += len(sd_info["verbs"]) - known
            stats["sd_actions_total"] += len(sd_info["verbs"])
            stats["sd_raw_paths"] += len(sd_info["raw_paths"])
            calls[da.get("callId")] = {"ts": ts}
            if is_owner and (sd_info["verbs"] or sd_info["raw_paths"]):
                mine = []
                for i, (tok, seg) in enumerate(sd_info["verbs"]):
                    verb = tok if tok in sd.VERBS else "other:" + tok
                    raw_path = ""
                    if tok == "raw":
                        m = sd.RAW_RE.search(seg)
                        raw_path = sd.clean_raw_path(m.group(1)) if m else ""
                    prev = open_turn["last"] if open_turn else None
                    a = Action(
                        id=_sha("%s|%s|%d" % (session_name, da.get("callId"), i)),
                        ts=ts, session=session_name,
                        turn=da.get("turn") or 0, step=da.get("step") or 0,
                        tool="bash", adapter=sd.NAME,
                        call_id=da.get("callId"), sub_idx=i,
                        verb=verb, raw_path=raw_path, segment=seg,
                        endpoint=("ACT " + tok) if verb.startswith("other:") else sd.endpoint_for(tok, raw_path),
                    )
                    # cmb-G2：轮询重复（同 verb 同命令段 ≤30s）
                    if (prev is not None and a.ts - prev.ts <= POLL_WINDOW_S * 1000
                            and a.verb == prev.verb and a.segment == prev.segment):
                        a.poll_repeat = True
                    # cmb-G2：间隔 > EPISODE_GAP_S 切分
                    if (prev is not None and a.ts - prev.ts > EPISODE_GAP_S * 1000):
                        _close(prev.ts, "unknown")
                        part += 1
                        stats["gap_splits"] += 1
                        open_turn = {"seq": open_turn["seq"], "turn": open_turn["turn"],
                                     "t_from": a.ts, "acts": [], "last": None}
                    actions.append(a)
                    mine.append(a)
                    if open_turn is not None:
                        open_turn["acts"].append(a)
                        open_turn["last"] = a
                # 锚定的 raw 子 shell 调用补成正式动作（去重：verb 扫描已发的 raw 跳过）
                raw_seen = {a.raw_path for a in mine if a.verb == "raw"}
                for path in sd.anchored_raw_paths(cmd):
                    if not path or path in raw_seen:
                        continue
                    raw_seen.add(path)
                    a = Action(
                        id=_sha("%s|%s|raw%d" % (session_name, da.get("callId"), len(mine))),
                        ts=ts, session=session_name,
                        turn=da.get("turn") or 0, step=da.get("step") or 0,
                        tool="bash", adapter=sd.NAME,
                        call_id=da.get("callId"), sub_idx=len(mine),
                        verb="raw", raw_path=path, segment="sd raw " + path,
                        endpoint="RAW " + path,
                    )
                    actions.append(a)
                    mine.append(a)
                    if open_turn is not None:
                        open_turn["acts"].append(a)
                        open_turn["last"] = a
                    stats["sd_actions_total"] += 1
                    stats["sd_raw_actions"] += 1
                pending[da.get("callId")] = mine
        elif typ == "tool/result":
            msg = da.get("message") or {}
            hit = calls.get(msg.get("toolCallId"))
            if hit is None:
                stats["orphan_results"] += 1   # 压缩/中断后会有孤儿 result，容忍+计数
                continue
            mine = pending.get(msg.get("toolCallId"))
            if not mine:
                continue
            err = bool(msg.get("isError"))
            dt = (ev.get("time", 0) - hit["ts"]) if hit["ts"] and ev.get("time") else -1
            txt = "".join(b.get("text", "") for b in (msg.get("content") or [])
                          if isinstance(b, dict))
            head = _ONE_SPACE.sub(" ", txt)[:160]
            osha = hashlib.sha1(txt.encode("utf-8")).hexdigest()[:16]
            for a in mine:
                a.is_error = err
                a.latency_ms = dt if dt >= 0 else -1
                a.out_head = head
                a.out_sha = osha
            del pending[msg.get("toolCallId")]
    return actions, episodes, stats
