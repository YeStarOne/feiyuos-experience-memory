# -*- coding: utf-8 -*-
"""四个接口的唯一实现（方案 §2）。S0 只落 replay（采集），其余接口有签名有桩。

record_action / commit_episode —— replay 里真实在写（append-only）
retrieve_skills / record_skill_feedback —— S2/S3 才有真身，这里只留签名
"""
import hashlib
import json
import os
import re

from .store import AppendOnly
from .sources.session_log import session_files, classify, iter_events
from .episode import build_session, PARSER_V
from .outcome import OUTCOME_V
from .bench import bench

ACTION_FIELDS = ("success", "failure", "unknown", "interrupted")


def _sha_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _check_meta(out_dir: str):
    """混合重放闸（11-12 下一批）：out_dir/meta.json 记着这份数据是哪代
    解析器（PARSER_V）与哪版判据（OUTCOME_V）写的；AppendOnly 只追加不改
    历史，版本不一致继续重放 = 新旧行混一锅，谁都说不清这把尺量的是什么。
    fail-closed：读不动/缺键（无版本信息的旧产物）当未知，拒绝并指路。"""
    path = os.path.join(out_dir, "meta.json")
    if not os.path.exists(path):
        return   # 无 meta.json 的旧产物放行：首次重放补写 meta，之后闸生效
    try:
        with open(path, encoding="utf-8") as f:
            old = json.load(f)
    except Exception:
        old = {}
    for k, cur in (("parser_v", PARSER_V), ("outcome_v", OUTCOME_V)):
        ov = old.get(k)
        if ov is None or int(ov) != cur:
            raise ValueError(
                "out_dir 与当前代码版本不一致（meta.json %s=%r，当前=%d）："
                "AppendOnly 改不了历史，混放会造出两代行并存的数据集——"
                "换一个新 out_dir 重放，或把旧目录整体归档" % (k, ov, cur))


def replay(root: str, out_dir: str, source: str = "execution") -> dict:
    """只读重放：sessions root → actions.jsonl / episodes.jsonl / metrics.json。

    幂等：同目录重跑不产生重复行（AppendOnly 按 id 去重，跨进程亦然——
    store 内部有独占锁+吸收）。
    版本闸：out_dir/meta.json 记 PARSER_V/OUTCOME_V，不一致直接拒绝（拒绝
    混合重放，评审遗留项）。
    source（11-12 下一批）：证据来源标签，落进每个 episode 行；非 "execution"
    的重放（bench/fixture 灌数）consolidate 侧一律拒绝进归纳轨。
    验收判据（方案 §4-S0）都落在 metrics 里。"""
    os.makedirs(out_dir, exist_ok=True)
    _check_meta(out_dir)
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
                e["source"] = source            # 11-12 下一批：来源随行落盘
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
    # 版本元数据（11-12 下一批）：下一次重放靠它识别"这是同一把尺写的数据"
    with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump({"parser_v": PARSER_V, "outcome_v": OUTCOME_V,
                   "source": source, "root": root}, f,
                  ensure_ascii=False, sort_keys=True, indent=1)
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


