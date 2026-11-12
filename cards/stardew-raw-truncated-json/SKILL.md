---
{
 "name": "stardew-raw-truncated-json",
 "description": "sd raw 拿到的 JSON 解析炸 / 报 Invalid control character / Expecting value 时看这张：不是你的 python 写错了，是 sd raw 把长输出截成了非法 JSON（已修，2026-11-13）。要完整 /state 用 sd state。",
 "tool_key": "stardew.raw",
 "state": "verified",
 "version": 2,
 "task_query": "sd raw 输出 json.load 报错 Invalid control character Expecting value 解析失败",
 "retrieval_anchor": "sd raw json.load JSONDecodeError Invalid control character Expecting value 解析失败 Traceback state 截断",
 "evidence": [
  {"verdict": "failure",
   "cmd": "~/qq-workspace/bin/sd raw /state 2>/dev/null | python3 -c \"import sys,json;d=json.load(sys.stdin);print(d[chr(39)+\"player\"+chr(39)])\"",
   "out": "json.decoder.JSONDecodeError: Invalid control character at: line 1 column 1501 (char 1500)",
   "note": "sd raw /state 只吐 1527 字节就断在 \"time\": { 处；直连 47843 同一时刻返回 13036 字节合法 JSON。",
   "date": "2026-11-13"},
  {"verdict": "success",
   "cmd": "~/qq-workspace/bin/sd raw /state 2>/dev/null | python3 -c \"import sys,json;d=json.load(sys.stdin);print(d[chr(39)+\"player\"+chr(39)][chr(39)+\"money\"+chr(39)])\"",
   "out": "1412525",
   "note": "修后：2546 字合法 JSON，player/time/location 全保，只折 inventory(9044字)+recent_events。",
   "date": "2026-11-13"}
 ]
}
---

# sd raw 解析炸了，先怀疑工具不是怀疑自己

## 什么时候用
你写 `sd raw <端点>` 后面接 `| python3 -c "json.load(...)"`，然后看到这几种报错时：
- `json.decoder.JSONDecodeError: Invalid control character at: line 1 column 1501`
- `Expecting value: line 1 column 1 (char 0)`
- 输出末尾停在一个 `"time": {` 或半截字符串上

## 第一件事：分清是端点坏还是工具坏
```
# 直连桥，看真实字节数（这是端点的真面目）
curl -s -H "Host: localhost" -m 10 "http://127.0.0.1:47843/state" -o /tmp/a.json -w "size=%{size_download}\n"
python3 -c "import json;d=json.load(open(\"/tmp/a.json\"));print(\"直连 OK\",len(d))"

# 再看 sd 给的是多少字节
~/qq-workspace/bin/sd raw /state 2>/dev/null | wc -c
```
**两者字节数差一个数量级 = 工具层截断，不是游戏/端点的问题。**
（实测：直连 13036 字节合法，sd 只给 1527 字节，切在字符串中间。）

## 判据（别靠"感觉像"）
- 端点真坏的特征：HTTP 非 200、或 `{"ok": false, ...}`、或直连也解析失败
- 工具截断的特征：**直连能解析、sd 不能**，且 sd 输出长度刚好是个整数边界
- 11-13 这次就是后者：`bin/sd` 的 `cmd_raw` 里写了 `[:1500]`

## 已经修好了（2026-11-13）
`bin/sd` 现在超长时**折叠大字段**而不是切字符：
- 预算 3000 字，`player` / `time` / `location` / `activeMenu` 属 CORE，永不折叠
- 列表类先折，给 `{_count, _sample}`，你仍能看到"包里前一件是什么"
- 被折了哪些字段，返回里 `_folded` 会列出来

所以现在 `sd raw /state` 可以直接 `json.load` 拿 `player.money` / `time.timeOfDay`。

## 日常更省事的做法
只要一行摘要就用 `sd state`（不经过 raw，本来就不截断）：
```
~/qq-workspace/bin/sd state
# Cabin(9,9) 冬6 06:00 | 体力219/270 HP100 | 钱1412525 | 手:Copper Pickaxe | 包26/36
```
要具体字段才用 `sd raw /state`。

## 别再做的两件事
1. **别把 `sd raw` 的输出当"完整状态"用**——它按设计会折叠，要全量看 `sd state --json` 或直连 curl
2. **别自己给 sd 输出加 `[:N]`**——你就是第二个造这个 bug 的人；截断请用 `json` 层折叠

## 历史（为什么这张卡值得留）
她的 31 次 `/state` 调用里有 6 次（19.4%）报 Traceback，全都因为这条。而同期
172 次 sd 端点调用里，**端点本身 0 失败**——她一直在为工具的假失败付出重试成本。
