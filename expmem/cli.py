# -*- coding: utf-8 -*-
"""expmem 命令行。S0：replay。
用法：python3 -m expmem.cli replay <sessions-root> --out <dir>"""
import argparse
import json

from . import api


def main(argv=None):
    ap = argparse.ArgumentParser(prog="expmem")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rp = sub.add_parser("replay", help="只读重放会话日志 → actions/episodes/metrics")
    rp.add_argument("root", help="~/.dsh/sessions/<workspace> 目录")
    rp.add_argument("--out", required=True, help="输出目录（幂等，可重跑）")
    args = ap.parse_args(argv)
    if args.cmd == "replay":
        m = api.replay(args.root, args.out)
        print(json.dumps(m, ensure_ascii=False, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
