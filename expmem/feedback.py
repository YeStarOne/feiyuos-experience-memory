# -*- coding: utf-8 -*-
"""record_skill_feedback 真身（S2 收尾，intel 四仓裁定：ExpeL + A-MEM）。

ExpeL (text, counter) 台账：
  - counter 基线 1 =「在书里」；weak_positive / strong success 加，
    weak_negative / strong failure 减；counter≤0 = 移出书（retrieve 不再装载，
    卡文件留盘——打脸不删卡的纪律不动，只是不再进她的注入目录）；
  - conf 只吃 strong：strong = 这张卡被执行过 且 当集 verdict=success/failure。
    conf = strong_ok/(strong_ok+strong_fail)，weak 反馈永远不碰 confidence；
  - counter 降序 = 注入序（retrieve._rank 里兑现）。

A-MEM 三守卫（防反馈通道本身成为污染源）：
  ① 只认本轮已展示的卡名（shown_ids）——模型没见过却「记得」的引用一律拒收；
  ② 解析失败 fail-closed——整条拒收，绝不半套用（counting 归零也算改了）；
  ③ 空列表不清既有值——fb 里带 "contradicts": [] / "tags": [] 不许抹掉旧值。

本模块是纯函数（front 进 front 出），落盘由调用方（api.record_skill_feedback）
走 skills.rewrite_card 之外的独立通道：feedback 不改正文，不触发 SkillWeaver
清零（台账追加不是内容改动——否则每次反馈都把验证清零，死循环）。"""
import json

KINDS = ("weak_positive", "weak_negative", "strong")
LIST_FIELDS = ("contradicts", "tags")   # A-MEM 守卫③覆盖的列表字段


def parse_feedback(raw) -> dict:
    """解析并校验一条反馈；不合法抛 ValueError（守卫②：整条拒收，无半套用）。"""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raise ValueError("feedback 不是合法 JSON")
    if not isinstance(raw, dict):
        raise ValueError("feedback 必须是对象")
    name = raw.get("name") or ""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("feedback 缺 name")
    kind = raw.get("kind")
    if kind not in KINDS:
        raise ValueError("未知 kind: %r（允许 %s）" % (kind, ", ".join(KINDS)))
    fb = {"name": name.strip(), "kind": kind,
          "note": str(raw.get("note") or "")[:200]}
    if kind == "strong":
        verdict = raw.get("verdict")
        if verdict not in ("success", "failure"):
            raise ValueError("strong 反馈必须带 verdict=success|failure")
        if raw.get("executed") is not True:
            raise ValueError("strong 反馈必须 executed=true（严格布尔：\"false\"/1/"
                             "1.0 一律不收——评审 11-12 P2）")
        fb["verdict"] = verdict
        fb["executed"] = True
    for k in LIST_FIELDS:
        if k in raw:
            v = raw[k]
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                raise ValueError("%s 必须是字符串列表" % k)
            fb[k] = v          # 原样带过去（空列表在 apply 阶段按守卫③丢弃）
    return fb


def apply_feedback(front: dict, fb: dict, shown_ids=()) -> tuple:
    """把一条已 parse 的反馈套到卡 front 上。返回 (front', applied, reason)。

    守卫①：shown_ids 非空时，name 不在其中 → 拒收（本轮没展示过的卡不许被引用）。
    shown_ids 传 () 表示调用方没有本轮展示上下文（人工 CLI 等）——这时守卫①放行，
    守卫②③照常生效。"""
    front = dict(front)
    name = fb.get("name", "")
    if shown_ids and name not in shown_ids:
        return front, False, "not_shown_this_round"
    kind = fb["kind"]
    counter = int(front.get("counter", 1) or 0)
    if kind == "weak_positive":
        counter += 1
    elif kind == "weak_negative":
        counter -= 1
    else:  # strong
        ok = int(front.get("strong_ok", 0) or 0)
        fail = int(front.get("strong_fail", 0) or 0)
        if fb["verdict"] == "success":
            ok += 1
            counter += 1
            front["test_count"] = int(front.get("test_count", 0) or 0) + 1
        else:
            fail += 1
            counter -= 1
        front["strong_ok"], front["strong_fail"] = ok, fail
        front["confidence"] = round(ok / (ok + fail), 2)   # conf 只吃 strong
    front["counter"] = counter
    # 守卫③：显式给出的**非空**列表才覆盖；空列表/缺省都不清既有值
    for k in LIST_FIELDS:
        if fb.get(k):
            front[k] = list(fb[k])
    return front, True, ""


def record_feedback(front: dict, raw, shown_ids=()) -> tuple:
    """parse + apply 一步到位。返回 (front', applied, reason)。
    parse 失败或 name 与这张卡对不上时原 front 原样返回（守卫②：fail-closed）。"""
    try:
        fb = parse_feedback(raw)
    except ValueError as e:
        return dict(front), False, "parse_rejected: %s" % e
    fname = front.get("name") or ""
    if fname and fb["name"] != fname:
        return dict(front), False, "name_mismatch: %s ≠ %s" % (fb["name"], fname)
    return apply_feedback(front, fb, shown_ids=shown_ids)


def auto_from_episode(front: dict, verdict: str, used: bool = False,
                      auto_key: str = "") -> tuple:
    """ACE 对齐（intel 11-11 交付：「成功回合也打 helpful 标，不止失败才反馈」）：
    卡被引用且当集 verdict=success → 自动 weak_positive。
    used **缺省 False**（intel 复审：缺省往好猜=每个成功回合都记给在册卡；
    拿不到就当作没用到，与 failure 不自动标同一条纪律）。调用方必须从
    api 注入 manifest 里取 used（本轮 refs.name ∩ 当集）。
    机器自动标只动台账，不碰 conf/test_count——conf 只吃 strong，strong 必须
    显式反馈带 executed=true。failure 不自动打标：环境性失败不许由机器归罪于卡
    （那要显式反馈的权力）。返回 (front, applied, reason)。"""
    if not used:
        return dict(front), False, "not_used"
    if verdict != "success":
        return dict(front), False, "failure_not_auto_marked"
    new_front, applied, reason = apply_feedback(
        front, {"name": front.get("name", ""),
                "kind": "weak_positive",
                "note": "auto: success turn used this card"})
    if applied and auto_key:
        # cmb-P2-residual（S4 必带）：按卡幂等——front 记 auto_marks，同一张卡
        # 同一 (session,turn) 永不重标。turn 级 .settled.jsonl 只做性能优化，
        # 不做正确性依赖（半截卡拖住回合结清时，好卡也不能每轮 +1）。
        marks = list(new_front.get("auto_marks") or [])
        if auto_key not in marks:
            marks.append(auto_key)
        new_front["auto_marks"] = marks
    return new_front, applied, reason
