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
# cmb-D（11-11 复核推翻旧天花板）：49% 的 state 读是短格式「Mountain(42,11) 春16 14:20」，
# 必选英文季后缀把它们全漏了 → 天花板实为 4.6%（12→62）。
# 但无锚宽正则扫全动作文本会吃进非日期数字（首跑实测 fill=47/ambig=27，偏离
# 对账锚 61/14）——cmb 锚定复算证明 `\(\d+,\d+\) 季N` 天花板不变（62），故落
# **锚定版**：(x,y) 前缀 + 中文季日；多日歧义按既定规则弃（宁空勿猜）。
# S4 goal_hint：回合的用户指令——生产 user/message 正文在 content[0].text，
# 真话在 <user_message qq=…>…</user_message> 标签里（其余全是记忆回灌/时间
# 快照/日程等系统注入）；没有标签时退回剥目录块后的全文（RN 信等）。
_CATALOG_BLOCK_RE = re.compile(r"<available_skills>.*?</available_skills>", re.S)
_USER_MSG_RE = re.compile(r"<user_message[^>]*>(.*?)</user_message>", re.S)


def _hint_text(da) -> str:
    text = da.get("text") or ""
    if not text or not isinstance(text, str):
        c = da.get("content")
        if isinstance(c, list):
            text = "".join(x.get("text", "") for x in c
                           if isinstance(x, dict))
    text = str(text)
    m = _USER_MSG_RE.search(text)
    if m:
        text = m.group(1)
    else:
        text = _CATALOG_BLOCK_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:120]


GAME_DAY_RE = re.compile(
    r"[（(]\s*\d{1,4}\s*,\s*-?\d{1,4}\s*[）)]\s*([春夏秋冬])(\d+)")
SEASON_JSON_RE = re.compile(r'"season"\s*:\s*"(spring|summer|fall|autumn|winter)"', re.I)
DOM_JSON_RE = re.compile(r'"(?:dayOfMonth|day)"\s*:\s*(\d+)')
SEASON_CN = {"spring": "春", "summer": "夏", "fall": "秋", "autumn": "秋", "winter": "冬"}

# S3 前采集修定（cmb 定的天花板杠杆，11-11 实测）：
#  ① 后台化 >60s 的 bash 结果文本只是通知（`[still running after 60000ms; moved to
#     background job bash-xxxx]`），真终态要靠之后的 job_output（309 个后台 job 里
#     300 个被她自己捞回来了）。会话日志里有 job/registered{jobId,toolCall.callId}
#     → 精确 join，别猜 id。
#  ② JSON /state 的 time/season 段常被 160 字头截掉（实测 205 条 JSON 读里 5 条
#     game_day 只在 160 字之后）→ 头之外再附一份「关注键摘要」，格式仍是合法 JSON
#     片段，现有 SEASON_JSON_RE/DOM_JSON_RE 直接命中。
BG_NOTICE_RE = re.compile(r"background job (bash-[0-9a-f]+)")
JOB_TAIL_RE = re.compile(r"\[status:\s*([a-z_]+),\s*exit code:\s*(-?\d+)\]")
DIGEST_KEYS = ("season", "dayOfMonth", "day", "time", "gold", "money", "energy",
               "stamina", "ready", "stored", "totalCost", "remainingGold", "charge")


def _sha(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]


# game_day 只信 state 类读（farm/shot 的输出里日期混着别的数字，见 _game_day 注释）
STATE_READ_VERBS = frozenset("state inv near".split())
STATE_READ_RAW = ("/state", "/inv")


def _is_state_read(a) -> bool:
    rp = getattr(a, "raw_path", "") or ""
    return (getattr(a, "verb", "") or "") in STATE_READ_VERBS \
        or rp.startswith(STATE_READ_RAW)


