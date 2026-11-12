# expmem-card-delivery（已部署 · 2026-11-12）

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

## 部署（✅ 已完成：2026-11-12 09:15 CST，主人拍板窗口）

1. 插件文件在服务器：`~/.dsh/plugins/expmem-card-delivery/{index.mjs,triggers.json}`
2. 用户层 patch：`~/.dsh/cordis.patch.yml`（dsh-app-boot:1131 加载；顶层 YAML 数组）：

```yaml
- insert:
    - id: expmem-card-delivery
      name: ./plugins/expmem-card-delivery/index.mjs
      config:
        cooldownMs: 1800000
```

相对路径锚定在 patch 文件旁（`anchorInsertedPluginNames`，
dsh-app-boot/lib/index.js:3971），所以 `./plugins/...` 解析为
`~/.dsh/plugins/...`——不用碰 npx 缓存。

3. 验证：`apply()` 会写 `.loaded-marker`（一次性加载金丝雀）。真实记录：
   - 2026-11-12 09:15:41 CST 随 dsh-web 启动载入（triggers=3）
   - **热加载实测（同一 PID 938055，全程未重启）**：
     - 改 patch 的 `cooldownMs` → 20 秒内插件带新配置重新挂载（09:16:02、09:17:03 两次）
     - 删掉 patch 再从零重建 → 20 秒内重新挂载（09:19:22）
   - ⇒ dsh-hmr 监听 `~/.dsh/cordis.patch.yml`（dsh-hmr/lib/index.js:850 同时监听
     profile patch 和 home patch），**以后改 patch / 加插件不需要重启**
4. ⚠️ 金丝雀自己瞎过一次：第一版 `apply()` 用了 `writeFileSync` 却没 import，
   ReferenceError 被 catch 吞掉，marker 永远不出现——连续两次重启都被误读成
   「插件没加载」。教训：**把「文件不存在」当判据之前，必须先让它出现过一次。**

## 维护约定

- 加/改触发器 = 只改 `triggers.json`，然后让 `~/.dsh/cordis.patch.yml` 产生一次
  变更（例如追加一行注释）——hmr 会重新挂载插件并重读表（已实测）；
  表坏了会在重挂时炸，不会带病上线
- hint 只写「指针 + 一句为什么」，卡才是真值；卡改了 hint 没跟，以卡为准
- 夹具必须来自她的真会话/真卡证据，不造样本
- 回滚：删除 `~/.dsh/cordis.patch.yml`（hmr 会让插件下架，已实测创建/删除都会重挂）
