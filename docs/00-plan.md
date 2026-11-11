# feiyuos-experience-memory · 落地方案 v1（2026-11-10）

> 目标：把「她在游戏里怎么把事做成/怎么搞砸」变成可复用的技能，而不是只存"她说过什么事实"。
> 本文件 = 逐文件改造任务 + 现有四条线的接口。写成过程中每条数字都是从线上实物里抓的（会话日志 / 插件源码 / profile）。

---

## 0. 一句话结论

**不新建第四套记忆存储。**
行为过程 → 新的 `EpisodeStore`（append-only，今天没人存这一层）；
事实/说法 → 仍归 `memory-hub` / `auto-memory` / `memory-recall`；
技能 → 落成 **原生 `SKILL.md`**（她会话里已经有 `skill` 工具，且文件根是热监听的，**加文件不用重启**）。

采集第一步 = **离线重放会话日志**，零插件改动、零重启、不碰她在跑的回合。

---

## 1. 现状盘点（全部实测）

### 1.1 事件线：唯一完整的行为原始档案

`~/.dsh/sessions/--home-agent-qq-workspace--/*/session.v4.jsonl.zstd`
实测 **121 个会话文件 / 51MB**，事件计数：

| 事件 | 次数 | 用途 |
|---|---|---|
| `step/start` / `step/end` | 9329 / 9328 | 步骤边界 |
| `assistant/message` | 9226 | 她的正文（memory-hub 只吃这一层） |
| `tool/call` | **8072** | 动作（名 + 参数 JSON 字符串） |
| `tool/result` | **8074** | 结果（含 `isError`）+ `meta.cwd` |
| `turn/start` / `turn/end` | 1418 / 1417 | 剧集边界 |
| `llm/retry` | 498 | 上游抖动（判"慢"要排除它） |
| `compaction/*` | 292 | 压缩窗口（剧集切分要避开） |

实测 schema（原样，不是推测）：

```json
{"type":"tool/call","seq":33,"time":1793020475158,
 "data":{"turn":2,"step":1,"callId":"call_00_Veb…","name":"bash",
         "arguments":"{\"description\":\"…\",\"command\":\"ls /home/agent/qq-workspace\"}"}}

{"type":"tool/result","seq":36,"time":1793020475262,
 "data":{"turn":2,"step":1,
         "message":{"toolCallId":"call_00_Veb…","isError":false,
                    "content":[{"type":"text","text":"AGENTS.md\nmemes\n…"}]},
         "meta":{"cwd":"/home/agent/qq-workspace"}}}
```

- **配对率 8074/8072 = 100%**，未配对 0（`message.toolCallId` 对 `data.callId`）。所以延迟、成功率都能算准。
- 归属分布：`onebot-private-1000000001` **6321/8072 = 78%**（主会话）；群 274；另一个私聊 180；其余是 automation 会话。
  ⚠️ **归属闸**：automation / 运维会话的调用不是"她的游戏经验"，adapter 必须按 session 前缀白名单过滤，否则技能会从 cron 里长出来。

### 1.2 工具层：她的游戏动作**藏在 bash 字符串里**

- 4826 次 `bash` 里 **558 次含 `sd` 调用**（11.6%）；直连桥的 curl 只有 1 次。
  → 这意味着：**GameTraceAdapter 的入口不是某个 `stardew_*` 工具，而是 bash 命令解析**。
  （初版我用宽松正则数出 957，那是把日记/文档里出现的 "sd xxx" 和 heredoc 正文也算进去了；改成"按 `;`/`&&`/`||`/换行分段 + 遇 heredoc 截断 + verb 白名单"后是 558 —— **口径以 558 为准**。）
- 实测动作量：**verb 级 1028 次**（含 `raw` 264）＋ **`sd raw <path>` 的路径调用 824 次**（76 个不同路径）→ 去重后 **≥1588 个游戏动作**，全部集中在**一个会话**：`onebot-private-1000000001-mvp9wgp5`。
- `sd raw` 的真实动作词表（这才是她的"动词"）：`/menu_close` 110、`/fishbot` 97、`/animals` 79、`/menu` 74、`/interact` 61、`/tool_area` 35、`/use` 25、`/click` 23、`/drop_item` 23、`/store` 17、`/eat` 17、`/buy` 17、`/rod` 16…
  ⚠️ 所以 adapter 不能只认 verb，**必须也认 `sd raw /path` 和命令替换 `$(sd raw …)` 里的调用**（子 shell 里很常见）。
- `sd` 是固定调用器（服务器 `~/qq-workspace/bin/sd`，526 行），verb 集：`state / inv / near / shot / warp / move / act / use / water / plant / harvest / dig / sleep / buy / sell / day / key / wait / raw / farm`
- 读/写已经分好：`state|inv|near` 都打到 `GET /state`；写动作是 POST；`sd wait /state "<cond>" <timeout>` 是长轮询（桥侧 `/wait`，1s 采样）。
- `sd` 已经把返回压成**一行摘要**（历史中位 360 字 → 1 行）：这正是"低 token 感知层"，adapter 直接吃它。
- 游戏回合：**1418 个 turn 里只有 59 个含游戏动作**；每回合动作数 1–12（1 个的 9 次、2 个 6 次、6 个 5 次）→ 剧集规模很小，"calls-to-goal" 可算。
- MC 车道不在她这条会话里（PC 侧桥 17700 / MCP）→ **第二个 adapter，本轮不做**，接口留位。

