/**
 * 插件纯逻辑测试（node test.mjs，零依赖，服务器上真跑）。
 * 夹具全部来自她的真会话/真卡证据，不造样本——「校验落在被消费的那一份上」。
 */
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";
import { compileTriggers, matchTrigger, shouldFire, resultText } from "./index.mjs";

const raw = JSON.parse(readFileSync(new URL("./triggers.json", import.meta.url), "utf8"));
const T = compileTriggers(raw);

// ---- compileTriggers：坏表启动期炸 ----
assert.throws(() => compileTriggers([{ id: "x" }]), /缺 card/);
assert.throws(() => compileTriggers([{ id: "x", card: "c", out_re: "(", hint: "h" }]),
	/Invalid regular expression|Unterminated/);
assert.throws(() => compileTriggers([
	{ id: "x", card: "c", out_re: "a", hint: "h" },
	{ id: "x", card: "c", out_re: "b", hint: "h" },
]), /重复/);

// ---- 她会话里逐字的失败样本（turn 616 / 691，修 sd 截断前抓的）----
const TRACEBACK = [
	"Traceback (most recent call last):",
	'  File "<string>", line 1, in <module>',
	'  File "/usr/lib/python3.12/json/__init__.py", line 293, in <module>',
	"json.decoder.JSONDecodeError: Invalid control character at: line 1 column 1501 (char 1500)",
].join("\n");

// raw-json-broken：sd raw + 解析炸 → 响
let hit = matchTrigger(T, '~/qq-workspace/bin/sd raw /state 2>/dev/null | python3 -c "..."', TRACEBACK);
assert.equal(hit?.id, "raw-json-broken");

// 同样的 traceback 但命令里没有 sd raw → 不响（不冤枉无关命令）
assert.equal(matchTrigger(T, "python3 -c 'import json'", TRACEBACK), null);

// Expecting value 形态（她的另一路炸法）也要响
hit = matchTrigger(T, "./bin/sd raw /state 2>/dev/null | python3 -c 'json.load(sys.stdin)'",
	"json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)");
assert.equal(hit?.id, "raw-json-broken");

// ---- store-noop：卡 evidence 里的逐字样本 ----
hit = matchTrigger(T, './bin/sd raw /store \'{"x":67,"y":16,"name":"Bullhead"}\' 2>&1|tail -1',
	'{"ok": true, "stored": [], "chestAt": {"x": 67, "y": 16}}');
assert.equal(hit?.id, "store-noop");

// stored 非空 = 成功 → 绝不响（172 次干净调用不打扰的铁律）
assert.equal(matchTrigger(T, './bin/sd raw /store \'{"x":67,"y":16}\'',
	'{"ok": true, "stored": [{"id": "Parsnips", "count": 2}]}'), null);

// ---- harvest-zero：卡 evidence 形态 ----
hit = matchTrigger(T, "do r=$(./bin/sd farm reap 2>&1|tail -1)",
	"收菜完成0件");
assert.equal(hit?.id, "harvest-zero");

// 收了 12 件 → 不响
assert.equal(matchTrigger(T, "./bin/sd farm reap", "收菜完成 12 件"), null);

// ---- 干净输出一律不响 ----
assert.equal(matchTrigger(T, "./bin/sd state",
	"Mountain(42,11) 冬6 10:30 | 体力270/270 HP100 | 钱1410325 | 手:Iridium Rod | 包26/36"), null);
assert.equal(matchTrigger(T, "./bin/sd raw /state", '{"ok": true, "worldReady": true, "player": {}}'), null);

// ---- 冷却闸：响一次→冷却内不响→冷却后再响 ----
const state = new Map();
assert.equal(shouldFire(state, "raw-json-broken", 1000, 60000), true);
assert.equal(shouldFire(state, "raw-json-broken", 2000, 60000), false);   // 冷却内
assert.equal(shouldFire(state, "raw-json-broken", 61000, 60000), true);   // 冷却过
assert.equal(shouldFire(state, "store-noop", 2000, 60000), true);          // 别的触发器互不影响

// ---- resultText：content 块 / 结构化 value / 空 ----
assert.ok(resultText({ content: [{ type: "text", text: "a" }, { type: "text", text: "b" }] })
	.includes("a"));
assert.ok(resultText({ value: { ok: true } }).includes('"ok"'));
assert.equal(resultText(null), "");
assert.equal(resultText({ content: [] }), "");

// ---- 表本身：每条 hint ≤ 200 字（注入是指针，不是抄卡）----
for (const t of raw) {
	assert.ok(t.hint.length <= 200, `${t.id} hint ${t.hint.length} 字超 200`);
	assert.ok(t.card.startsWith("stardew-"), `${t.id} 卡名要带域前缀`);
}

console.log("expmem-card-delivery: 全部断言绿 (%d 条触发器)", raw.length);
