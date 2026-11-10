# -*- coding: utf-8 -*-
"""expmem 命令行。S0：replay；S2 收尾：search / feedback / induct。

用法：
  python3 -m expmem.cli replay <sessions-root> --out <dir>
  python3 -m expmem.cli search "存仓库对账"
  python3 -m expmem.cli feedback <card-name> --kind strong --verdict success \
      --executed [--shown id1,id2]
  python3 -m expmem.cli induct <sessions-root> --out <dir> [--skills <dir>]
"""
import argparse
import json
import os

from . import api


def _print(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="expmem")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rp = sub.add_parser("replay", help="只读重放会话日志 → actions/episodes/metrics")
    rp.add_argument("root", help="~/.dsh/sessions/<workspace> 目录")
    rp.add_argument("--out", required=True, help="输出目录（幂等，可重跑）")
    sp = sub.add_parser("search", help="检索技能卡目录行（不递正文）")
    sp.add_argument("query")
    sp.add_argument("--domain", default=None)
    fp = sub.add_parser("feedback", help="对卡落 ExpeL 台账（A-MEM 三守卫）")
    fp.add_argument("name", help="卡名（必须在本轮 shown 里，除非 --no-shown）")
    fp.add_argument("--kind", required=True,
                    choices=("weak_positive", "weak_negative", "strong"))
    fp.add_argument("--verdict", default=None, choices=("success", "failure"))
    fp.add_argument("--executed", action="store_true")
    fp.add_argument("--shown", default="",
                    help="本轮展示过的卡名，逗号分隔；空=人工无上下文（守卫①放行）")
    ip = sub.add_parser("induct", help="AWM 整份重写归纳 → specs/confirmations")
    ip.add_argument("root", help="~/.dsh/sessions/<workspace> 目录")
    ip.add_argument("--out", required=True, help="specs 输出目录")
    ip.add_argument("--skills", default=None,
                    help="既有卡目录（默认 EXPMEM_SKILLS_DIR 或 ~/.dsh/skills）")
    ip.add_argument("--apply", action="store_true",
                    help="把 spec 落成 SKILL.md（走 rewrite_card，SkillWeaver 清零）")
    op = sub.add_parser("orphan", help="双向 orphan 审计（card↔action↔episode 三向点得回）")
    op.add_argument("--cards", required=True, help="卡目录（~/.dsh/skills）")
    op.add_argument("--actions", required=True, help="replay 产物 actions.jsonl")
    op.add_argument("--episodes", required=True, help="replay 产物 episodes.jsonl")
    tp = sub.add_parser("settle", help="ACE 自动打标结算（幂等：已结算回合跳过）")
    tp.add_argument("root", help="~/.dsh/sessions/<workspace> 目录")
    tp.add_argument("--out", required=True, help="replay 产物目录（幂等复用）")
    tp.add_argument("--skills", default=None,
                    help="卡目录（默认 EXPMEM_SKILLS_DIR 或 ~/.dsh/skills）")
    args = ap.parse_args(argv)
    if args.cmd == "replay":
        _print(api.replay(args.root, args.out))
    elif args.cmd == "search":
        _print(api.retrieve_skills(args.query, domain=args.domain))
    elif args.cmd == "feedback":
        fb = {"name": args.name, "kind": args.kind}
        if args.verdict:
            fb["verdict"] = args.verdict
        if args.executed:
            fb["executed"] = True
        shown = tuple(s.strip() for s in args.shown.split(",") if s.strip())
        _print(api.record_skill_feedback(fb, shown_ids=shown))
    elif args.cmd == "induct":
        actions, episodes = _load_replay(args.root, args.out)
        from .retrieve import load_catalog
        skills_dir = args.skills or api._skills_dir()
        specs, ops, dropped = _induct(episodes, actions,
                                      load_catalog(skills_dir) if os.path.isdir(skills_dir) else [])
        _print({"specs": specs, "ledger_ops": ops, "dropped": dropped})
        if args.apply:
            n = _apply_specs(specs, skills_dir)
            print("applied %d cards → %s" % (n, skills_dir))
    elif args.cmd == "orphan":
        from .orphan import audit_dirs
        _print(audit_dirs(args.cards, args.actions, args.episodes))
    elif args.cmd == "settle":
        actions, episodes = _load_replay(args.root, args.out)
        _print(api.settle_auto_marks(episodes, actions,
                                     skills_dir=args.skills or api._skills_dir()))


def _load_replay(root, out):
    """replay 一次拿 actions/episodes（幂等目录复用）。"""
    import json as J
    api.replay(root, out)
    with open(os.path.join(out, "actions.jsonl"), encoding="utf-8") as f:
        actions = [J.loads(ln) for ln in f if ln.strip()]
    with open(os.path.join(out, "episodes.jsonl"), encoding="utf-8") as f:
        episodes = [J.loads(ln) for ln in f if ln.strip()]
    return actions, episodes


def _induct(episodes, actions, existing_fronts):
    from .consolidate import induct_specs
    return induct_specs(episodes, actions, existing_fronts=existing_fronts)


def _apply_specs(specs, skills_dir) -> int:
    """spec → SKILL.md：唯一写路径 rewrite_card（AWM 整份重写 + SkillWeaver 清零）。
    旧卡不存在则按 spec 新建；正文闸不过的 spec 整份跳过并计数。"""
    from .skills import load_skill, rewrite_card, save_skill
    n = 0
    for s in specs:
        path = os.path.join(skills_dir, s["name"], "SKILL.md")
        front = {k: v for k, v in s.items()
                 if k not in ("body", "stats", "_path")}
        try:
            if os.path.exists(path):
                old_front, old_body = load_skill(path)
                new_front = rewrite_card(old_front, old_body, front, s["body"])
                save_skill(path, new_front, s["body"])
            else:
                save_skill(path, front, s["body"])
            n += 1
        except ValueError as e:      # StateError ⊂ ValueError：正文闸/状态机拒绝
            print("skip %s: %s" % (s.get("name"), e))
    return n


if __name__ == "__main__":
    main()