### 1.3 现有记忆：三套并存，全都不是"行为"

| 组件 | 收什么 | 缺口 |
|---|---|---|
| `dsh-onebot-qq/lib/memory-hub.js` | `noteAssistant()` 只过滤 `b.type === 'text'`（第 384 行） | **tool_use / tool_result 全丢** ← 根因在这，是采集面，不是检索 |
| `auto-memory`（autoSummarize=true） | `agent/disposed` 时 LLM 提炼 candidate 记忆（带原文引用） | 只有说法，没有动作+结果+判定 |
| `memory-recall`（USER.md / MEMORY.md） | 常驻画像 | 同上 |

→ 三个系统都答不了「上次这步为什么失败」，因为 `(tool, args, result, verdict)` 这层从来没落过盘。

### 1.4 技能机制：原生已有，不要新造

- 她会话的 `request/header` 里**实测有 `skill` 工具**。
- profile 已挂 `dsh-skill-filesystem`（项目/自定义/用户三个根，**热监听**：新增/改名/删除下一轮目录就生效，**不用重启**）+ `dsh-tool-skill`（模型侧目录+按需加载）。
- 格式：目录内 `SKILL.md` 或扁平 `<name>.md`，frontmatter 必须有 `name` + `description`。

→ `SkillRegistry` **就是**一个 SKILL.md 目录。我们要写的只有三件：**生成器**、**frontmatter 里的证据/置信度**、**检索入口（目录行注入）**。

### 1.5 度量基线：现在就能算（pre-registered，先记下来再改）

| tool | calls | err | err% | p50 | p90 |
|---|---|---|---|---|---|
| bash | 4826 | 10 | 0.2% | 2.2s | 24.4s |
| pet_say | 1407 | 0 | 0% | 0.94s | 3.9s |
| read_image | 383 | 32 | 8.4% | 0.09s | 0.70s |
| job_output | 351 | 2 | 0.6% | 35.7s | 300s |
| qq_send_image | 110 | 23 | **20.9%** | 0.72s | 7.3s |
| qq_draw | 57 | 14 | **24.6%** | 33.3s | 67.3s |
| edit | 43 | 9 | **20.9%** | 0.02s | 0.03s |
| web_fetch | 29 | 10 | **34.5%** | 1.8s | 6.8s |
| gen_image | 27 | 0 | 0% | **111.6s** | 249.6s |

游戏侧基线（**S0 已跑出，1028 次 verb 级动作**）：

| verb | calls | err | err% | p50 | p90 |
|---|---|---|---|---|---|
| state | 328 | 1 | 0.3% | 9.3s | 51.7s |
| raw | 264 | 0 | 0% | 6.3s | 22.4s |
| warp | 160 | 1 | 0.6% | 7.5s | 23.9s |
| inv | 114 | 0 | 0% | 8.6s | 34.5s |
| use | 73 | 0 | 0% | 7.1s | 16.5s |
| near | 37 | 0 | 0% | 3.9s | 11.0s |
| move | 21 | 0 | 0% | 18.5s | 25.4s |
| shot | 19 | 0 | 0% | 18.4s | 47.3s |
| key | 10 | 0 | 0% | 9.6s | 50.4s |
| plant / farm | 2 | 0 | 0% | — | — |

**这张表最刺眼的一列是 err%：1028 次游戏动作，工具层只报 2 次失败（0.2%）。**
而我们知道她真实踩过：`/buy` 不带 price 按售价×2 扣钱、`/store` 返 `stored:[]` 假装 ok、`farm_report.ready=34` 而收菜 0 件。
→ **工具层 isError 对游戏成败几乎没有鉴别力**，这是阻塞①的量化证据，不是担心。
单动作 p50 7–18s（走 Tailscale 中继），所以"少绕路"直接等于"少等十几秒"。

---

## 2. 四个接口（最小、可跑、不做别的）

```python
# expmem/api.py
def record_action(act: Action) -> str
    # append actions.jsonl；幂等键 = (session_id, call_id, sub_idx)
    # 一行一个动作；adapter 负责把 bash/sd 拆成 sub_idx

def commit_episode(ep: Episode) -> str
    # append episodes.jsonl；幂等键 = episode_id = sha1(session|turn_from|turn_to)
    # 只追加，不改历史（改判走 evidence 新行，见 §3 evidence.py）

def retrieve_skills(q: SkillQuery) -> list[SkillRef]
    # 返回"目录行"（name + when + confidence + state），不返回正文
    # S0/S1 只读本地 SKILL.md frontmatter，不进任何 prompt（observe-only）

def record_skill_feedback(fb: SkillFeedback) -> None
    # append feedback.jsonl + 重算该 skill 的 frontmatter 计数（support/contra/last_used）
    # 正文永不自动改；state 升级必须过 §3 skills.py 的状态机
```

数据结构（字段就这些，别加）：

```json
// Action
{"id":"act_<sha1>","ts":1793020475158,"session":"onebot-private-1000000001-…",
 "turn":2,"step":1,"source":"session-log","cwd":"/home/agent/qq-workspace",
 "tool":"bash","adapter":"stardew_sd","verb":"farm_report","sub_idx":0,
 "endpoint":"GET /farm_report","args":{"raw":"sd farm_report"},
 "result":{"isError":false,"text_sha":"…","head":"…一行摘要…"},
 "latency_ms":210}

// Episode
{"episode_id":"ep_<sha1>","session":"…","turn_from":None,"turn_to":None,
 "t_from":1793020000000,"t_to":1793022000000,"goal_hint":"收菜/浇水/买种子",
 "actions":["act_a","act_b"],"verdict":"success|failure|unknown",
 "outcome":{"probe":"farm_report","ready":0,"harvested":0},
 "skill_refs":["stardew-target-tile-use@v3"]}

// SkillFeedback
{"skill":"stardew-target-tile-use","version":3,"episode_id":"ep_…",
 "signal":"helpful|harmful|neutral","evidence":{"before":{…},"after":{…}},"by":"consolidator|owner|runtime"}
```

