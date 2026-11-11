# -*- coding: utf-8 -*-
"""评审 11-12 修复补丁：P1-1/P1-2/P1-4 + 四条 P2 + README 边界。

每个锚点必须恰好命中一次，否则整仓不动（fail-closed）。
"""
import io
import sys

ROOT = "/tmp/expmem-review"


def load(rel):
    return io.open(ROOT + "/" + rel, encoding="utf-8").read()


def save(rel, text):
    io.open(ROOT + "/" + rel, "w", encoding="utf-8", newline="\n").write(text)


def rep(src, old, new, tag):
    n = src.count(old)
    if n != 1:
        raise SystemExit("ANCHOR-FAIL %s (count=%d)" % (tag, n))
    return src.replace(old, new)


ok = []

# ---------- 1. consolidate.py ----------
f = "expmem/consolidate.py"
s = load(f)

s = rep(s, "def scan_success(episodes, actions):",
        "def scan_success(episodes, actions, stats=None):", "c-sig")

s = rep(s, """    for e in (episodes or []):
        if e.get("verdict") == "failure":
            continue            # 失败集（含 blocked；wrong_arg 仅历史数据）不进成功轨""",
"""    for e in (episodes or []):
        if e.get("verdict") != "success":
            continue            # 只认 outcome.py 判定的 success（评审 11-12 P1-1）：
                                # unknown/interrupted 的正向信号同样不进成功轨——
                                # 单操作的 stored 非空 ≠ 整个任务过成功判经""", "c-strict")

s = rep(s, """            rule = ""
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
""", """            rule = _success_rule(a.get("verb") or "",
                                 a.get("raw_path") or "", h)
""", "c-rule-helper")

s = rep(s, """                             "verb": a.get("verb") or "",
                             "is_write": (a.get("verb") or "") in _WRITE_VERBS,""",
"""                             "verb": a.get("verb") or "",
                             "raw_path": a.get("raw_path") or "",
                             "is_write": (a.get("verb") or "") in _WRITE_VERBS,""", "c-rawpath")

s = rep(s, """    # 同 call 归因：有写动作时读动作让位（写动作是效果的制造者）
    by_call = {}
    for r in rows:
        by_call.setdefault((r["session"], r["call_id"]), []).append(r)
    out = []
    for group in by_call.values():
        writes = [r for r in group if r["is_write"]]
        keep = writes if writes else group
        out.extend(keep)
    return out""",
"""    # 同 call 归因（评审 11-12 P1-2）：一段 out_head 属于整条 bash 链，链上每个
    # 子动作带着同一份文本——不能整段冒充每个子命令各自的证据（stardew-store 卡
    # 三条 evidence 同一段 Bullhead×39 实锤）。归因阶梯：
    #   1) call 内命中去重后只剩一个动作 → 归它（原行为）
    #   2) 多个动作命中 → 按顺序切 JSON 对象段（sd 桥每命令一段 JSON）；
    #      段数==链上子动作数时按 sub_idx 对号，各动作只认自己段里的信号
    #   3) 切不动 → 整组不进成功轨，计入 stats["parked_success_rows"]
    #      （宁可空轨不硬凑；dropped 里留痕，不静默丢）
    by_call = {}
    for r in rows:
        by_call.setdefault((r["session"], r["call_id"]), []).append(r)
    subs_per_call = {}
    for a in (actions or []):
        k = (a.get("session") or "", a.get("call_id") or "")
        subs_per_call[k] = subs_per_call.get(k, 0) + 1
    out = []
    for group in by_call.values():
        writes = [r for r in group if r["is_write"]]
        keep = writes if writes else group
        if len({r["action"] for r in keep}) == 1:
            keep[0]["attribution"] = "single"   # 同一动作多集命中=同一份证据
            out.append(keep[0])
            continue
        n_subs = subs_per_call.get((keep[0]["session"], keep[0]["call_id"]), 0)
        segs = _split_json_objects(keep[0]["out"]) if n_subs else []
        if len(segs) != n_subs:
            if stats is not None:
                stats["parked_success_rows"] = \\
                    stats.get("parked_success_rows", 0) + len(keep)
            continue
        for r in keep:
            seg = segs[r["sub_idx"]] if r["sub_idx"] < len(segs) else ""
            rule = _success_rule(r["verb"], r.get("raw_path") or "", seg)
            if not rule:
                if stats is not None:
                    stats["parked_success_rows"] = \\
                        stats.get("parked_success_rows", 0) + 1
                continue
            r2 = dict(r)
            r2["out"] = seg
            r2["rule"] = rule
            r2["attribution"] = "segment"
            out.append(r2)
    return out""", "c-attrib")