def retrieve_skills(query, top_k: int = None, domain: str = None,
                    session: str = "", turn: int = 0) -> dict:
    """真身（S2 收尾）：扫 ~/.dsh/skills（EXPMEM_SKILLS_DIR 可覆盖）→ 检索。
    返回 {"refs","incomplete","dropped_weak","conflict_pairs","total_active",
    "lines","level","hint"}——lines/level 是 catalog_lines 的注入文本与降级级别，
    hint 是 refs=∅ 时的「另有 N 条」。

    intel 复审（11-11）补的缺件：**本轮注入清单落盘**（~/.dsh/skills/.injected.jsonl，
    行={ts,session,turn,query,names,scores}）——没有它，A-MEM 守卫①在自动链路上
    是空的（模型没见过的卡引用会被放行），ACE 自动打标也拿不到 used。"""
    import time as _time
    from .retrieve import catalog_lines, empty_hint, load_catalog as _load
    from .retrieve import retrieve_skills as _rs
    result = _rs(query, _load(_skills_dir()), domain=domain, top_k=top_k)
    lines, level = catalog_lines(result)
    result["lines"], result["level"] = lines, level
    result["hint"] = empty_hint(result)
    if result["refs"]:
        mrow = {"ts": int(_time.time()), "session": session or "",
                "turn": turn or 0, "query": (query or "")[:120],
                "names": [r["name"] for r in result["refs"]],
                "scores": [r["score"] for r in result["refs"]]}
        try:
            with open(os.path.join(_skills_dir(), ".injected.jsonl"),
                      "a", encoding="utf-8") as f:
                f.write(json.dumps(mrow, ensure_ascii=False,
                                   sort_keys=True) + "\n")
        except OSError:
            pass                      # manifest 失败不挡检索（宿主可能只读）
    return result


def injected_names(skills_dir: str = None, session: str = "",
                   turn: int = None) -> list:
    """读注入清单：某 (session, turn) 本轮递给她的卡名。session 为空=全部、
    turn 为 None=不限回合。清单不存在或读不动 → 空列表（fail-safe：当没用到）。"""
    path = os.path.join(skills_dir or _skills_dir(), ".injected.jsonl")
    names = []
    try:
        with open(path, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    row = json.loads(ln)
                except Exception:
                    continue
                if session and row.get("session") != session:
                    continue
                if turn is not None and row.get("turn") != turn:
                    continue
                for n in row.get("names") or []:
                    if n not in names:
                        names.append(n)
    except OSError:
        return []
    seen = set()
    return [n for n in names if not (n in seen or seen.add(n))]


CATALOG_BLOCK_RE = re.compile(r"<available_skills>(.*?)</available_skills>",
                              re.S)
CATALOG_NAME_RE = re.compile(r"^-\s*`([a-z0-9-]+)`", re.M)


def derive_manifest(root: str) -> dict:
    """从会话日志派生「本轮展示过什么」（cmb-P1 裁定②的落法）。

    两个零改动真源（都不动 dsh 源码）：
    ① <available_skills> 块——harness skill 插件全量列举 ~/.dsh/skills
      （`- \\`name\\`: Use when…` 行格式），只在回合首条/技能集变化时出现；
    ② 原生 skill 工具的「翻开」——`tool/call name=skill arguments={"name":X}`
      是比列举更强的 shown 证据（S3 实录：卡 02:37 部署后只有 turn 925 有
      目录块，但她 926 翻开 stardew-store——翻开也该进 shown-set，否则
      used=true 的样本永远等不到）。
    返回 {(session, turn): [names]}。"""
    from .sources.session_log import classify, iter_events, session_files
    manifest = {}
    for name, path in session_files(root):
        if classify(name) != "owner":
            continue
        turn = None
        for ev in iter_events(path):
            typ = ev.get("type")
            da = ev.get("data") or {}
            if typ == "turn/start":
                turn = da.get("turn")
                continue
            if typ == "tool/call" and da.get("name") == "skill":
                open_name = ""
                try:
                    open_name = (json.loads(da.get("arguments") or "{}")
                                 .get("name") or "")
                except Exception:
                    open_name = ""
                if open_name and turn is not None:
                    manifest.setdefault((name, turn), [])
                    if open_name not in manifest[(name, turn)]:
                        manifest[(name, turn)].append(open_name)
                continue
            if typ != "user/message":
                continue
            text = da.get("text") or ""
            if not text or not isinstance(text, str):
                # cmb-P0（复审）：生产 user/message.data 没有text键——
                # 空串也是 str，旧条件永假=永远不读 content=生产恒 {}。
                # 「空或非str」都走 content[0].text（夹具必须用生产形）。
                c = da.get("content")
                if isinstance(c, list):
                    text = "".join(x.get("text", "") for x in c
                                   if isinstance(x, dict))
            m = CATALOG_BLOCK_RE.search(text)
            if not m:
                continue
            names = CATALOG_NAME_RE.findall(m.group(1))
            if names:
                manifest.setdefault((name, turn), [])
                for n in names:
                    if n not in manifest[(name, turn)]:
                        manifest[(name, turn)].append(n)
    return manifest


def record_skill_feedback(fb, shown_ids=(), manual_review=False) -> dict:
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
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name or ""):
        return {"applied": False, "reason": "invalid_name: %r" % (name,),
                "front": None}   # 卡名白名单：路径穿越进不来（评审 11-12 P2）
    path = os.path.join(_skills_dir(), name, "SKILL.md")
    if not os.path.exists(path):
        return {"applied": False, "reason": "card_not_found: %s" % name, "front": None}
    if not shown_ids and not manual_review:
        # 空展示上下文必须显式二选一（评审 11-12 P2）：静默放行=模型没见过
        # 卡的反馈也记账
        return {"applied": False,
                "reason": "no_shown_context: 传 shown_ids 或 manual_review=True",
                "front": None}
    from .skills import card_lock
    with card_lock(_skills_dir(), name):    # 读旧卡→改→写全程持锁（评审 P1-4）
        front, body = load_skill(path)
        new_front, applied, reason = _fb.record_feedback(front, fb,
                                                         shown_ids=shown_ids)
        if applied:
            save_skill(path, new_front, body)   # 正文原样；save_skill 再过一次正文闸
    return {"applied": applied, "reason": reason, "front": new_front}


