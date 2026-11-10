# feiyuos-experience-memory (expmem)

肥鱼 OS 游戏技能记忆：**行为过程 → 技能卡**。只补「过程」这一层——事实仍归
memory-hub / auto-memory / 手册§8 / shared-memory.md（知识层），两层用 evidence
互相点，不互相复述。

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

- verb 级动作（段首锚定 + 白名单 + heredoc 截断）与 `sd raw <path>` 调用
  （不锚段首，吃 `$(sd raw …)`）是**两把尺子**，有重叠，分开计数。
- 归属闸：automation 会话计数照记，但**不进产物**。
- verdict 四值：success / failure / unknown / interrupted——外部打断不是她的失败，
  不进 bench 分母（cmb 对齐 C 条）。
