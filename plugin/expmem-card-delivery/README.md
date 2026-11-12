# expmem-card-delivery（原型，未部署）

沉淀卡「动手那一刻」递送插件。为什么需要它：docs/00-plan.md §7-14 实测——
她从不查 skill 目录（8537 次工具调用里 `skill` 只被调 3 次，2 次还是 RN 信
点名了端点），覆盖沉淀卡的 sd 端点调用却有 172 次。**没有查询，就没有召回**；
知识必须在已知失败形态出现的那一刻递到她眼前。

## 文件

| 文件 | 作用 |
|---|---|
| `index.mjs` | 插件本体（零依赖，只 import node: 内建——file:// 导入解析不到 node_modules） |
| `triggers.json` | 触发器表：失败形态 → 卡名 + 一句指针。**表顺序=优先级** |
| `test.mjs` | 纯逻辑测试，夹具全部来自她真会话/真卡证据（node test.mjs） |

## 机制（照抄官方范例 dsh-repeat-tool-reminder:2251）

`ctx.on("tools/post-execute")` → 只看 `bash` 的 `arguments.command` 和结果文本 →
命中触发器且过冷却闸 → `createUserMessage`（user 角色 + source.kind=插件名）→
返回 `{ kind:"accept", additionalContexts:[msg, ...downstream] }`。

- 干净调用绝不打扰（真数据 172 次里 ≤7 次会响）
- 每触发器冷却期默认 30 分钟（她连着重试 ≠ 灌水）
- 任何自身异常吞掉并 warn——postExecute 合约：throwing listener → isError，
  递卡插件绝不能弄失败她的工具调用

## 部署（⏳ 待主人拍板 + 她空闲，需重启 dsh-web）

1. 拷贝到服务器：`~/.dsh/plugins/expmem-card-delivery/{index.mjs,triggers.json}`
2. 创建 `~/.dsh/cordis.patch.yml`（dsh-app-boot:1131 会加载这个用户层，
   现在该文件不存在；顶层必须是 YAML 数组）：

```yaml
- insert:
    - id: expmem-card-delivery
      name: ./plugins/expmem-card-delivery/index.mjs
      config:
        cooldownMs: 1800000
```

相对路径锚定在 patch 文件旁（`anchorInsertedPluginNames`，
dsh-app-boot/lib/index.js:3979），所以 `./plugins/...` 解析为
`~/.dsh/plugins/...`——不用碰 npx 缓存。

3. 重启后验证：`grep expmem-card-delivery <dsh-web 日志>` 看插件装载 +
   造一次 `sd raw /store` 返回 `stored:[]` 看注入是否到达。

## 维护约定

- 加触发器 = 只改 `triggers.json`（不用动代码，不用重启？——重启才重读表，
  所以加触发器也要挑空闲重启；表错了启动期就炸，不会带病上线）
- hint 只写「指针 + 一句为什么」，卡才是真值；卡改了 hint 没跟，以卡为准
- 夹具必须来自她的真会话/真卡证据，不造样本