s = rep(s, """def scan_success(episodes, actions, stats=None):""",
"""def _success_rule(verb, raw_path, txt):
    \"\"\"store 存进/收到/买对 三信号的机械匹配（主扫描与段级再归因共用）。\"\"\"
    if STORED_NONEMPTY_RE.search(txt):
        return "stored_nonempty"
    m = HARVEST_COUNT_RE.search(txt)
    if m and int(m.group(1)) >= 1:
        return "harvest_count_%s" % m.group(1)
    if verb == "buy" or raw_path == "/buy":
        mc = _BUY_CHARGE_RE.search(txt)
        if mc:
            return "buy_charged_%s" % mc.group(1)
    return ""


def _split_json_objects(txt):
    \"\"\"按顺序切顶层 JSON 对象（sd 桥每命令一段 JSON，段间允许杂散文本）。
    只认 dict；段数与子动作数对不上时由调用方 parked，不硬凑。\"\"\"
    dec = json.JSONDecoder()
    segs, i, n = [], 0, len(txt or "")
    while i < n:
        while i < n and txt[i] != "{":
            i += 1
        if i >= n:
            break
        try:
            obj, j = dec.raw_decode(txt, i)
        except ValueError:
            i += 1
            continue
        if isinstance(obj, dict):
            segs.append(txt[i:j])
        i = j
    return segs


def scan_success(episodes, actions, stats=None):""", "c-helpers")

s = rep(s, "    ok_rows = scan_success(episodes, actions)",
"""    _ss = {}
    ok_rows = scan_success(episodes, actions, stats=_ss)
    if _ss.get("parked_success_rows"):
        dropped = list(dropped) + [dict(_ss)]   # parked 留痕可见，不静默丢""", "c-induct")

save(f, s)
ok.append(f)

# ---------- 2. skills.py ----------
f = "expmem/skills.py"
s = load(f)

s = rep(s, """def transition(front: dict, new_state: str, actor: str) -> dict:""",
"""# 外部确认主体白名单（评审 11-12 P2）：自由文本不算确认
_CONFIRM_ACTORS = ("owner",)


def transition(front: dict, new_state: str, actor: str) -> dict:""", "s-conf")

s = rep(s, """        if actor == "machine":
            raise StateError("机器禁止升级（%s→%s 需外部确认）" % (old, new_state))""",
"""        if actor == "machine":
            raise StateError("机器禁止升级（%s→%s 需外部确认）" % (old, new_state))
        if actor not in _CONFIRM_ACTORS:
            raise StateError("actor=%r 不是确认主体白名单 %s（自由文本不算外部确认"
                             "——评审 11-12 P2）" % (actor, _CONFIRM_ACTORS))""", "s-actor")

s = rep(s, """    with open(path, "w", encoding="utf-8", newline="\\n") as f:
        f.write(text)""",
"""    # 原子写（评审 11-12 P1-4）：中途退出不留半截卡——
    # 同目录临时文件 + fsync + os.replace（同盘 rename 原子）
    import tempfile
    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(prefix=".skill.", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise""", "s-atomic")

s = rep(s, """def save_skill(path: str, front: dict, body: str) -> str:""",
"""import contextlib


@contextlib.contextmanager
def file_lock(path):
    \"\"\"单机文件锁（评审 11-12 P1-4）：flock 独占；点前缀锁文件不入检索。\"\"\"
    import fcntl
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    fh = open(path, "a")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield fh
    finally:
        try:
            fcntl.flock(fh, fcntl.LOCK_UN)
        finally:
            fh.close()


@contextlib.contextmanager
def card_lock(skills_dir, name):
    \"\"\"按卡锁：覆盖「读旧卡→改→写→记账」全程（评审 11-12 P1-4）。\"\"\"
    with file_lock(os.path.join(skills_dir, name, ".lock")):
        yield


def save_skill(path: str, front: dict, body: str) -> str:""", "s-locks")

save(f, s)
ok.append(f)

# ---------- 3. feedback.py ----------
f = "expmem/feedback.py"
s = load(f)
s = rep(s, """        if not raw.get("executed"):
            raise ValueError("strong 反馈必须 executed=true（执行过才算 strong）")""",
"""        if raw.get("executed") is not True:
            raise ValueError("strong 反馈必须 executed=true（严格布尔：\\"false\\"/1/"
                             "1.0 一律不收——评审 11-12 P2）")""", "f-exec")
save(f, s)
ok.append(f)