def _digest(txt: str, limit: int = 90) -> str:
    """把 JSON 结果里我们关心的键压成一段**合法 JSON 片段**（喂给 out_head 尾部）。

    为什么需要：`sd raw /state` 的 season/dayOfMonth/time 常落在 160 字之后，
    只截头就把游戏日/时间整段丢了（采集层天花板）。返回 ""=不是 JSON 或没关心键。"""
    obj = None
    s = (txt or "").strip()
    if s[:1] not in "{[":
        return ""      # 散文里出现的花括号不是 JSON 输出（别给散文造假摘要）
    try:
        obj = json.loads(s)
    except Exception:
        # JSON + 尾巴（job_output 的 `[status: completed, exit code: 0]`）
        i, j = s.find("{"), s.rfind("}")
        if 0 <= i < j:
            try:
                obj = json.loads(s[i:j + 1])
            except Exception:
                obj = None
    if not isinstance(obj, dict):
        return ""
    found = {}

    def walk(o, depth=0):
        if depth > 4 or not isinstance(o, dict):
            return
        for k, v in o.items():
            if k in DIGEST_KEYS and k not in found and isinstance(v, (int, float, str, bool, list)):
                found[k] = v
            elif isinstance(v, dict):
                walk(v, depth + 1)

    walk(obj)
    if not found:
        return ""
    frag = json.dumps(found, ensure_ascii=False, separators=(",", ":"))
    return frag if len(frag) <= limit else frag[:limit]


def _head(txt: str, limit: int = 160) -> str:
    """结果文本头（空白折叠 + 截断）+ 关注键摘要。摘要在截断之后拼，永不被截掉。"""
    flat = _ONE_SPACE.sub(" ", txt or "").strip()
    digest = _digest(txt)
    return (flat[:limit] + (" ⟨%s⟩" % digest if digest else ""))[:limit + 240]


def _game_day(acts, ambig_seen=None, ep_key="", t_from=0, t_to=0) -> str:
    """从本集探针 out_head 抓游戏日，存「冬25」样式（对齐手册 §9 双标）。

    只看 state 类读的动作（farm/shot 的输出里日期混着别的数字）。
    采信判据拿**事件时间**判，不拿来源判（cmb finishedAt 实锤 11-11：按来源
    一刀切和「唯一证据例外」各错一边——job 文本当且仅当 finishedAt∈窗口才
    采信；窗口内完成的 job 引入第二日=真跨日窗口→歧义，不许用回填把跨日
    窗口填成假单日）。JSON 摘要兜底走同一条时间规则（它曾完全不设限，是
    P2 的真残留缝）。ambig_seen 是集合：同集重复判不重复计。"""
    def _admits(a):
        if getattr(a, "out_source", "") != "job_output":
            return True
        done = getattr(a, "job_done_ts", None)
        if not done or not t_from or not t_to:
            return False                 # 拿不到完成时刻=不可采信（宁空勿猜）
        return t_from <= done <= t_to

    days = []
    for a in acts:
        if not _is_state_read(a) or not _admits(a):
            continue
        h = getattr(a, "out_head", "") or ""
        if not h:
            continue
        for m in GAME_DAY_RE.finditer(h):
            d = m.group(1) + m.group(2)
            if d not in days:
                days.append(d)
    if len(days) == 1:
        return days[0]
    if len(days) > 1:
        if ambig_seen is not None:
            ambig_seen.add(ep_key)        # 独立集数：同集重复判不重复计
        return ""          # 跨日窗口：弃
    for a in acts:
        if not _admits(a):
            continue
        h = getattr(a, "out_head", "") or ""
        if not h:
            continue
        ms = SEASON_JSON_RE.search(h)
        md = DOM_JSON_RE.search(h)
        if ms and md:
            return SEASON_CN[ms.group(1).lower()] + md.group(1)
    return ""