**为什么是这四个**：`record_action` 是采集的唯一写口；`commit_episode` 是"一次任务"的封口（没有剧集就没有成功/失败，只有零散调用）；`retrieve_skills` 把"取"和"注入"分开（observe 期只取不注入）；`record_skill_feedback` 让技能能被现实纠正，而不是越写越神。

---

## 3. 逐文件拆分

新仓 `feiyuos-experience-memory`（Python 侧为主，**S0–S2 不含任何 dsh 插件**）：

```
feiyuos-experience-memory/
├─ README.md                      # 定位：只做"行为过程→技能"，事实记忆不搬
├─ LICENSE                        # MIT（与伞仓一致）
├─ expmem/
│  ├─ __init__.py
│  ├─ api.py            ★ 四个接口的唯一实现（其余模块只被它调用）
│  ├─ models.py         ★ Action / Episode / SkillFeedback / SkillRef 的 dataclass + JSON schema 校验
│  ├─ store.py          ★ AppendOnlyStore：jsonl 追加、幂等、崩溃安全（os.replace 只用于索引）
│  ├─ sources/
│  │  ├─ session_log.py ★ 读 session.v4.jsonl.zstd → 事件流（zstd -dc 逐行 json）
│  │  ├─ live_tap.py    ⏳ S3：dsh 插件侧（agent/pre-step + tool/call/result），本轮不写
│  │  └─ bridge_log.py  ⏳ 桥侧日志/探针对账（可选，用于 ground truth）
│  ├─ adapters/
│  │  ├─ base.py        ★ Adapter 协议：match(action_ctx) / parse() → Action[] / outcome_probe()
│  │  ├─ stardew_sd.py  ★ bash 命令解析：`sd <verb>` 链（; && | 分段）→ 每个 verb 一个 Action
│  │  ├─ tool_generic.py★ 非游戏工具的兜底（tool=name, args=原样），保证剧集不残缺
│  │  └─ minecraft.py   ⏳ MC 车道（17700/RCON），接口留位不实现
│  ├─ episode.py        ★ turn/start..turn/end 切剧集 + 压缩窗口避让 + 归属白名单
│  ├─ outcome.py        ★ 成功判定（见 §5 阻塞①）：isError + 桥侧结果探针 + 前后差值
│  ├─ evidence.py       ★ 证据链：每行可追到 (session, turn, step, callId)；只追加不删
│  ├─ consolidate.py    ★ 剧集/簇 → candidate 技能（LLM 离线跑，不进她的回合）
│  ├─ skills.py         ★ Skill 状态机 candidate→observed→verified→deprecated；写 SKILL.md
│  ├─ retrieve.py       ★ 检索：词法+元数据打分，输出目录行（≤3 条 × ≤80 字）
│  ├─ feedback.py       ★ 反馈落盘 + frontmatter 计数重算
│  ├─ bench.py          ★ Game Skill Benchmark：从 episodes 算 6 个指标
│  └─ cli.py            ★ `expmem replay|consolidate|skills|lint|bench`
├─ skills/                       # 生成物（SKILL.md），部署时复制到技能根
├─ tests/
│  ├─ fixtures/                  # 真实会话片段（脱敏）：1 主会话 + 1 群 + 1 automation 反例
│  ├─ test_session_log.py        # 8072 里取 3 个真文件，断言事件计数与配对率
│  ├─ test_stardew_adapter.py    # `sd warp …; sleep 40; sd state` 必须拆成 2 个 Action
│  ├─ test_ownership.py          # automation 会话必须被过滤（反例夹具，期望 0 条）
│  ├─ test_outcome.py            # isError=false 但 farm_report.ready=0 → verdict=failure
│  ├─ test_skills.py             # 状态机：candidate 不会自动变 verified；contradiction 留痕
│  └─ test_bench.py              # 空集不许算通过（分母守卫）
└─ docs/
   ├─ 00-plan.md                 # 本文件
   ├─ 01-interfaces.md           # 四接口 + schema 冻结版
   └─ 02-adapter-stardew.md      # sd verb ↔ 端点 对照（照 `sd` 源码抄，不照记忆）
```

**明确不动的东西（防扩散）**：

- `dsh-onebot-qq/lib/memory-hub.js` —— 不改它（它是 text 通道，职责对的）；将来若要让它也记动作，只在 `noteAssistant` 旁加一个**旁路**，不混进 enqueue。
- `~/.dsh/profiles/web/cordis.patch.yml` —— S0–S2 不需要改；只有 S3 加 tap 插件时才动，且必须挑她空闲窗口。
- 她正在跑的游戏端 / 桥 / mod —— 一个都不碰。

---

## 4. 落地顺序（观察先于自动）

