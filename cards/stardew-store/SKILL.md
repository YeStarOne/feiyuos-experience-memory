---
{
 "description": "星露谷 store：失败 2 例 / 成功 5 例，主失败形态 noop",
 "evidence": [
  {
   "action": "e33871f348e1f7d5",
   "call_id": "call_00_t5wc2006x2oxwzg0xjd1n0rz",
   "cmd": "./bin/sd raw /store '{\"x\":65,\"y\":15,\"item\":\"Moss\"}' 2>&1|tail -c 100",
   "episode": "8bbfae380adc3abe",
   "failure_class": "noop",
   "out": "warp ok t\": 24}, {\"item\": \"Sap\", \"count\": 4}, {\"item\": \"Moss\", \"count\": 4}], \"ch",
   "session": "onebot-private-1000000001-mvp9wgp5",
   "sub_idx": 1,
   "verdict": "failure"
  },
  {
   "action": "dba8f9c9c73c1d45",
   "call_id": "call_0cadb54d8f3b402cbb308cce",
   "cmd": "do r=$(./bin/sd raw /store \"{\\\"x\\\":52,\\\"y\\\":13,\\\"name\\\":\\\"$n\\\"}\" 2>&1|tail -c 80)",
   "episode": "82de8a939cbe5f88",
   "failure_class": "noop",
   "out": "竹鱼竿 => {\"ok\": true, \"stored\": [], \"chestAt\": {\"x\": 52, \"y\": 13}} BambooPole => {",
   "session": "onebot-private-1000000001-mvp9wgp5",
   "sub_idx": 0,
   "verdict": "failure"
  },
  {
   "action": "b31976a490340ad3",
   "call_id": "call_0c4a1c554305418ab8bef0e8",
   "cmd": "./bin/sd raw /store '{\"x\":67,\"y\":16,\"name\":\"Bullhead\"}' 2>&1|tail -1",
   "episode": "67c8a637d8a72837",
   "out": "{\"ok\": true, \"stored\": [{\"item\": \"Bullhead\", \"count\": 39}], \"chestAt\": {\"x\": 67, \"y\": 16}} {\"ok\": true, \"stored\": [{\"ite",
   "rule": "stored_nonempty",
   "session": "onebot-private-1000000001-mvp9wgp5",
   "sub_idx": 1,
   "verdict": "success"
  },
  {
   "action": "86f5944630830283",
   "call_id": "call_0c4a1c554305418ab8bef0e8",
   "cmd": "./bin/sd raw /store '{\"x\":67,\"y\":16,\"name\":\"Green Algae\"}' 2>&1|tail -1",
   "episode": "67c8a637d8a72837",
   "out": "{\"ok\": true, \"stored\": [{\"item\": \"Bullhead\", \"count\": 39}], \"chestAt\": {\"x\": 67, \"y\": 16}} {\"ok\": true, \"stored\": [{\"ite",
   "rule": "stored_nonempty",
   "session": "onebot-private-1000000001-mvp9wgp5",
   "sub_idx": 2,
   "verdict": "success"
  },
  {
   "action": "f31544a3eae432ba",
   "call_id": "call_0c4a1c554305418ab8bef0e8",
   "cmd": "./bin/sd raw /store '{\"x\":67,\"y\":16,\"name\":\"Chub\"}' 2>&1|tail -1",
   "episode": "67c8a637d8a72837",
   "out": "{\"ok\": true, \"stored\": [{\"item\": \"Bullhead\", \"count\": 39}], \"chestAt\": {\"x\": 67, \"y\": 16}} {\"ok\": true, \"stored\": [{\"ite",
   "rule": "stored_nonempty",
   "session": "onebot-private-1000000001-mvp9wgp5",
   "sub_idx": 3,
   "verdict": "success"
  },
  {
   "action": "de083d118be1bfb9",
   "call_id": "call_0c4a1c554305418ab8bef0e8",
   "cmd": "./bin/sd raw /store '{\"x\":67,\"y\":16,\"name\":\"Largemouth Bass\"}' 2>&1|tail -1",
   "episode": "67c8a637d8a72837",
   "out": "{\"ok\": true, \"stored\": [{\"item\": \"Bullhead\", \"count\": 39}], \"chestAt\": {\"x\": 67, \"y\": 16}} {\"ok\": true, \"stored\": [{\"ite",
   "rule": "stored_nonempty",
   "session": "onebot-private-1000000001-mvp9wgp5",
   "sub_idx": 4,
   "verdict": "success"
  },
  {
   "action": "3f9e5fe7b4bf076b",
   "call_id": "call_00_zxne40jiiw4xm3j3ztut1dv1",
   "cmd": "./bin/sd raw /store '{\"x\":63,\"y\":20,\"name\":\"\"}' 2>&1|tail -c 200",
   "episode": "26309785dfe55a50",
   "out": "{\"ok\": true, \"stored\": [{\"item\": \"Stone\", \"count\": 15}], \"chestAt\": {\"x\": 63, \"y\": 20}} 包36格 | 6:错误物品 (1115) 8:绿藻x18 9:错",
   "rule": "stored_nonempty",
   "session": "onebot-private-1000000001-mvp9wgp5",
   "sub_idx": 0,
   "verdict": "success"
  }
 ],
 "name": "stardew-store",
 "retrieval_anchor": "algae bass bin bullhead chub do green item largemouth moss name raw sd store tail fish english falseok 英文 假成功 鱼名",
 "state": "candidate",
 "task_query": "存仓库后当场确认 stored 非空",
 "tool_key": "stardew.store",
 "version": 2
}
---
# stardew.store 的动手卡

## 什么时候用
存仓库后当场确认 stored 非空

## 成功轨
- `./bin/sd raw /store '{"x":(坐标),"y":(坐标),"name":"Bullhead"}' 2>&1|tail -1` → 效果已核（stored_nonempty）
- `./bin/sd raw /store '{"x":(坐标),"y":(坐标),"name":"Green Algae"}' 2>&1|tail -1` → 效果已核（stored_nonempty）
- `./bin/sd raw /store '{"x":(坐标),"y":(坐标),"name":"Chub"}' 2>&1|tail -1` → 效果已核（stored_nonempty）
- `./bin/sd raw /store '{"x":(坐标),"y":(坐标),"name":"Largemouth Bass"}' 2>&1|tail -1` → 效果已核（stored_nonempty）
- `./bin/sd raw /store '{"x":(坐标),"y":(坐标),"name":""}' 2>&1|tail -c 200` → 效果已核（stored_nonempty）

## 失败轨
- `./bin/sd raw /store '{"x":(坐标),"y":(坐标),"item":"Moss"}' 2>&1|tail -c 100` → 失败形态（noop）
- `do r=$(./bin/sd raw /store "{"x":(坐标),"y":(坐标),\"name\":\"$n\"}" 2>&1|tail -c 80)` → 失败形态（noop）

## 两个假成功坑（11-11 实战补）
- **`ok:true` ≠ 存进去了**：判成败只看 `stored` 非空。`stored:[]` 的两种常见原因——坐标处没箱子（会另给 `No chest at ...`）、**名字对不上**（静默 noop，不报错）。
- **`name` 只认英文物品名**（Bullhead / Green Algae 这类内部名）。inv 回显里的中文显示名（绿藻、竹鱼竿）塞进去一律 `stored:[]` 假成功；事后查箱子读数也用英文名。
- **`name:""` 是 store-all**：把当次能存的全部塞进那只箱子，只想存一样东西时别用；用了就做好包被清空的预期。

具体数值与完整输出一律见 frontmatter evidence（点回 actions.jsonl），正文只写规则不抄数值。