def _apply_job_outputs(actions, episodes, job_bind, bg_calls, job_out,
                       job_settled, stats, ambig_seen, job_settled_ts) -> int:
    """把 job_output 的终态文本回填到**被后台化**的动作，并重判受影响的剧集。

    只回填终态（completed）：running 的文本是半截，killed 的会被她中途打断
    （partial 归 S2 语义层），宁可留着「输出不可用」也别把半截当证据。
    注意 job_bind 覆盖**所有** bash（每条 bash 都是一个 job），所以只能用
    bg_calls（结果真的是通知的那些）当分母，否则 job_backgrounded 会等于 bash 总数。
    返回回填的动作数。"""
    by_id = {a.id: a for a in actions}
    touched, killed, pending = 0, 0, 0
    for a in actions:
        if a.call_id not in bg_calls:
            continue
        jid = job_bind.get(a.call_id, "")
        a.job_id = jid
        txt = job_out.get(jid) if jid else None
        if txt is None:
            pending += 1                       # 从没被 job_output 捞回来
            a.job_status = job_settled.get(jid, "") if jid else ""
            continue
        m = JOB_TAIL_RE.search(txt)
        status = m.group(1) if m else (job_settled.get(jid) or "unknown")
        a.job_status = status
        a.job_exit = int(m.group(2)) if m else None
        if status != "completed":
            killed += 1                        # killed/unknown：不回填
            continue
        a.out_head = _head(txt)
        a.out_sha = hashlib.sha1(txt.encode("utf-8")).hexdigest()[:16]
        a.out_source = "job_output"
        a.job_done_ts = job_settled_ts.get(jid)
        touched += 1
    stats["job_killed"] = killed
    stats["job_pending"] = pending
    if not touched:
        return 0
    for row in episodes:
        acts = [by_id[i] for i in row["actions"] if i in by_id]
        row["game_day"] = _game_day(acts, ambig_seen, row.get("episode_id", ""),
                                    t_from=row.get("t_from") or 0,
                                    t_to=row.get("t_to") or 0)
        classify_episode(row, acts)            # 探针可用性/成败按真终态重判
    return touched


