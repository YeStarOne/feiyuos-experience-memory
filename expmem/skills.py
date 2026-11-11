# -*- coding: utf-8 -*-
"""SkillRegistry：技能卡 = SKILL.md 文件（原生 dsh-skill 机制，热监听加文件即生效）。

设计要点（方案 §7-8 / OpenViking+Voyager 对齐）：
- frontmatter 机器算（v2.1：tool_key/state/valid_from 双标/evidence/confidence/
  contradicts/superseded_by/retrieval_anchor/task_query/version），正文给人读。
- frontmatter 用 JSON 序列化（JSON ⊂ YAML，dsh 的 frontmatter 解析器直接吃，
  我们这边 json 往返零依赖、零歧义）。
- 状态机闸门：candidate→observed→verified 只能逐级升；任何态可降 deprecated
  （现实打脸不删卡——Voyager 反证"只记成功"的学不会失败）；同名覆盖后旧卡留盘。
- 禁止自动 verified：升级必须外部确认（owner/二审），机器只许降不许升。

11-11 S2 收尾（intel 四仓裁定落地）：
- ExpeL 台账字段：counter（净值，基线 1=「在书里」；≤0 移出书——retrieve 侧不装载）
  + strong_ok/strong_fail（conf 只吃 strong）+ test_count（升级闸只认 is_tested）。
- SkillWeaver「改一次验证清零」：正文/语义字段任何变化 → version+1、test_count=0、
  counter 回基线、状态回 candidate（deprecated 永不复活）。AWM 整份重写不带
  merge 阈值——rewrite_card 就是唯一写路径，旧的合并二审机制删除。
- 正文机械闸：卡要泛化——具体坐标/G 数属于 frontmatter evidence（点回
  actions.jsonl），不许出现在指令正文里。"""
import json
import os
import re

STATES = ("candidate", "observed", "verified", "pending_review", "deprecated")

# 允许的状态迁移（cmb-P1-2：pending_review 是 conflict 卡的落点，retrieve 侧
# ACTIVE_STATES 早就有它）——升级逐级 + 全域可降 + pending 可撤回/降废。
LEGAL = {
    ("candidate", "candidate"), ("candidate", "observed"),
    ("candidate", "pending_review"), ("candidate", "deprecated"),
    ("observed", "observed"), ("observed", "verified"),
    ("observed", "pending_review"), ("observed", "deprecated"),
    ("verified", "verified"), ("verified", "deprecated"),
    ("pending_review", "pending_review"), ("pending_review", "candidate"),
    ("pending_review", "deprecated"),     # 二审撤回/降废；pending 永不可直接升
    ("deprecated", "deprecated"),         # 降级后仍可补证据/改指向往，但永不可复活
}

# ExpeL 台账（feedback.py 维护）：counter 是 (text, counter) 的 counter；
# 基线 1 = 卡被写进书里这件事本身。负向反馈减、正向加，≤0 = 移出书。
COUNTER_BASELINE = 1

# SkillWeaver 清零判定里「动了就算改」的语义字段（evidence 的增删也算——
# 证据变了卡就该重验证，这正是「改一次验证清零」的本意）
SEMANTIC_FIELDS = ("description", "retrieval_anchor", "task_query", "tool_key",
                   "evidence", "contradicts", "domain")

# 正文机械闸（cmb 实测分布 11-11：绝对坐标 50.0% / JSON 坐标键 3.0%（segment）/
# 钱142538 44.2% / 数字+g 0%——g 形态只在她写的正文里可能出现，闸命中率低是常态，
# 0 命中+无已知拒收夹具才是假绿）。闸只扫正文，**绝不扫 evidence**——证据必须
# 逐字保留（点回 actions.jsonl），现有卡 evidence 里就有 chestAt 坐标，必须放行。
BODY_COORD_RE = re.compile(r"[（(]\s*-?\d{1,4}\s*[,，]\s*-?\d{1,4}\s*[）)]")
# JSON 键形态要吃进 shell 转义版（首跑实锤：她真实命令里是 \"x\":52,\"y\":13，
# 反斜杠会把朴素版漏掉——cmb 的提醒：形态要对真产物核）
BODY_COORD_JSON_RE = re.compile(
    r"""\\*["']x\\*["']\s*:\s*-?\d+\s*,\s*\\*["']y\\*["']\s*:\s*-?\d+""")
BODY_MONEY_G_RE = re.compile(r"(?<!\d)\d[\d,]{0,8}\s*g(?![a-zA-Z0-9])")
BODY_MONEY_CN_RE = re.compile(r"(?:钱|金币|gold)\s*[:：]?\s*\d[\d,]{2,}")

BODY_GATE_HINTS = (
    ("具体坐标 (x,y)——挪进 frontmatter evidence，正文写规则不写坐标", BODY_COORD_RE),
    ("JSON 坐标键 (\"x\":767,\"y\":538 形态)——正文写模板占位符，具体值进 evidence",
     BODY_COORD_JSON_RE),
    ("具体 G 数——正文写「看钱差对不对」不写具体金额", BODY_MONEY_G_RE),
    ("具体钱数（钱/gold+数字）——正文写规则不写具体金额", BODY_MONEY_CN_RE),
)


class StateError(ValueError):
    pass


def lint_body(body: str) -> None:
    """正文机械闸：命中即拒。frontmatter evidence 不在闸内（那里就该放具体值），
    模板生成侧（consolidate）对 cmd 里的坐标/金额做占位符消毒后再进正文。"""
    hits = [hint for hint, rx in BODY_GATE_HINTS if rx.search(body or "")]
    if hits:
        raise StateError("正文机械闸拒绝: " + "; ".join(hits))