| 步 | 做什么 | 需要重启？ | 验收判据（硬） |
|---|---|---|---|
| **S0** ✅ 11-10 完成 | `session_log` + `stardew_sd` + `episode` + `store`：121→122 个会话重放成 `actions.jsonl` / `episodes.jsonl`，出 `sd` 按 verb 的基线表（仓：本地 `G:\feiyuos-experience-memory`，测试 16/16；服务器副本 `/tmp/expmem-pkg`） | ❌ | 11-10 23:3x 实跑钉死：`tool/call` = **8138**（会话在涨，早间 spike 时 8072）；bash = 4859；含 sd 的 bash = **558**（verb 口径）/ **909**（含 raw-only 口径，两把尺子分列）；verb 级动作 = **1028**（产物 1027 = 1028 − 1 条 automation 被归属闸挡下）；raw 路径调用 = **824**；owner 回合 = **1315** / 游戏回合 = **59**；automation 进产物 = **0**（守门落在写出的文件上，不落在内存清单上）；同输入两跑 sha 一致（actions `8aed0fcb052ccbe6` / episodes `42f1af8f005b8201`）。产物：服务器 `~/expmem-s0/run-1110/{actions,episodes,metrics,bench_baseline}`；verb 基线：state 327 / raw 264 / warp 160 / inv 114 / use 73 / near 37 / move 21 / shot 19 / key 10，总 err **2/1027**。坑：AppendOnly 幂等键默认 `id`，Episode 的键是 `episode_id` → 全部 dedup 成 1 行，第一跑 episodes_written=1 假绿，改键后 1315 |
| **S1** ✅ 11-10 规则版完成（codex 拍板由主持实现，他汇总把关） | `outcome.py`（规则版：interrupted > blocked > 探针可用性 > noop，证据全部来自日志内 `out_head`，不碰桥；wrong_arg 双倍扣钱 11-11 退役——钱包共享/主持也赚花/卖货隔日结算，余额差不作证据）+ `bench.py`（分母守卫：probe ok 且 verdict∈{success,failure} 且非 blocked） | ❌ | 11-10 23:5x 实跑：1315 owner 剧集 → **failure 3（全是 `/store` stored:[] 假 ok，turn 753/754/770，逐条人工核为真阳性）** / interrupted 21（外部打断≠她的失败，不进分母）/ unknown 1291；probe_status ok 仅 **52/1315（4%）**——"动作后当场对账"本身就是最该炼的第一条技能（现在对账大多在别的回合，归不进剧集）；游戏回合动作数 p50=8 / p90=51，时长 p50=472s / p90=2843s；工具层 err 2/1027 对照坐实 isError 无鉴别力。新 sha：actions `8a1b39b22a513a46` / episodes `6088b62d6acc75b0`（S1 给行加了 out_head/probe_status/failure_class/act_count/game，须全新目录重放）。产物 `~/expmem-s1/run-1110/`（含 bench.json）。**已知限制**：success_rate=null（要 S2 的 goal_hint）；stale/partial 留给 consolidator；wrong_arg 已退役（11-11 主人纠正：钱共享+隔日结算，余额差不可判扣款）。测试 31/31（本地+服务器） |
| **S2** | `consolidate.py` → `skills/*.md`（state=candidate，**不部署**），人工过一遍 | ❌ | 每条技能必须有 ≥3 条 evidence 指向具体 episode；不许出现无证据的"经验" |
| **S3** | 部署 SKILL.md 到技能根（热监听，仍不重启）→ 跑 bench 对比 | ❌ | 只需新增文件；她下一条相关任务能看到目录行（判据：`skill` 工具被调用 或 目录命中日志） |
| **S4** | `live_tap` 插件（实时反馈）→ 预取注入 | ✅ 挑她空闲 | 只看条件：S3 后 `calls-to-goal` 没改善才做 |

S0–S3 **全部零重启、零插件**：这就是"observe 先于 auto"的具体含义，不是口号。

---

## 5. 阻塞（每条都带后果，不是清单式担忧）

1. **`isError:false` ≠ 成功（已量化）。** 1028 次游戏动作里工具层只报 2 次失败（0.2%），但真实失败至少三例：`/store` 默认 `keepTools=true` 返 `stored:[]` 还 ok；`farm_report.ready=34` 而收菜 0 件；`/buy` 不带 price 按售价×2 扣钱（游戏内钱变少，工具层成功）。
   → 不做 ground truth，技能置信度就是编的。修法：`outcome.py` 三类判据 = ①`isError` ②动作后桥侧字段（`/state`、`farm_report`、`inv` 差值）③同一回合窗口内取，跨回合取会归错账（pending 的日期/state 都滞后，已有教训）。
2. **她的上下文 165k，每步整读 ≈27s。** 任何"每步注入技能正文"都是往慢里推。
   → 硬约束：注入只允许**目录行**（≤3 条 × ≤80 字），正文按需由 `skill` 工具加载。这也决定了 `retrieve_skills` 的返回类型是 `SkillRef` 而不是 Skill。
3. **她在玩的时候谁都不许碰游戏端**，而加插件必须重启 dsh-web。
   → 所以能力顺序必须是"文件级（热加载）→ 插件级（挑窗口）"，S0–S3 都不越线。
4. **归属闸**：78% 调用在主会话，其余分散在群/私聊/automation。不按 session 前缀白名单过滤，consolidator 会把 cron 的运维动作炼成"游戏技能"。
5. **`sd` verb ↔ 端点必须照源码**：实测 `state/inv/near` 全是 `GET /state`，不是三条端点；`sd wait` 是桥侧长轮询。照记忆写 adapter 必错（层：`/state` 被 Git Bash 改写那段注释就在源码里）。

