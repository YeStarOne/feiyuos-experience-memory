# -*- coding: utf-8 -*-
"""会话日志源：~/.dsh/sessions/<workspace>/<session>/session.v4.jsonl(.zstd)

实测 schema（方案 §1.1）：
  tool/call   data={turn,step,callId,name,arguments(JSON字符串)}
  tool/result data={message:{toolCallId,content[].text,isError}, meta.cwd}
  turn/start|turn/end data={turn,...}
"""
import glob
import json
import os
import subprocess

OWNER_PREFIXES = (
    "onebot-private-1792612052",   # 主会话（78% 调用在这里）
    "onebot-private-1848785393",
    "onebot-group-427592205",
)
AUTOMATION_PREFIX = "dsh-automation-session"


def classify(session_name: str) -> str:
    """归属闸（方案 §5-4）：automation 不是她的游戏经验。"""
    if session_name.startswith(AUTOMATION_PREFIX):
        return "automation"
    if session_name.startswith(OWNER_PREFIXES):
        return "owner"
    return "other-uuid"


def session_files(root: str):
    """返回 [(session_name, path)]，按名字排序保证确定性。"""
    out = []
    for d in sorted(glob.glob(os.path.join(root, "*"))):
        for fn in ("session.v4.jsonl.zstd", "session.v4.jsonl"):
            p = os.path.join(d, fn)
            if os.path.isfile(p):
                out.append((os.path.basename(d), p))
                break
    return out


def iter_events(path: str):
    """逐行产出事件 dict。zstd 走子进程解压；plain jsonl 直接读（测试用）。"""
    if path.endswith(".zstd"):
        raw = subprocess.run(["zstd", "-dc", path], capture_output=True,
                             timeout=180).stdout.decode("utf-8", "replace")
        lines = raw.splitlines()
    else:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except Exception:
            continue