def sanitize_for_body(s: str) -> str:
    """evidence/cmd 进正文前的消毒：具体值→占位符（规则保留，数值点回 evidence）。"""
    s = BODY_COORD_RE.sub("(坐标)", s)
    s = BODY_COORD_JSON_RE.sub('"x":(坐标),"y":(坐标)', s)
    s = BODY_MONEY_G_RE.sub("(金额)", s)
    s = BODY_MONEY_CN_RE.sub("(金额)", s)
    return s


def is_tested(front: dict) -> bool:
    return int(front.get("test_count", 0) or 0) > 0


def normalize_ledger(front: dict) -> dict:
    """补台账默认字段（旧卡无这些键也能被 feedback/retrieve 处理）。"""
    front.setdefault("counter", COUNTER_BASELINE)
    front.setdefault("test_count", 0)
    front.setdefault("strong_ok", 0)
    front.setdefault("strong_fail", 0)
    return front


def rewrite_card(old_front: dict, old_body: str,
                 new_front: dict, new_body: str) -> dict:
    """AWM 整份重写 + SkillWeaver 清零——唯一写路径（merge 阈值/合并二审已删除）。

    返回重写后的 front。内容（正文或语义字段）没变就不动版本/台账；
    变了就 version+1、test_count=0、counter 回基线、状态回 candidate
    （deprecated 永不复活：保持原态）。lint 不过整份拒绝，旧卡原样留盘。"""
    lint_body(new_body)
    new_front = normalize_ledger(dict(new_front))
    changed = (new_body.rstrip() != old_body.rstrip()) or any(
        old_front.get(k) != new_front.get(k) for k in SEMANTIC_FIELDS)
    if changed:
        new_front["version"] = int(old_front.get("version", 1)) + 1
        new_front["test_count"] = 0
        new_front["counter"] = COUNTER_BASELINE
        if old_front.get("state") == "deprecated":
            new_front["state"] = "deprecated"      # 打脸不删卡，也不许改写复活
            new_front["superseded_by"] = old_front.get("superseded_by")
        else:
            new_front["state"] = "candidate"
    else:
        # 内容没变：台账属于这张卡，旧值优先（不许被新 front 的缺省值悄悄清掉）
        for k in ("test_count", "counter", "strong_ok", "strong_fail"):
            new_front[k] = old_front.get(k, new_front.get(k, 0) or 0)
        new_front["state"] = old_front.get("state", "candidate")
        new_front["version"] = int(old_front.get("version", 1))
    return new_front


def render_skill(front: dict, body: str) -> str:
    """SKILL.md 文本：--- JSON frontmatter --- + 正文。"""
    for k in ("name", "description", "state"):
        if not front.get(k):
            raise StateError("frontmatter 缺必填字段: %s" % k)
    if front["state"] not in STATES:
        raise StateError("非法状态: %s" % front["state"])
    return "---\n%s\n---\n%s" % (json.dumps(front, ensure_ascii=False,
                                            sort_keys=True, indent=1), body.rstrip() + "\n")


def parse_skill(text: str):
    """SKILL.md → (front dict, body str)。front 必须是合法 JSON。"""
    if not text.startswith("---"):
        raise StateError("SKILL.md 缺 frontmatter")
    end = text.find("\n---", 3)
    if end < 0:
        raise StateError("frontmatter 未闭合")
    front = json.loads(text[3:end].strip())
    body = text[end + 4:].lstrip("\n")
    return front, body


def load_skill(path: str):
    with open(path, encoding="utf-8") as f:
        return parse_skill(f.read())


import contextlib


@contextlib.contextmanager
def file_lock(path):
    """单机文件锁（评审 11-12 P1-4）：flock 独占；点前缀锁文件不入检索。"""
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
    """按卡锁：覆盖「读旧卡→改→写→记账」全程（评审 11-12 P1-4）。"""
    with file_lock(os.path.join(skills_dir, name, ".lock")):
        yield


def save_skill(path: str, front: dict, body: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    lint_body(body)                       # 写盘前过闸（rewrite_card 也过，双保险）
    text = render_skill(front, body)
    # 原子写（评审 11-12 P1-4）：中途退出不留半截卡——
    # 同目录临时文件 + fsync + os.replace（同盘 rename 原子）
    import tempfile
    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(prefix=".skill.", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
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
        raise
    return path


# 外部确认主体白名单（评审 11-12 P2）：自由文本不算确认
_CONFIRM_ACTORS = ("owner",)


def transition(front: dict, new_state: str, actor: str) -> dict:
    """状态机闸门。机器只许降不许升：升级必须 actor 标明外部确认。
    升级闸只认 is_tested（SkillWeaver：任何内容改动都清零验证，
    「induction only from verified」在本仓的机械等价 = 没被真实执行过的卡爬不了级）。"""
    old = front.get("state", "candidate")
    if new_state not in STATES:
        raise StateError("未知状态: %s" % new_state)
    if (old, new_state) not in LEGAL:
        raise StateError("非法迁移: %s → %s（升级必须逐级，机器禁止越级）" % (old, new_state))
    if new_state in ("observed", "verified") and old != new_state:
        if actor == "machine":
            raise StateError("机器禁止升级（%s→%s 需外部确认）" % (old, new_state))
        if actor not in _CONFIRM_ACTORS:
            raise StateError("actor=%r 不是确认主体白名单 %s（自由文本不算外部确认"
                             "——评审 11-12 P2）" % (actor, _CONFIRM_ACTORS))
        if not is_tested(front):
            raise StateError("test_count=0（改过就没再被真实执行过），不许升级 %s→%s"
                             % (old, new_state))
    front = dict(front)
    front["state"] = new_state
    if new_state == "deprecated":
        front.setdefault("superseded_by", None)  # 打脸不删卡，留证据链
    front["version"] = int(front.get("version", 1)) + 1
    return front
