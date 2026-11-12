/**
 * expmem-card-delivery —— 沉淀卡「动手那一刻」递送插件（原型，未部署）
 *
 * 解决的问题（2026-11-13 实测，docs/00-plan.md §7-14）：她从不查 skill 目录
 * ——8537 次工具调用里 `skill` 只被调 3 次（其中 2 次还是 RN 信点名了端点），
 * 而覆盖沉淀卡的 sd 端点调用有 172 次。知识躺在目录里不等于被用：必须在
 * 已知失败形态出现的那一刻，把卡递到她眼前。
 *
 * 机制（照抄官方范例 dsh-repeat-tool-reminder/lib/index.js:2251）：
 *   ctx.on("tools/post-execute") → 匹配触发器 → createUserMessage →
 *   返回 { kind:"accept", additionalContexts:[msg, ...downstream] }
 *
 * 铁律：
 *   · 只在「已知失败形态」出现时注入（stored:[] / 收菜 0 件 / JSON 解析炸）；
 *     干净调用一律不打扰——按真数据 172 次里 ≤7 次会响
 *   · 每个触发器有冷却期，她连着重试 ≠ 灌水
 *   · 注入的是「指针」（卡名 + 一句为什么）；卡才是真值，hint 与卡漂移时以卡为准
 *   · 任何自身异常都吞掉并 warn——递卡插件绝不能把她的工具调用弄失败
 *     （postExecute 合约：throwing listener → isError）
 *
 * 零依赖：只许 node: 内建。插件经 file:// 导入（~/.dsh/cordis.patch.yml 的
 * insert.name 写 ./plugins/expmem-card-delivery/index.mjs），裸包名解析不到
 * node_modules，所以连 schemastery 都不能用——config 手工校验。
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { randomUUID } from "node:crypto";

export const name = "expmem-card-delivery";

const HERE = dirname(fileURLToPath(import.meta.url));

/**
 * 编译并校验触发器表：坏正则 / 缺字段 / 重复 id 在启动期炸，不进运行期。
 * 表结构（triggers.json）：
 *   id      唯一标识（冷却记录的键）
 *   card    指向的沉淀卡名（~/.dsh/skills/<card>/SKILL.md）
 *   cmd_re  可选；命令侧白名单（不给=只看输出侧）
 *   out_re  输出侧失败形态（JS 正则）
 *   hint    注入的一句话（指向卡，不复制卡）
 */
export function compileTriggers(raw) {
	if (!Array.isArray(raw) || raw.length === 0) {
		throw new Error("triggers 必须是非空数组");
	}
	const seen = new Set();
	return raw.map((t, i) => {
		if (typeof t !== "object" || t === null) {
			throw new Error("trigger#%d 必须是对象".replace("%d", i));
		}
		for (const k of ["id", "card", "out_re", "hint"]) {
			if (typeof t[k] !== "string" || !t[k]) throw new Error(`trigger#${i} 缺 ${k}`);
		}
		if (seen.has(t.id)) throw new Error(`trigger id 重复: ${t.id}`);
		seen.add(t.id);
		new RegExp(t.out_re);                       // 先试编译，坏正则启动期炸
		if (t.cmd_re) new RegExp(t.cmd_re);
		return {
			id: t.id,
			card: t.card,
			cmdRe: t.cmd_re ? new RegExp(t.cmd_re) : null,
			outRe: new RegExp(t.out_re),
			hint: t.hint,
		};
	});
}

/** 从工具结果抽出可匹配文本（bash=content[].text；结构化工具=value 序列化）。 */
export function resultText(result) {
	if (!result) return "";
	const parts = [];
	if (Array.isArray(result.content)) {
		for (const b of result.content) {
			if (b && typeof b.text === "string") parts.push(b.text);
		}
	}
	if (typeof result.value === "string") {
		parts.push(result.value);
	} else if (result.value !== undefined) {
		try { parts.push(JSON.stringify(result.value)); } catch { /* 循环引用等，忽略 */ }
	}
	return parts.join("\n");
}

/** 第一条命中的触发器；cmdRe 存在时要求命令侧也匹配（表顺序=优先级）。 */
export function matchTrigger(triggers, command, text) {
	if (!text) return null;
	for (const t of triggers) {
		if (t.cmdRe && !(typeof command === "string" && t.cmdRe.test(command))) continue;
		if (t.outRe.test(text)) return t;
	}
	return null;
}

/** 冷却闸：同一触发器冷却期内只响一次。返回 true=该响（并记录本次）。 */
export function shouldFire(state, id, now, cooldownMs) {
	const last = state.get(id);
	if (last !== undefined && now - last < cooldownMs) return false;
	state.set(id, now);
	return true;
}

/**
 * 安装监听。config（patch yml 里给）：{ cooldownMs?: number 毫秒，默认 30 分钟 }
 * 导出 Config 省略——loader 全程可选链，零依赖插件不接 schemastery。
 */
export function apply(ctx, config) {
	const cooldownMs = config?.cooldownMs ?? 30 * 60 * 1000;
	if (typeof cooldownMs !== "number" || cooldownMs < 0 || !Number.isFinite(cooldownMs)) {
		throw new Error(`expmem-card-delivery: 非法 cooldownMs ${config?.cooldownMs}`);
	}
	const triggers = compileTriggers(
		JSON.parse(readFileSync(join(HERE, "triggers.json"), "utf8")));
	const lastFire = new Map();

	ctx.on("tools/post-execute", async (exec, result, next) => {
		const downstream = await next();
		try {
			if (exec?.name !== "bash") return downstream;   // 只有 bash 承载 sd 语义
			const cmd = exec?.arguments?.command;
			const text = resultText(result);
			const hit = matchTrigger(triggers, cmd, text);
			if (!hit) return downstream;
			if (!shouldFire(lastFire, hit.id, Date.now(), cooldownMs)) return downstream;
			// 与 dsh-repeat-tool-reminder 的 createUserMessage 同构：
			// user 角色 + 稳定 id + source.kind=插件名
			const msg = structuredClone({
				role: "user",
				id: randomUUID(),
				content: [{ type: "text", text: `【沉淀卡 ${hit.card}】${hit.hint}` }],
				source: { kind: name, form: "notice", summary: hit.card },
			});
			Object.freeze(msg);
			return {
				...downstream,
				additionalContexts: [msg, ...(downstream.additionalContexts ?? [])],
			};
		} catch (err) {
			console.warn(`[${name}] 注入失败（已忽略，不影响工具结果）:`,
				err?.message ?? err);
			return downstream;
		}
	});
}