---

## 6. 分工建议

- **我（主持）先干 S0**：`session_log` + `stardew_sd` + `episode` + 基线表，今天出数字。理由：这一步只读文件、不碰她、不需要任何人配合，且它是后面所有东西的地基。
- **codex 接 `outcome.py` + `bench.py`**（S1）：判成功/失败是最难的语义活，且和"体验"定义强相关。
- **`consolidate.py` / `skills.py`（S2）**：等 S0/S1 的产物落地再写，否则 schema 会漂。
- 谁写 SKILL.md 都要过：**正文给人看，frontmatter 给机器算，证据必须能点回 (session,turn,step,callId)**。

---

## 7. 与 cmb 拆解报告的六条对齐（2026-11-10，cmb-absorb-research 供给，已逐条裁定）

1. **知识层不新建存放点（采纳）**：技能卡走原生 skill 机制；memory-graph / auto-memory / 手册§8 / shared-memory.md 一概不动。
   修边界：cmb 列的「正本」都是**知识层**（结论）；**过程层今天没有存放点**（8072 次调用只在 session.v4 通用事件流里）。EpisodeStore 补的是空层，不是第 5 个知识库。
   分工判据：错题本=过程数据，§8/shared-memory=知识结论，两层用 evidence **互相点**，不互相复述。
2. **frontmatter v2 定稿（采纳）**：
   ```yaml
   name: stardew-buy-with-price
   tool_key: stardew.buy                  # 对齐 INDEX.tsv 列名
   state: candidate|observed|verified|superseded_by:<id>
   valid_from: {date: "2026-11-10", game_day: "冬3"}   # 双标，按手册§9
   evidence: [{episode: "ep_xx", cmd: "sd buy 472 8", out: "-240g", verdict: success}]
   confidence: 0.71                       # 从 evidence 算，不拍脑袋
   contradicts: [stardew-buy-blind]       # 矛盾留痕不删
   source: consolidator@v1
   ```
3. **检索两条腿（采纳）**：目录行注入 + 显式检索工具 `expmem_search`（词法为主）同一批落地；不押宝自动注入（图谱召回接口类 0 命中）。
4. **写入两段式（采纳）**：自动捕获 candidate（触发信号=「报错→重试成功」「mailbox 裁决落定」）→ 每日 §9 收口升正本；事实级 dedup 用 Dice≥阈值。
5. **时间语义（采纳）**：episode 增列 `game_day`（从 /state 抓）；**episode 跨游戏日必须切开**（换季 0 株/跨日记错账的坑都在这）。
6. **不回退（同意）**：§9 六条约定+INDEX.tsv+人设指针=模块的约定层与触发器。协调点：**INDEX.tsv 扩列等本 schema v2 一起动，别两头改**；cmb 拆解报告等仓建好再贴 issue。

---

## 8. S0 独立对账（2026-11-10，cmb-absorb-research 主动复核）→ S0.1 修定版

cmb 用自己的分段器复核 S0：**S0 通过**，但抓到一个真缺陷+三个口径问题，全盘接受修正：

- **D1（真缺陷）**：白名单静默丢 **234 个非白名单 verb**（face 106/select 63/interact 35/heal 20/fishbot 6…）——基线 1028 偏小 18.6%，战斗交互类技能会学不到。根因=sd.py 有通用 `cmd_action` 分发器，词表开放。**修**：白名单废除，未知 verb → `other:<token>`、endpoint=`ACT <token>`；顺带修 `[a-z_]+`（select_tool 之前被截成 select）。
- **D2（命名）**：`turns_owner` 实为 5 会话含 3 群（29% 非主私聊）→ 改名 `turns_nonautomation`。
- **D3（口径）**：三个键分列——`bash_with_sd_verb`=570（段首锚定）、`bash_with_sd`=921（含 raw-only）、文本样 1348 不产（heredoc 正文误报，无消费方）。
- **D4（口径）**：raw 824（不锚段首，吃 `$(sd raw …)`）与 raw-verb 264 两把尺子写死在 README。
- **G1**：game_day 日志内尽力回填，实跑 **0/1337**（正则没对上真实 /state 字段，如实报不做假数据；桥侧回填留 S2+）。
- **G2**：相邻动作间隔 >600s 切分（gap_splits）+ 同 verb 同段 ≤30s 标 `poll_repeat`（保留在 actions，bench 的 calls-to-goal 剔除）→ 单回合 160 动作/6.5h 的轮询回合不再污染 p90（51→40）。
- **G3**：Action 补 `out_sha`（结果全文 sha1[:16]），schema 承诺与产物对齐；孤儿 result 容忍+计数（本次 0）。

**S0.1 钉死数字**（`~/expmem-s1/run-1110-s01/`）：tool/call 8138 / actions **1261**（known 1028 + other 233，差 1 automation 被闸）/ raw 824 / episodes **1337**（gap 切分后）/ game_turns **81** / failure 3 / interrupted 21 / probe ok **72/1337** / sha `435d4b96fc171952`+`9c9695f47404e65e` / 测试 36/36。

