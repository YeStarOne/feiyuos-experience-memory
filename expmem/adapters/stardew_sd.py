# -*- coding: utf-8 -*-
"""星露谷 adapter：她的游戏动作没有专用工具，全写在 bash 字符串里（方案 §1.2）。

口径（11-10 定，比初版 spike 更准的两处）：
  - heredoc：**行级**跳过 `<<MARK … MARK` 的正文体，终止符之后的真命令照算
    （初版 spike 一刀切到 `<<`，会把 heredoc 之后的命令也丢掉）；
  - raw 路径：`sd raw <path>` / `$(sd raw …)` 都算，剥掉子 shell 的右括号。
verb 级动作与 raw 路径是两把尺子，有重叠，分开计数。
"""
import re

NAME = "stardew_sd"

VERBS = frozenset(
    "state inv near shot warp move act use water plant harvest dig "
    "sleep buy sell day key wait raw farm help".split()
)
READ_VERBS = frozenset("state inv near shot farm help".split())

SD_RE = re.compile(
    # P2-10：锚点加 `(` —— `(sd state)` / `x=$(sd state)` 是真调用，此前被静默丢
    # （与白名单同类损失）；raw 的路径另有 RAW_RE/RAW_ANCHOR_RE 两把尺，重叠是有意的。
    # ⭐925-928 实锤（11-11）：她学会给 sd 调用加 `timeout N` 前缀（桥死时挂过
    # 60s 教训）——timeout/nohup/env/nice/sudo 包装不是新命令，必须跳过包装
    # 再看 sd，否则她整个「带超时的谨慎调用」时代对采集层隐形。
    r"(?:^|[;&|(\n])\s*(?:(?:timeout\s+[\w.]+|nohup|env\s+\S+|nice\s+(?:-n\s+)?-?\d+|sudo)"
    r"\s+)*(?:[\w./~-]*/)?sd(?:\.py)?\s+(?:--?[a-z_]+\s+)*([a-z_]+)"
)
RAW_RE = re.compile(r"(?:[\w./~-]*/)?sd(?:\.py)?\s+raw\s+(\S+)")
# 锚定的 raw 子 shell 调用：$(sd raw /store …) 或 ; sd raw /x —— 是真动作，要进 actions
# （11-11 实跑教训：3 条 stored:[] 里 2 条的 /store 藏在子 shell 里，verb 扫描看不见）
RAW_ANCHOR_RE = re.compile(
    r"(?:^|[;&|(]\s*(?:\$\(\s*)?(?:(?:timeout\s+[\w.]+|nohup|env\s+\S+|nice\s+(?:-n\s+)?-?\d+|sudo)\s+)*)"
    r"(?:[\w./~-]*/)?sd(?:\.py)?\s+raw\s+(\S+)")
HEREDOC_RE = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?")
RAW_TAIL_RE = re.compile(r"[);;&|`].*$")  # 子 shell 右括号 / 分隔符尾巴


def strip_heredocs(cmd: str) -> str:
    """行级跳过 heredoc 体：从 <<MARK 起到独立一行 MARK 止。"""
    out, skip = [], None
    for ln in cmd.split("\n"):
        if skip is not None:
            if ln.strip() == skip:
                skip = None
            continue
        m = HEREDOC_RE.search(ln)
        if m:
            skip = m.group(1)
            out.append(HEREDOC_RE.sub("", ln))
            continue
        out.append(ln)
    return "\n".join(out)


def clean_raw_path(p: str) -> str:
    p = p.strip().strip("'\"")
    return RAW_TAIL_RE.sub("", p)


def split_cmd(cmd: str):
    cmd = strip_heredocs(cmd)
    return [s.strip() for s in re.split(r"(?:;|&&|\|\||\n)", cmd) if s.strip()]


def verb_calls(cmd: str):
    """[(token, segment)]，段首锚定。

    ⚠️ 11-10 cmb 对账 D1：**白名单过滤已废除**——sd.py 有通用 cmd_action
    分发器（face/select/interact/heal/fishbot…都是真调用），白名单曾静默丢掉
    234 次（18.6%）。现在原样返回 token；known/other 的判定在 episode 层做
    （unknown → verb="other:<token>"，endpoint="ACT <token>"），永不静默丢。"""
    out = []
    for seg in split_cmd(cmd):
        for m in SD_RE.finditer(seg):
            out.append((m.group(1), seg[:200]))
    return out


def raw_paths(cmd: str):
    """[path]，不要求段首（捕捉 $(sd raw …) 子 shell）。"""
    return [clean_raw_path(m.group(1)) for m in RAW_RE.finditer(strip_heredocs(cmd))]


def anchored_raw_paths(cmd: str):
    """[path]，锚定版（命令位/子 shell 位）——补成正式 Action 用的那把尺。"""
    cmd = strip_heredocs(cmd)
    return [clean_raw_path(m.group(1)) for m in RAW_ANCHOR_RE.finditer(cmd)]


def endpoint_for(verb: str, raw_path: str = "") -> str:
    if verb == "raw" and raw_path:
        return "RAW " + raw_path
    if verb in ENDPOINT_READ:
        return ENDPOINT_READ[verb]
    if verb in READ_VERBS:
        return "GET /state"
    return "POST"


ENDPOINT_READ = {
    "state": "GET /state", "inv": "GET /state", "near": "GET /state",
    "farm": "GET /farm_report",
    "shot": "GET /shot",   # P2-10：截图端点不是 /state，此前误归一
}


def match(name: str, args: dict) -> bool:
    if name != "bash":
        return False
    return bool(verb_calls(args.get("command") or "") or raw_paths(args.get("command") or ""))


def parse(args: dict) -> dict:
    cmd = args.get("command") or ""
    verbs = verb_calls(cmd)
    raws = raw_paths(cmd)
    return {"verbs": verbs, "raw_paths": raws}
