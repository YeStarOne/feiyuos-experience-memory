# 致谢与来源声明

本项目在机制设计上参考了以下开源项目（**只借鉴机制与教训，未复制任何原文**——
其中 AGPL 项目按传染要求做了隔离：机制可抄，措辞全部自写，本仓无任何上游提示词原文）：

| 项目 | 许可 | 借鉴点 |
|---|---|---|
| [volcengine/OpenViking](https://github.com/volcengine/OpenViking) | AGPL-3.0 | 技能目录 token 预算两级降级；SKILL.md 正文三段结构；检索硬阈值/不静默截断；蒸馏纪律（拆优先于合、prefetch、supersedes 二审）。⚠️ AGPL：未复制任何文字，产出卡有 grep 闸 |
| [vectorize-io/Hindsight](https://github.com/vectorize-io/hindsight) | AGPL-3.0 | evidence trend 纯函数；双向互指 orphan=0；deprecated 证据折叠守恒；delta-ops 更新协议思想。⚠️ AGPL：同上隔离 |
| [NevaMind-AI/memU](https://github.com/NevaMind-AI/memU)（dsh 包装层快照） | 见上游 | reviewDecision 三硬门；风险分拦改写；门语料只算 active+pending；conflictCard 成对呈现；freshness fail-open；domain 隔离 |
| [MineDojo/Voyager](https://github.com/MineDojo/Voyager) | MIT | 失败改写检索词；description 检索键写法；阈值闸；向量双份存储漂移的反面教训 |
| dsh-hippocampus (coner13) | 见上游 | 「用了分强弱」（weak 只计数 / strong 才进 confidence）；结果只记「查到什么」的截断纪律 |
| neo4j-memory（快照） | 见上游 | evidence 生成时校验可点回原文；追问只接受本轮已交付 id；反幻觉包装语 |
| Suzu（快照） | 见上游 | 忘查税 15% 的量化；检索包装语清单 |

我们自己的实测基线（会话日志重放）与全部代码均为原创；测试夹具为合成数据。
行为数据（actions/episodes）只留在运行环境，不入仓。