# ---------- 4. api.py ----------
f = "expmem/api.py"
s = load(f)

s = rep(s, "def record_skill_feedback(fb, shown_ids=()) -> dict:",
        "def record_skill_feedback(fb, shown_ids=(), manual_review=False) -> dict:",
        "a-sig")

s = rep(s, """    path = os.path.join(_skills_dir(), name, "SKILL.md")
    if not os.path.exists(path):
        return {"applied": False, "reason": "card_not_found: %s" % name, "front": None}
    front, body = load_skill(path)
    new_front, applied, reason = _fb.record_feedback(front, fb, shown_ids=shown_ids)
    if applied:
        save_skill(path, new_front, body)   # 正文原样；save_skill 再过一次正文闸
    return {"applied": applied, "reason": reason, "front": new_front}""",
"""    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name or ""):
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
    return {"applied": applied, "reason": reason, "front": new_front}""", "a-rsf")

s = rep(s, """def settle_auto_marks(episodes, actions=None, skills_dir: str = None,
                      extra_manifest=None) -> dict:""",
"""def settle_auto_marks(episodes, actions=None, skills_dir: str = None,
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
                   extra_manifest=None) -> dict:""", "a-wrap")

s = rep(s, """    from . import feedback as _fb
    from .consolidate import _tool_key
    from .skills import load_skill, save_skill
    sdir = skills_dir or _skills_dir()
    settled_path = os.path.join(sdir, ".settled.jsonl")""",
"""    from . import feedback as _fb
    from .consolidate import _tool_key
    from .skills import load_skill, save_skill, card_lock
    settled_path = os.path.join(sdir, ".settled.jsonl")""", "a-sdir")

s = rep(s, """            front, body = load_skill(path)
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
                skipped += 1                  # not_used 等终局判定：算结清""",
"""            with card_lock(sdir, name):       # 单卡读改写全程持锁（评审 P1-4）
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
                    skipped += 1                  # not_used 等终局判定：算结清""", "a-cardlock")

save(f, s)
ok.append(f)

# ---------- 5. store.py ----------
f = "expmem/store.py"
s = load(f)
s = rep(s, """        self._fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True,
                                  separators=(",", ":")) + "\\n")
        self._seen.add(rid)""",
"""        self._fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True,
                                  separators=(",", ":")) + "\\n")
        # 崩溃安全兑现（评审 11-12 P2）：显式刷盘，进程退出/断电不丢已 append 行
        self._fh.flush()
        os.fsync(self._fh.fileno())
        self._seen.add(rid)""", "st-fsync")
save(f, s)
ok.append(f)

# ---------- 6. cli.py ----------
f = "expmem/cli.py"
s = load(f)
s = rep(s, """    fp.add_argument("--shown", default="",
                    help="本轮展示过的卡名，逗号分隔；空=人工无上下文（守卫①放行）")""",
"""    fp.add_argument("--shown", default="",
                    help="本轮展示过的卡名，逗号分隔")
    fp.add_argument("--manual-review", action="store_true",
                    help="显式声明人工审核通道（无本轮展示上下文时必须二选一）")""", "cli-arg")
s = rep(s, "        _print(api.record_skill_feedback(fb, shown_ids=shown))",
"""        _print(api.record_skill_feedback(fb, shown_ids=shown,
                                         manual_review=args.manual_review))""", "cli-call")
save(f, s)
ok.append(f)

# ---------- 7. README ----------
f = "README.md"
s = load(f)
s = rep(s, """memory-hub / auto-memory / 手册§8 / shared-memory.md（知识层），两层用 evidence
互相点，不互相复述。""",
"""memory-hub / auto-memory / 手册§8 / shared-memory.md（知识层），两层用 evidence
互相点，不互相复述。

## 当前边界：Stardew-first（2026-11-12 评审明确）

现在真正跑通的经验链只有 **dsh 会话日志 → bash → sd 命令 → 星露谷动作** 这一条。
`adapters/tool_generic.py`、`adapters/minecraft.py`、`sources/live_tap.py` 尚未实现，
`api.record_action()` / `commit_episode()` 仍是 `NotImplementedError`——本仓
口径是「星露谷离线经验提炼 + 原生 Skill 卡消费」，不是「所有工具/游戏通用」。
通用化按 Action/Episode 协议走 **Adapter + Outcome Evaluator** 两个扩展点，
不为每个游戏另起一套技能记忆（见 docs/00-plan.md）。""", "rd-scope")
save(f, s)
ok.append(f)

print("PATCHED:", ", ".join(ok))
