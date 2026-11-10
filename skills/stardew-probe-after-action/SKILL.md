---
{
 "body_head": null,
 "confidence": 0.55,
 "contradicts": [],
 "description": "星露谷：每个写动作之后当场 sd state 对账——不当场看结果就没法判成败，事后补看会记错账",
 "evidence": [
  {
   "cmd": "sd raw /store {\"id\":\"Cranberries\"}",
   "episode": "2fd2a2e18628bb73",
   "out": "{\"ok\": true, \"stored\": [], \"chestAt\": {\"x\": 65, \"y\": 15}}",
   "verdict": "failure"
  },
  {
   "cmd": "expmem bench（2026-11-11 全量重放）",
   "episode": "bench-2026-11-11",
   "out": "probe ok 72/1337=5.4%，工具层 isError 全历史 2/1027——不当场对账，成败基本不可判",
   "verdict": "failure"
  }
 ],
 "name": "stardew-probe-after-action",
 "retrieval_anchor": "星露谷 做完动作 检查 成功没有 对账 存进去没 买到没 收到没 state store buy harvest sell use 之后",
 "source": "owner+host@v1",
 "state": "candidate",
 "superseded_by": null,
 "task_query": "在星露谷里完成一个写操作（buy/store/harvest/sell/use）后，同一回合内立即用 sd state 或对应只读端点核对结果（钱差=标价×数量、stored 非空、包差=收获数），确认成败后再继续下一步",
 "tool_key": "stardew.state",
 "valid_from": {
  "date": "2026-11-11",
  "game_day": "冬25"
 },
 "version": 1
}
---
# 动作后当场对账

## 什么时候用
在星露谷里做完任何**写**操作（buy / store / harvest / sell / use / water…）之后。
适用条件：这一步你想要「确认成没成」，而不是「大概齐就行」。

## 步骤
1. 动作完成，**同一回合内**跟一条只读核对，别拖到下一回合：
   - 买东西 → `sd state` 看钱差是否等于 标价×数量（不带 price 会按 ×2 扣钱）
   - 存仓库 → `sd raw /store` 返回的 `stored` 数组必须非空；`stored:[]` = 一件没存进去
   - 收庄稼 → 先 `sd raw /farm_report` 看 ready，收完对包差；ready>0 而收 0 件 = 空转，要停下来查
2. 对不上就当场修正（补参数重做），不要把「看起来 ok」记成成功。

## 为什么
工具返回 ok ≠ 真成功（isError 全历史只有 2/1027=0.2%，三次真实踩坑全在 isError=false 里）。
跨回合补看会记错账：日期/state 滞后、pending 是采样快照、重启后游戏重走前一天。
同回合对账 = 整个经验系统唯一能判「成败」的证据来源。