def build_session(events, session_name: str):
    """喂一个会话的事件流，返回 (actions, episodes, stats)。"""
    is_owner = classify(session_name) == "owner"
    stats = {
        "tool_calls": 0, "bash": 0, "bash_with_sd": 0, "bash_with_sd_verb": 0,
        "sd_verb_actions": 0, "sd_other_actions": 0, "sd_actions_total": 0,
        "sd_raw_paths": 0, "sd_raw_actions": 0, "sd_in_automation": 0,
        "turns": 0, "game_turns": 0, "gap_splits": 0, "orphan_results": 0,
        "game_day_ambiguous": 0,
        "job_backgrounded": 0, "job_recovered": 0, "job_pending": 0,
        "job_killed": 0, "digest_added": 0,
    }
    ambig_seen = set()    # 歧义剧集的独立集合（cmb 实锤：int 计数会被双判翻倍）
    actions, episodes = [], []
    calls = {}            # callId -> {"ts": ...}（配对用）
    pending = {}          # callId -> [Action]（result 回填用）
    job_bind = {}         # callId -> jobId（job/registered 精确 join；通知文本兜底）
    bg_calls = set()      # 结果文本是「转到后台」通知的 callId（只有这些需要终态回填）
    job_out = {}          # jobId -> job_output 拿到的终态文本
    job_settled = {}      # jobId -> job/settled 的 status
    job_settled_ts = {}   # jobId -> finishedAt（cmb：采信拿事件时间判）
    jo_call = {}          # job_output 的 callId -> jobId
    open_turn = None      # 按文件顺序开合，不按回合号
    part = 0              # gap 切分的分片号
    last_hint = ""        # turn 外到的用户消息（有些日志 user/message 先于 turn/start）

    def _close(turn_end_ts, verdict):
        nonlocal open_turn, part
        if open_turn is None:
            return
        acts = open_turn["acts"]
        ep = Episode(
            episode_id=_sha("%s|%s|%d" % (session_name, open_turn["seq"], part)),
            session=session_name, turn=open_turn["turn"],
            t_from=open_turn["t_from"], t_to=turn_end_ts,
            goal_hint=open_turn.get("hint", ""),
            actions=[a.id for a in acts],
            verdict=verdict,
        )
        row = ep.row()
        row["act_count"] = len(acts)
        row["act_count_poll_free"] = sum(1 for a in acts if not a.poll_repeat)
        row["game"] = any(a.verb for a in acts)
        row["game_day"] = _game_day(acts, ambig_seen, row.get("episode_id", ""),
                                    t_from=row.get("t_from") or 0,
                                    t_to=row.get("t_to") or 0)
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
                             "t_from": ts, "acts": [], "last": None,
                             "hint": last_hint}
                last_hint = ""
        elif typ == "user/message":
            # S4 goal_hint：本回合的用户指令（生产形 content[0].text，
            # 真话在 <user_message> 标签；time-context 快照剥不出真话=空）
            src = da.get("source")
            is_snapshot = (isinstance(src, dict) and src.get("kind") == "time-context")
            h = "" if is_snapshot else _hint_text(da)
            if h and h.startswith("Time sampled while preparing"):
                h = ""                     # 旧日志无 source 字段时的兜底
            if h:
                if open_turn is not None and not open_turn["hint"]:
                    open_turn["hint"] = h
                elif open_turn is None:
                    last_hint = h
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
        elif typ == "job/registered":
            tc = da.get("toolCall") or {}
            if da.get("jobId") and tc.get("callId"):
                job_bind[tc["callId"]] = da["jobId"]
        elif typ == "job/settled":
            if da.get("jobId"):
                job_settled[da["jobId"]] = str(da.get("status") or "")
                fin = da.get("finishedAt") or da.get("finished_at")
                if fin:
                    job_settled_ts[da["jobId"]] = int(fin)
        elif typ == "tool/call":
            stats["tool_calls"] += 1
            name = da.get("name")
            try:
                args = json.loads(da.get("arguments") or "{}")
            except Exception:
                args = {}
            if name == "job_output" and args.get("job_id"):
                jo_call[da.get("callId")] = args["job_id"]
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
            cid = msg.get("toolCallId")
            txt_full = "".join(b.get("text", "") for b in (msg.get("content") or [])
                               if isinstance(b, dict))
            if cid in jo_call:
                # job_output 的返回才是后台命令的真终态（通知文本只是「还在跑」）
                job_out[jo_call[cid]] = txt_full
                continue
            hit = calls.get(cid)
            if hit is None:
                stats["orphan_results"] += 1   # 压缩/中断后会有孤儿 result，容忍+计数
                continue
            mine = pending.get(cid)
            if not mine:
                continue
            err = bool(msg.get("isError"))
            dt = (ev.get("time", 0) - hit["ts"]) if hit["ts"] and ev.get("time") else -1
            head = _head(txt_full)
            if "⟨" in head:
                stats["digest_added"] += 1
            osha = hashlib.sha1(txt_full.encode("utf-8")).hexdigest()[:16]
            m = BG_NOTICE_RE.search(txt_full)
            if m:
                job_bind.setdefault(cid, m.group(1))   # 兜底：没有 job/registered 时从通知抓
                bg_calls.add(cid)
                # 通知文本不是输出：留着会假装「探针可用」（probe_status=ok 的假绿）。
                # 终态能不能捞回来由 _apply_job_outputs 决定，捞不回来就如实不可用。
                head, osha = "", ""
            for a in mine:
                a.is_error = err
                a.latency_ms = dt if dt >= 0 else -1
                a.out_head = head
                a.out_sha = osha
            del pending[cid]
    # 采集修定：后台化动作的终态回填 + 受影响剧集重判（回填前不存在「同回合对账」证据）
    stats["job_backgrounded"] = sum(1 for a in actions if a.call_id in bg_calls)
    stats["game_day_ambiguous"] = len(ambig_seen)
    stats["job_recovered"] = _apply_job_outputs(actions, episodes, job_bind, bg_calls,
                                                job_out, job_settled, stats,
                                                ambig_seen, job_settled_ts)
    stats["game_day_ambiguous"] = len(ambig_seen)   # 回填重判后再取终值
    return actions, episodes, stats