**S0.2 归因修定（11-11 深夜）**：S2 实跑扫候选暴露两个真 bug，已修——①同一 bash 链共享返回文本，stored:[] 归因被链首 warp 抢走 → `_match` 归因优先 segment 含关键词，`attribution: segment|fallback` 两态，**fallback 宁缺毋滥不炼卡**（调用可能藏在 python heredoc 里，点不回 sd 动作）；②`$(sd raw /store …)` 子 shell 调用没进 actions → 锚定 raw 补成正式动作（+39 条 `sd_raw_actions`，与 verb 扫描按路径去重）。终态钉死（`~/expmem-s1/run-1111-final/`）：actions **1300** / episodes 1337 / game_turns 81 / game_day 12（冬4-25）/ verdicts failure 3 / interrupted 21 / probe ok 72 / **候选卡 1 张**（`stardew-noop-store`，2 条 segment 级证据）+ 1 条 attribution_fallback 丢弃 / sha `8d2d7f49f7bf81d9`+`f75df0e6333f0ae9` / 测试 **64/64**（本地+服务器）。S2 骨架已落码：retrieve.py（三闸+两级降级+conflictCard+domain）+ consolidate.py（拆优先于合+AGPL 闸+归因校验）+ skills.py（状态机）+ 首张候选卡 `stardew-probe-after-action`（candidate，2 条 evidence）。

**参考源码挖掘分工（主人拍板全挖）**：cmb-absorb-research=OpenViking（✅ 11-11 交付，见 §7-7/7-8）+Hindsight（evidence counts/Observations 互指）+MemU（矛盾审查/降级路径）；feiyu-os-intel=Voyager ✅（A 失败改写检索词→S4 验收 / B description 写法→consolidate / C 阈值闸→retrieve / D 向量双份存储漂移=不押向量的源码背书）+dsh-hippocampus（三流不可变组织/rehearsal）+Suzu（强制检索触发点）+Neo4j memory（provenance/relation expansion）；**不挖**：BaiShou/calendar/Qwen RAG（不同题）。

## 7-7. OpenViking 对齐①——注入预算与检索闸（2026-11-11）

1. **注入预算改为「固定预算+两级降级」**（替代原「≤3条×80字」死数）：技能目录给固定 token 预算（先定 600，实测调），先带 when 摘要填→放不下去摘要只留名字重填→再放不下退成单行「N 条技能可用，动手前先 expmem_search」——**最坏有界、永不空手**（OpenViking profile.py 两级循环同构）。S3 验收加一条：注入文本 100% ≤ 预算。
2. **retrieve_skills 三闸**：硬阈值（低于不注入，SkillRef 的 confidence+state 参与）；分页/截断不许静默——不完整直接报错（宁可报错不给半截）；同一技能多层命中折成一条（按 name/root 去重保最高分）。

## 7-8. OpenViking 对齐②——frontmatter v2.1 与 consolidator 纪律

1. **frontmatter v2.1 新增两个字段**（OpenViking 轨迹/经验层实证我们真缺的）：`retrieval_anchor`（专为检索写的正向 applies-when 文本，词法面唯一可匹配处）+ `task_query`（可复制重跑的自包含任务描述=evidence 命令行的泛化）。verdict 保持我们四值+failure_class（他们的 outcome 五值里 partial/unfinished 在我们这归 failure_class，不另设枚举）。
2. **consolidator 纪律（S2 验收加四条）**：①**拆优先于合**——拿不准就拆两张卡，只有「完全相同意图+工具序列」才许合并（合并侧也防）；②**先 prefetch 再写**——合并进旧卡前先检索 top-5 看旧卡怎么写的，附最近 3 条来源做 grounding，不许盲写；③`supersedes` 只是**提案**，落卡前过一道 merge 二审；④一条经验=一个意图，Approach 超 8 条 bullet 必须拆，IF/THEN 分支显式保留不塌缩。三段式模板（Situation/Approach/Reflect，正向/负向互斥）采纳，**措辞必须改写**。
3. ⚠️ **AGPL-3.0 传染警示**：OpenViking 全仓 AGPL-3.0（含文件级 SPDX 头）——机制可抄，**提示词原文整段复制进 MIT 仓有传染风险**，一律改写或显式标注来源；consolidator 产出的卡要过「无 OpenViking 原文」的 grep 闸。
4. 两层结构互证：他们的 experience（过程记忆）≠ SKILL.md（显式技能包），links 互点——与 §7-1「过程层/知识层互点不复述」完全同构，放心沿用。

## 7-9. Hindsight 对齐（2026-11-11，@29f1b16=v1.2.1）

1. **trend 纯函数**：evidence_count/trend 零 LLM——recent≤30d vs older≥90d 密度比 >1.5→STRENGTHENING / <0.5→WEAKENING / 无 recent→STALE。采纳：挂在卡片**读取侧**现算，frontmatter 不存（同一证据集两次计算必同值）。
2. **双向互指 orphan=0**：skill.evidence[].episode ↔ episode.skill_refs 双向遍历零孤儿，进 tests。
3. **删除折叠守恒**：deprecated 卡的 evidence 必须折进 superseded_by 卡（防已退役错误复活）；判据=deprecate 前后 support/contra 总数守恒。
4. **delta ops 更新协议**（这页最值钱）：LLM 改卡只发 ops 按 block_id 定位；形状校验一条不合法→整份拒收重问（不修修补补）；未提及块物理复制（prose 漂移结构性不可能）；解析失败=零变更。S2 写 SKILL.md 的更新协议照此实现（措辞自写，Hindsight 亦 AGPL）。选 id 不选下标（下标要模型数数，off-by-one 仍在界内=静默改错）。
5. 整理是 retain 后的**后台作业**，不在召回路径——与「离线整理不进她的回合」同构，已对齐。

