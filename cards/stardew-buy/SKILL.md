---
{
 "description": "星露谷 buy：失败 0 例 / 成功 3 例",
 "evidence": [
  {
   "action": "ee915bffa1486724",
   "call_id": "call_ed7a67df5f944a62b9cddbe0",
   "cmd": "./bin/sd raw /buy '{\"id\":\"(T)BambooPole\"}' 2>&1|tail -c 130",
   "episode": "617c0793c79eed05",
   "out": "{\"ok\": true, \"bought\": \"Bamboo Pole\", \"quantity\": 1, \"unitPrice\": 100, \"totalCost\": 100, \"remainingGold\": 517294} ⟨{\"tot",
   "rule": "buy_charged_100",
   "session": "onebot-private-1000000001-mvp9wgp5",
   "sub_idx": 0,
   "verdict": "success"
  },
  {
   "action": "de957b0b3a4fc97f",
   "call_id": "call_8f2348e7e0474fe79120f3ce",
   "cmd": "./bin/sd raw /buy '{\"id\":\"(O)685\",\"quantity\":200,\"price\":5}' 2>&1|tail -c 120",
   "episode": "1418d654955284c7",
   "out": "{\"ok\": true, \"bought\": \"Bait\", \"quantity\": 200, \"unitPrice\": 5, \"totalCost\": 1000, \"remainingGold\": 529549} Id\": \"(O)685",
   "rule": "buy_charged_1000",
   "session": "onebot-private-1000000001-mvp9wgp5",
   "sub_idx": 1,
   "verdict": "success"
  },
  {
   "action": "3f62a7fae2541006",
   "call_id": "call_830f6909a5ba4b8480034d29",
   "cmd": "./bin/sd raw /buy '{\"id\":\"(O)481\",\"quantity\":20,\"price\":160}' 2>&1|tail -c 120",
   "episode": "8eecf03180eca0b5",
   "out": "{\"ok\": true, \"bought\": \"Blueberry Seeds\", \"quantity\": 20, \"unitPrice\": 160, \"totalCost\": 3200, \"remainingGold\": 532099} ",
   "rule": "buy_charged_3200",
   "session": "onebot-private-1000000001-mvp9wgp5",
   "sub_idx": 0,
   "verdict": "success"
  }
 ],
 "name": "stardew-buy",
 "retrieval_anchor": "bamboopole bin buy id price quantity raw sd tail",
 "state": "candidate",
 "task_query": "对 stardew.buy 的结果负责时先查这张卡",
 "tool_key": "stardew.buy",
 "version": 1
}
---
# stardew.buy 的动手卡

## 什么时候用
对 stardew.buy 的结果负责时先查这张卡

## 成功轨
- `./bin/sd raw /buy '{"id":"(T)BambooPole"}' 2>&1|tail -c 130` → 效果已核（buy_charged_100）
- `./bin/sd raw /buy '{"id":"(O)685","quantity":200,"price":5}' 2>&1|tail -c 120` → 效果已核（buy_charged_1000）
- `./bin/sd raw /buy '{"id":"(O)481","quantity":20,"price":160}' 2>&1|tail -c 120` → 效果已核（buy_charged_3200）

## 失败轨
（暂无失败轨证据——本卡目前只有成功形态）

具体数值与完整输出一律见 frontmatter evidence（点回 actions.jsonl），正文只写规则不抄数值。
