# -*- coding: utf-8 -*-
"""SkillRegistry：技能卡 = SKILL.md 文件（原生 dsh-skill 机制，热监听加文件即生效）。

设计要点（方案 §7-8 / OpenViking+Voyager 对齐）：
- frontmatter 机器算（v2.1：tool_key/state/valid_from 双标/evidence/confidence/
  contradicts/superseded_by/retrieval_anchor/task_query/version），正文给人读。
- frontmatter 用 JSON 序列化（JSON ⊂ YAML，dsh 的 frontmatter 解析器直接吃，
  我们这边 json 往返零依赖、零歧义）。
- 状态机闸门：candidate→observed→verified 只能逐级升；任何态可降 deprecated
  （现实打脸不删卡——Voyager 反证"只记成功"的学不会失败）；同名覆盖后旧卡留盘。
- 禁止自动 verified：升级必须外部确认（owner/二审），机器只许降不许升。"""
import json
import os

STATES = ("candidate", "observed", "verified", "deprecated")

# 允许的状态迁移：升级逐级 + 全域可降 deprecated + 原态更新证据
LEGAL = {
    ("candidate", "candidate"), ("candidate", "observed"), ("candidate", "deprecated"),
    ("observed", "observed"), ("observed", "verified"), ("observed", "deprecated"),
    ("verified", "verified"), ("verified", "deprecated"),
    ("deprecated", "deprecated"),   # 降级后仍可补证据/改指向往，但永不可复活
}


class StateError(ValueError):
    pass


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
    return parse_skill(open(path, encoding="utf-8").read())


def save_skill(path: str, front: dict, body: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    text = render_skill(front, body)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def transition(front: dict, new_state: str, actor: str) -> dict:
    """状态机闸门。机器只许降不许升：升级必须 actor 标明外部确认。"""
    old = front.get("state", "candidate")
    if new_state not in STATES:
        raise StateError("未知状态: %s" % new_state)
    if (old, new_state) not in LEGAL:
        raise StateError("非法迁移: %s → %s（升级必须逐级，机器禁止越级）" % (old, new_state))
    if new_state in ("observed", "verified") and old != new_state:
        if actor == "machine":
            raise StateError("机器禁止升级（%s→%s 需外部确认）" % (old, new_state))
    front = dict(front)
    front["state"] = new_state
    if new_state == "deprecated":
        front.setdefault("superseded_by", None)  # 打脸不删卡，留证据链
    front["version"] = int(front.get("version", 1)) + 1
    return front
