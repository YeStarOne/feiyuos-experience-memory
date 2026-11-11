# feiyuos-experience-memory (expmem)

肥鱼 OS 游戏技能记忆：**行为过程 → 技能卡**。只补「过程」这一层——事实仍归
memory-hub / auto-memory / 手册§8 / shared-memory.md（知识层），两层用 evidence
互相点，不互相复述。

## 当前边界：Stardew-first（2026-11-12 评审明确）

现在真正跑通的经验链只有 **dsh 会话日志 → bash → sd 命令 → 星露谷动作** 这一条。
`adapters/tool_generic.py`、`adapters/minecraft.py`、`sources/live_tap.py` 尚未实现，
`api.record_action()` / `commit_episode()` 仍是 `NotImplementedError`——本仓
口径是「星露谷离线经验提炼 + 原生 Skill 卡消费」，不是「所有工具/游戏通用」。
通用化按 Action/Episode 协议走 **Adapter + Outcome Evaluator** 两个扩展点，
不为每个游戏另起一套技能记忆（见 docs/00-plan.md）。

## 她昨晚怎么用它（真实例子，2026-11-11 凌晨）

**1. 02:44 — 三张卡悄悄进了技能架。**
stardew-buy / stardew-store / stardew-probe-after-action 三张卡拷进她的技能目录，
技能架热加载，**不用重启她**。她下一个回合的技能清单里就出现了这三张卡。

**2. 03:01 — 她自己翻开了卡。**
任务是「把鱼存进仓库」。她用原生 skill 工具**主动翻开** stardew-store 卡，照卡教的
做：连续三次探桥（间隔 30/60 秒重试），桥不通就如实说「存不了」，不硬编、不瞎猜。
这就是设计里最想要的消费路径——不是被喂知识，是自己去翻书。

**3. 04:0x — 105 分钟的存箱攻坚，判据机判了「失败」。**
她跑了 20 多条命令：先拍库存照、扫箱子、五次尝试存箱，把所有失败方式都撞了一遍
（坐标写错→找不到箱子、物品名写中文→静默没存进去、返回 ok 但其实是空）。回合最后
被网络掐断没发出总结，但判据机从日志里**自动**判定这回合失败——不是她没干活，是活
没干成。第二天早上，她踩的两个坑（中文物品名假成功、返回 ok 不等于存进去了）已经
作为「失败轨」写进卡片 v2。**她用自己的失败教会了未来的自己**——这个回路就是本
模块存在的意义。

> 附一笔结案：「她包里的鱼一夜消失」不是 bug——是主人帮她清包了（2026-11-11 主人确认）。
> 跟物品名、桥、箱子都无关。卡里「name:"" 等于整包塞箱」的警示来自她 Stone x15 的
> 真实调用，与这件事无关，规则照留。

## 模块架构（人话版）

```mermaid
flowchart TD
    A["鱼鱼在游戏里干活"] --> B["会话日志<br/>每条命令和结果都自动记录"]
    B --> C["采集层<br/>从日志里认出游戏动作"]
    C --> D["错题本<br/>只记不删"]
    D --> E["判据机<br/>自动打标 成功 失败 被打断"]
    D --> F["归纳器<br/>把同类成败总结成规则"]
    F --> G["技能卡<br/>成功轨 加 失败轨 加 证据链"]
    G --> H["技能架<br/>热载 不用重启"]
    H -->|她翻卡或被推荐| I["她照卡行事 做完当场对账"]
    I -->|用得对不对| E
    E -->|加分 减分| G
```

| 层 | 一句话 |
|---|---|
| 会话日志 | 她干活的原始记录（dsh 会话事件流），这里只读不写 |
| 采集层 | 正则认出「她动了游戏」——锚点必须定期对活日志做命令形态普查（她改一次命令习惯就能让采集悄悄瞎掉一半，这事真发生过） |
| 错题本 | append-only 只记不删；被打断不算她的失败 |
| 判据机 | 机械判成败：有写动作 + 写完当场看到效果，缺一就判「不知道」，不许灌水 |
| 归纳器 | 从错题本总结成功轨/失败轨，自动长出技能卡 |
| 技能卡 | 原生 SKILL.md，每条规则都能点回她当天的真实调用（evidence 三元组） |
| 技能架 | dsh 技能目录热载，加卡不用重启 |
| 反馈闭环 | 用卡→有没有用→计数器加减→卡自动进化（已落码，等第一个真实回合闭环） |

## 目录

- `expmem/` 模块源码
- `skills/` 种子卡（retrieve 测试夹具——往这里加卡会改测试基线，生产卡别放这）
- `cards/` 生产卡镜像（她 live 技能目录的 documentary 副本，测试不消费；session 字段已掩码）
- `tests/` 172 tests
- `docs/00-plan.md` 方案与验收口径

## 定位（防走样，cmb-absorb-research 2026-11-10 判据）

- 错题本 = 过程数据（tool, args, result, verdict），今天唯一存放点是本仓的
  `actions.jsonl` / `episodes.jsonl`——之前只躺在 dsh session 通用事件流里。
- 知识结论的正本不变：手册 §8（裁决）、shared-memory.md（台账）、INDEX.tsv（日期索引）。
- 技能卡 = 原生 `SKILL.md`（dsh-skill-filesystem 热监听，加文件不用重启）。

## S0（observe-only，零重启零插件）

```bash
python3 -m expmem.cli replay ~/.dsh/sessions/--home-agent-qq-workspace-- --out /tmp/expmem-out
```

只读重放，产出 `actions.jsonl` / `episodes.jsonl` / `metrics.json`。
验收口径与判据见 `docs/00-plan.md` §4（S0 行）。

## 测试

```bash
python3 -m unittest discover -s tests -t .
```

## 关键口径（别混）

- verb 级动作（段首锚定 + heredoc 截断）与 `sd raw <path>` 调用
  （不锚段首，吃 `$(sd raw …)`）是**两把尺子**，有重叠，分开计数。
- 归属闸：automation 会话计数照记，但**不进产物**。
- verdict 四值：success / failure / unknown / interrupted——外部打断不是她的失败，
  不进 bench 分母（cmb 对齐 C 条）。
- 隐私：真实 QQ 号不入公共仓——OWNER_PREFIXES 仓库默认假号 1000000001/2/3，
  部署侧放 `expmem/_owner_local.py`（gitignored）覆盖；文档与夹具里的会话名同用假号。