SETTLE_VERDICT_V = OUTCOME_V
# 判据版本：直接对齐 outcome.py 的 OUTCOME_V（v2=S4 引入 success verdict）——
# 判据抬版时这里自动跟，v1 行（含今晚全是 unknown 的历史结清）自动视为未结算
# 重判；settled 行带 v，读取时 v<当前版 视为未结算（历史 unknown 结清不许
# 永久封死未来的重判/重放信用，cmb-P1-forward）。
# 台账压实（11-12 下一批）：settled 行数超过阈值就压实——同 (session,turn)
# 只留 max(v) 行（消费语义本来就是取 max(v)，压实不改判读），旧文件留 .bak。
# 只在 .settled.lock 内做（_settle_locked 全程持锁）。台账是幂等账不是证据链，
# 压实有备份、有阈值、可重放（重放全史会把该结的重新结出来），不算改历史。
SETTLED_COMPACT_AT = 2000


def _compact_settled(settled_path: str) -> int:
    """同 (session,turn) 只留 max(v) 行，原子替换，旧文件留 .bak。
    返回压实后的行数；文件不存在/为空返回 0（不动）。坏行不带走（留 .bak 里）。"""
    try:
        with open(settled_path, encoding="utf-8") as f:
            rows = [ln.strip() for ln in f if ln.strip()]
    except OSError:
        return 0
    if not rows:
        return 0
    best, order = {}, []
    bad = []
    for ln in rows:
        try:
            row = json.loads(ln)
            k = (row.get("session"), row.get("turn"))
        except Exception:
            bad.append(ln)
            continue
        if k not in best:
            best[k] = row
            order.append(k)
        else:
            if (row.get("v") or 0) >= (best[k].get("v") or 0):
                best[k] = row          # 同键取 max(v)；同 v 取后写的
    if len(order) + len(bad) == len(rows) and len(bad) == 0:
        return len(rows)               # 没有可压空间：原样不动（幂等）
    import tempfile
    d = os.path.dirname(os.path.abspath(settled_path)) or "."
    compact = [json.dumps(best[k], ensure_ascii=False, sort_keys=True)
               for k in order] + bad
    fd, tmp = tempfile.mkstemp(prefix=".settled.", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            for ln in compact:
                f.write(ln + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(settled_path, settled_path + ".bak")
        os.replace(tmp, settled_path)
        os.chmod(settled_path, 0o644)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return len(compact)


def settle_auto_marks(episodes, actions=None, skills_dir: str = None,
                      extra_manifest=None) -> dict:
    # 并发闸（评审 11-12 P1-4）：全程持 .settled.lock——两进程同时 settle
    # 同一回合只结一次；串行幂等语义不变（.settled.jsonl 防重放仍有效）。
    # 实现体挪到 _settle_locked，签名/返回不变。
    sdir = skills_dir or _skills_dir()
    os.makedirs(sdir, exist_ok=True)
    from .skills import file_lock
    with file_lock(os.path.join(sdir, ".settled.lock")):
        return _settle_locked(episodes, actions, sdir, extra_manifest)


def _settle_locked(episodes, actions=None, sdir=None,
                   extra_manifest=None) -> dict:
    """ACE 自动打标的唯一接线（intel 复审 #1 的落点）：逐个成功回合，
    used = 该 (session, turn) 注入清单里的卡名 ∩ **组内全部集真实执行过的
    tool_key 并集**（intel 复审 B：只按「递过」打标=回声室；cmb-P1：判单
    单位=回合不是剧集——gap 切分的同键多集先聚合，首集之后的动作才算数；
    并集**含 interrupted 集**——「她执行过这个 tool_key」语义成立，S4 若要
    排除被打断的动作须在此显式改口径，不许默认）。
    幂等（intel 复审 A）：已结算的 (session, turn) 记进 .settled.jsonl（带
    v+reason），重放全史也不重复计；v 落后判据版本的行视为未结算；
    **消费者口径：同 (session,turn) 多行并存时取 max(v) 那行**（抬版后旧行
    不删，append-only），外部按行计数会双计（cmb 复审实证 1318→2636）。
    解析不动的 settled 行整条不结（A-MEM 守卫②同款）。
    返回 {"marked": {name: n}, "skipped": n, "already_settled": n,
          "reasons": {"verdict:*"/"no_injected"/"complete": n}}。"""
    from . import feedback as _fb
    from .consolidate import _tool_key
    from .skills import load_skill, save_skill, card_lock
    settled_path = os.path.join(sdir, ".settled.jsonl")
    settled = set()
    try:
        with open(settled_path, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    row = json.loads(ln)
                    # cmb-P1-forward：v 落后当前判据版=未结算（重判）
                    if (row.get("v") or 0) < SETTLE_VERDICT_V:
                        continue
                    settled.add((row.get("session"), row.get("turn")))
                except Exception:
                    continue           # 坏行不挡（幂等宁可少结不半套）
    except OSError:
        pass
    # manifest 一次读成索引（intel 顺带：逐集全量读是 O(集数×清单行数)）
    manifest = {}
    mpath = os.path.join(sdir, ".injected.jsonl")
    try:
        with open(mpath, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    row = json.loads(ln)
                    cur = manifest.setdefault((row.get("session"),
                                               row.get("turn")), [])
                    for n in row.get("names") or []:
                        if n not in cur:      # cmb-P3a：多行 retrieve 合并去重
                            cur.append(n)
                except Exception:
                    continue
    except OSError:
        pass
    for skey, names in (extra_manifest or {}).items():
        cur = manifest.setdefault(skey, [])
        for n in names:
            if n not in cur:
                cur.append(n)
    # 本集动作的 tool_key 集合
    keys_by_ep = {}
    for a in (actions or []):
        keys_by_ep.setdefault(a.get("id"), _tool_key(a))
    marked, skipped, already = {}, 0, 0
    reasons = {}
    new_settled = []
    # cmb-P1（复审）：判单单位=回合，不是剧集——同 (session,turn) 的多集
    # （gap 切分）先聚合再打标一次：used_keys=组内全部集动作的并集，
    # verdict=组内任一集 success 即该回合 success。否则首集之后的动作
    # （活日志实测 68%）对 used 检测不可见 → 好卡被记成「展示过但没用」。
    groups, ep_order = {}, []
    for e in episodes or []:
        skey = (e.get("session"), e.get("turn"))
        if skey not in groups:
            groups[skey] = []
            ep_order.append(skey)
        groups[skey].append(e)
    for skey in ep_order:
        eps = groups[skey]
        if skey in settled:
            already += 1
            continue
        verdicts = {e.get("verdict") for e in eps}
        gverdict = ("success" if "success" in verdicts
                    else "failure" if "failure" in verdicts
                    else "interrupted" if "interrupted" in verdicts
                    else "unknown")
        if gverdict != "success":
            skipped += 1
            reasons["verdict:" + gverdict] = \
                reasons.get("verdict:" + gverdict, 0) + 1
            new_settled.append(skey + ("verdict:" + gverdict,))
            settled.add(skey)
            continue
        injected = manifest.get(skey) or []
        if not injected:
            skipped += 1
            reasons["no_injected"] = reasons.get("no_injected", 0) + 1
            new_settled.append(skey + ("no_injected",))
            settled.add(skey)
            continue
        used_keys = set()
        for e in eps:                          # 组内全集的并集，不只首集
            for i in (e.get("actions") or []):
                if i in keys_by_ep:
                    used_keys.add(keys_by_ep[i])
        complete = True        # intel 复审 A：settled 只在**全部落账后**写——
        for name in injected:  # 卡缺失/落盘失败 = 该回合不结，下次重试，
            if not os.path.isdir(os.path.join(sdir, name)):
                continue       # cmb-P1：harness 等非本仓卡名——忽略，
                               # 不算 incomplete（否则 settled 永不写=每轮重标=灌水）
            path = os.path.join(sdir, name, "SKILL.md")   # 不许永久作废信用
            if not os.path.exists(path):
                skipped += 1
                complete = False   # 目录在而 SKILL.md 缺：真损，可重试
                continue
            with card_lock(sdir, name):       # 单卡读改写全程持锁（评审 P1-4）
                front, body = load_skill(path)
                # cmb-P2-residual（S4 必带）：按卡幂等——这张卡这个回合标过就跳过
                # （半截卡拖住回合结清 → 重试时好卡不许再 +1）
                skey_str = "%s|%s" % skey
                if skey_str in (front.get("auto_marks") or []):
                    continue
                card_key = front.get("tool_key") or ""
                used = bool(card_key) and card_key in used_keys   # 「用了」≠「递了」
                new_front, applied, _ = _fb.auto_from_episode(front, "success",
                                                              used=used,
                                                              auto_key=skey_str)
                if applied:
                    try:
                        save_skill(path, new_front, body)
                    except OSError:
                        skipped += 1
                        complete = False          # 落盘失败：不写 settled，可重试
                        continue
                    marked[name] = marked.get(name, 0) + 1
                else:
                    skipped += 1                  # not_used 等终局判定：算结清
        if complete:
            reasons["complete"] = reasons.get("complete", 0) + 1
            new_settled.append(skey + ("complete",))
            settled.add(skey)                 # cmb-P2：同回合多集（gap 切分）同轮单标
    compacted = 0
    if new_settled:
        try:
            with open(settled_path, "a", encoding="utf-8") as f:
                for s, t, why in new_settled:
                    f.write(json.dumps({"session": s, "turn": t,
                                        "v": SETTLE_VERDICT_V, "reason": why},
                                       ensure_ascii=False, sort_keys=True) + "\n")
        except OSError:
            pass
        # 台账压实（11-12 下一批）：超阈值就地压（同键留 max(v)），仍在锁内
        try:
            n_lines = sum(1 for _ in open(settled_path, encoding="utf-8"))
            if n_lines > SETTLED_COMPACT_AT:
                compacted = _compact_settled(settled_path)
        except OSError:
            pass
    return {"marked": marked, "skipped": skipped, "already_settled": already,
            "reasons": reasons, "compacted": compacted}
