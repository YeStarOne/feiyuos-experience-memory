---
{
 "name": "stardew-mine-fight",
 "description": "下矿打怪前看这张：借用她的身体（BWF 借身 7843 / MCP）前后必须清借身标记，忘了会让主持和她抢同一个身体。含打怪前必查的活体判据。",
 "tool_key": "stardew.mine",
 "state": "candidate",
 "version": 1,
 "task_query": "下矿打怪 借身 BWF 7843 MCP 清标记 抢身体",
 "retrieval_anchor": "下矿 打怪 借身 BWF borrow 7843 MCP 清标记 抢身体 heal 探索矿井 explore_mine",
 "evidence": [
  {
   "verdict": "unknown",
   "note": "这张卡是从人工排障笔记转来的（2026-11-12 补 frontmatter 时才进目录），不是从 episode 归纳出来的，所以 evidence 为空。等真跑一次下矿再回填。",
   "date": "2026-11-12"
  }
 ]
}
---

# 星露谷挖矿打怪（fish 客户端 7843 / BWF MCP）

## 前置纪律（主持定的，破一次冻结一天）
1. 跑 explore_mine 前必须 `fin exec --ps "Set-Content 'D:/StardewAI/.bwf-borrow-fish' -Value (Get-Date -Format o)"` —— 自己的挂机循环见到它会让路
2. 跑完立刻删：`Remove-Item 'D:/StardewAI/.bwf-borrow-fish'` 并 `Test-Path` 确认；忘删 = 冻结一整天
3. 新手期只下 1~2 层，见好就收；低血先 POST 7843/heal

## 调用方式（SSE，用 curl 别用 urllib）
```
curl -sN --max-time 590 -X POST 'http://127.0.0.1:47852/mcp' \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  --data '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"explore_mine","arguments":{"levels":2}}}'
```
- 主口 17852 断流就用兜底 127.0.0.1:47852（实测兜底稳）
- python urllib 会被 Connection reset 掐断 SSE，**别用它跑长动作**
- 连接被掐 ≠ 失败：动作可能在跑。动作互斥，用 say/get_state 试探；say 参数是 `text` 不是 `message`

## 动作后对账（probe 卡通用）
- get_state 读：地点 UndergroundMineN / 生命 / 体力 / 技能等级（采矿/战斗）
- 生命 88/100 没到 heal 线就别浪费；楼梯每5层有电梯
- 出口：interact 出口梯子坐标（get_state 会给）

## 战果记录模板
到第几层 / 打了几只怪（当前层剩几只） / 血线 / 技能升级没 / 信号删没删

## ⚠️ 第四条端点坑（2026-11-12 教官裁决）
**warp_into 对 farmhand 是裸改内存**：player.currentLocation = 目标点，不走多人同步；farmhand 位置由主持权威裁决，下一拍就被拍回去。表象 = 返回 ok 但 /state 读回 Mountain/旧位置。
- 正规进门路径：**explore_mine {levels:N, start_level:0}**（BWF 官方，已验证不弹）
- 矿内行为：层1无怪；敲石出楼梯是概率事件，/ladder 只认 UndergroundMine* 图
- 身体协调只走 .bwf-borrow-fish flag，**永远别碰 loopctl**（那管主持的循环，动了它 watchdog 会无视 hold 强拉）

## ⚠️ 运行时雷（2026-11-14 教官判例）
`node --check` 过 ≠ 能跑——「给 const 赋值」这类运行时错误语法检查抓不到，loop 的雷要到夜里第一次踩到那行才炸（昨晚 07:33 实锤，崩前 warp 已成功所以人没事）。
**改 loop 验收四关：mtime → grep → node --check → 重启并盯第一轮**。少一关都不算闭环。