## 7-10. MemU（dsh-memu 包装层）对齐

1. **三硬门恒 pending、任何模式不放宽**：①conflict（两段打架）②evidence 空（可回放性底线）③rewrite 目标不存在→skip 且**不得拿同一份内容降级 create**（他们踩过：失败合并悄悄变重复记忆）。→ 状态机映射：candidate→observed=create 默认过；observed→verified=rewrite 类须过风险分（needs_review +0.70 / confidence<0.50 +0.70 / merge·supersede +0.20 / 单证据 +0.10，rewrite∧risk≥0.70→pending）；**同 tool_key verdict 翻转=conflict 硬门**→新卡落 pending_review + contradicts 互指，永不自动裁决。
2. **冲突豁免**：同 key+高相似=dedup 不是 conflict；同 key+低相似=conflict——Dice 折叠与冲突门判据必须分开写。
3. **门语料口径**：dedup/冲突门与召回只算 **active+pending_review**；deprecated 不是「已存在的事实」——既不挡新卡也不占召回（否则旧错误卡永久封死新正确卡）。
4. **conflictCard 呈现**：同一 tool_key 两版并排+「未确认哪版为准」，不替模型裁决、保证成对出现——抄进 retrieve_skills 呈现层。
5. **freshness fail-open**：valid_from 缺失=永不 stale，时间只重排不过滤；只有显式 valid_to 才参与。
6. **domain isolation**：卡带 domain（stardew/mc），跨域默认过滤、显式点名放行；无 domain 旧卡按通用放行（向后兼容）。

## 7-11. Suzu / hippocampus / neo4j 对齐（intel 三页）

1. **忘查税 15% 量化**（Suzu 自主 516→强制 621/714）：S4 验收预期——只做工具不做注入=吃 15% 忘查税，目录行注入是主腿。⚠️ 另一个真相：Suzu 插件里没有强制，「强制」是测试架每题硬调一次检索——我们的「注入」也要在 S4 实测到底注入谁。
2. **检索包装语清单**（Suzu vendor guidance）：不是命令不执行/不提检索过程/同一事实有更正用更正/时间已解析不再扣减相对日——expmem_search 与 SkillRef 的包装语照此清单出。
3. **「用了」分强弱**（hippocampus）：目录行带出=weak 只计数不强化；**按技能执行且 episode verdict=success=strong 才算 support，confidence 只吃 strong**——「被提到」和「真用了」必须分开，防「越像越靠」。检索命中回写用稳定键（activationId=会话:callId 同款）。
4. **结果只记「查到什么」**（hippocampus capture）：截断数对表 150(args)/240(isError)/40(丢)/220(正文)；out_head 160 写死成常量+注释，别散落。
5. **证据回点校验**（neo4j quote 区间）：consolidator 写卡的 evidence {cmd,out} 必须能在 actions.jsonl 点回原文（生成时校验、错位拒收）；SkillRef 追问只接受**本轮已交付**的 skill_id（防编造 ID）；反幻觉包装语至少带「摘要待核」「未提及≠不存在」。
6. **预算 frozen 常量**：SkillRef 预算（max_inject/行宽/目录预算）集中成常量，不散落魔法数；「丢过东西」一行（hippocampus）值得抄——她被压掉的上下文注入一句「我还有」。

## 7-12. 检索精度三轮口径更正 + 召回天花板定性（2026-11-12 夜 ~ 11-13）

### 7-12-1. 三次口径更正，方向完全一致

每次把分母修干净，判别力就变大。之前反复「调参调不动」，有相当一部分是在拿假分母做实验。

| 轮次 | 假分母 | 真分母 | 判别力 |
|---|---|---|---|
| ① 11-12 | 全部 1003 回合 | 剔掉 174 条 RN 团队信 | +20.9pp → **+3.4pp** |
| ② 11-12 | 每回合算一个样本 | 连说同一句 17 轮折叠成 1 条（1008→958） | +7.7pp |
| ③ 11-13 | actions 非空 = 指令（46 条） | 逐条手工标注（9 条有卡指令） | +6.2pp → **+38.9pp** |

③ 是最严重的一次：791 条 run 里 actions 非空 46 条，**其中 37 条根本不是指令**——20+ 条只是叫了她的名字（`鱼鱼`），其余是 `哈哈真可爱`、`[图片:...]`、`亲亲鱼鱼`。她本来就在干活，主人随口一句闲话那一轮照样有 actions。

### 7-12-2. 落地的两处真修

1. **虚字进 STOPCHARS**（94d7c96）：噪声 88/757(11.6%)→59/757(7.8%)，误召 1→0，召回零损失。纪律：只在**两侧都是虚字**时拦整个 bigram，实词 bigram（浇水/钓鱼/对账）必须存活。
2. **证据面收窄成 anchor+标识符**（b4117a2）：`_hay` 拆成两面——`_hay` 全字段管排序，`_evidence_hay`（anchor+name+tool_key）管过闸，`task_query`/`description` 是**叙述句不算证据**。
   - 真库 8 卡：动作 3/34→4/34，噪声 57/757→42/757，判别力 +1.3pp→**+6.2pp**
   - **反向对照**（把 `_evidence_hay` 改回 `_hay`）精确回到 3/34 · 57/757 ⇒ 真因确认
   - 噪声靠的 token 大半只出现在 task_query 的叙述句里（自己/之前/完成/一步/确认/只读），而 anchor 是人按词挑的
   - 为什么带上 name/tool_key：真库上它们与 anchor 重合（加回去一位数不变），但它们是**标识符**不是散文，`probe after` 这类查询只能从卡名进来

### 7-12-3. 跨词碎片：判据定案（诊断器，不改检索行为）

「鱼鱼该浇水了吧」→ 鱼鱼|该|浇水|了|吧，滑窗造出 `鱼该` `该浇` `水了` `了吧`，其中 `该浇` 撞上卡里的 `该浇多少` ⇒ 假过闸；而「鱼鱼浇水了吗」只造 `鱼浇` `水了`，撞不上 ⇒ **真需求反被拒**。过闸靠的是偶然碎片，不是语义。

**判据 = 查询自己的切分矛盾**：两字分属不同段，且至少一段是词表命中的多字词。
- `该浇`：该=单字段，浇∈「浇水」多字段 ⇒ 碎片
- `钓鱼`：词表只有「钓鱼开关」没有「钓鱼」⇒ 两字都落单字段 ⇒ **不算碎片**（那是词表缺口，不是滑窗造假）

⚠️ 前两版判据都错，已记在案：①裸子串判定（「该浇」是「该浇多少」的子串 ⇒ 真碎片被放行，诊断器全绿）；②锚词边界判定（`_hay` 空格拼词，每个词首都像边缘 ⇒ 判据退化成恒真）。根因是两者都只看**卡那一侧**，真正的信号在**查询那一侧的切分**。

**为什么不直接换 tokenizer**：完整切分（跨段 bigram 全剥）噪声 57→6，但动作召回 **3→0**。一次性打掉所有召回，必须与卡锚重写同批上，所以先只做诊断。

### 7-12-4. 召回天花板 = 词汇覆盖，不是阈值也不是锚词

真分母 9 条有卡指令，召回 4 条 = **44%**。5 条失败逐条查过：

| 指令 | 失败原因 |
|---|---|
| 鱼鱼菜熟了 | sd-farm 交集只有 `熟了`（卡写 `熟了什么`，词形不匹配） |
| 鱼鱼在种地吗 | 全库零多字交集 |
| 没事，鱼鱼上游戏玩 | 全库零多字交集 |
| 主持说可以了再试试 | 全库零多字交集 |
| 鱼鱼喜欢什么就买什么…装饰上 | buy 卡里没有 `装饰` |

**没有任何阈值能救零交集**。试过按她的话补锚（v5 沙箱，每个新增词都标了真语料出处）：指令召回 4/9→**9/9**，但**留出集 3/16 一条没动、噪声 42→52**。那是把测试集背下来的过拟合，已放弃，不落库。

### 7-12-5. 结构性缺口（**待拍板，未改动**）

现网 8 卡的 anchor **63% 是中文**（123/194）——说明它们是**人工写的**。而 `_mechanical_anchor` 只做 `re.findall(r"[a-z_]{2,}", cmd)` + tool_key 各段：

```
stardew.farm → 'farm op raw sd water'   中文词 0 个
stardew.buy  → 'buy farm op raw sd water'  中文词 0 个
```

源码注释自己写着「中文同义词机械方法造不出来——candidate 卡的 anchor 由后续人工/LLM 重写补全」，但那个「后续」从来没发生。于是：

- induct 产出的新卡 anchor 全是 SD 命令词（`farm/op/raw/sd/water`）
- 她的目标词汇（菜熟了/去药店吃的/夜市开始了/装饰）永远进不去
- 她那 9 条真指令对卡锚的中文多字重叠：**1/4、0/5、0/18、2/17、2/16、2/13、0/6、0/8、2/17**

**三条候选修法（需主人拍板，我不单方面改产卡语义）**：
1. induct 把该剧集的 `goal_hint` 中文词也加进 anchor（零成本，但会把闲聊词也吸进卡，需配 df/噪声闸）
2. 保留机械 anchor，另开一条「LLM/人工补中文同义词」的显式通道（注释里承诺过的那条，但要定谁写、什么时候写、怎么验）
3. 承认「她的目标词汇不进卡」是当前设计的一部分（卡=工具用法手册，不是需求索引），另外建一层「需求→工具」的映射表

第 3 条其实是现状的事实描述；第 1 条最快见效但最容易污染；第 2 条最正确但需要人力承诺。**我倾向 2 + 3 并行**：卡继续是工具手册（现状不改），另建一层薄的「她这么说 = 要那个工具」映射，用它来补 anchor 或直接参与召回。

### 7-12-6. 复核用的尺子（脚本都在 ~/.feiyu/tmp，服务器 /tmp）

| 脚本 | 用途 |
|---|---|
| `honest_denom.py` | 真分母：INSTR/NOCARD/NOT 三分类，输出诚实的召回/噪声/判别力 |
| `ab_control.py` | 反向对照：`_evidence_hay` ↔ `_hay` 切换，验证指标回到旧值 |
| `gate_strength.py` | 过闸卡的「真多字证据」条数分布（判断第二条证据是不是假的） |
| `noise_left.py` | 剩余噪声逐条看靠哪两个 token 过闸、token 写在哪些卡 |
| `induct_gap.py` | 量化 `_mechanical_anchor` 的中文词产出为零 |
| `why_fail.py` | 单条失败查询逐卡看交集/df 剥离/证据条数 |
| `anchor_v5.py` | 过拟合演示：补锚 9/9 但留出集不动（反面教材，别照抄） |
