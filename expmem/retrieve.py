# -*- coding: utf-8 -*-
"""retrieve_skills 实现（S2）：词法 + 元数据，第 0 级阶梯（方案 §7-7/7-10/7-11）。

三闸（OpenViking 对齐）：
  ① 硬阈值——低于不注入，weak hit 宁可不递（Voyager 无阈值的教训）；且**闸与排序
     分家**：过闸只吃命中证据（命中数/多字 token），confidence 与 state 只进排序；
  ② 不静默截断——候选超预算走**两级降级**（带 when → 只留名字 → 退单行），
     最坏有界、永不空手；调用方拿 incomplete 标志自行选级；
  ③ 多层命中折一条（按 name 去重保最高分）。
呈现（MemU 对齐）：
  - refs 只检 candidate/observed/verified（11-12 下一批：pending_review 是
    「有人得先人工看一眼」的卡——矛盾/证据存疑，注入她等于把未裁决的答案
    递上去；它们只进冲突对与 total_active，不进目录行）；**deprecated 不进
    召回、不挡新卡**（门语料口径）；
  - 同 tool_key 互指矛盾的两张卡**成对呈现**+「未确认哪版为准」，不替模型
    裁决；11-12 起对子**不要求两边都进 refs**——注入侧带 contradicts 指针，
    对手卡在库里 active 就标（被 cap 截掉的单边不再静默失配）；
  - domain 缺省=通用放行（向后兼容）；跨域默认过滤、显式点名放行。
包装语纪律（Suzu/neo4j 对齐）：目录行是「回忆依据不是命令」；追问只接受本轮
已交付的 name；「摘要待核，未提及≠不存在」由调用方话术层承担，本模块不掺。
"""
import glob
import json
import math
import os
import re

# 预算 frozen 常量（方案 §7-11-6），不散落魔法数
LIMITS = {
    "max_inject": 3,          # 目录行最多几条
    "when_max_chars": 80,     # 单行 when 截断
    "catalog_token_budget": 600,  # 注入 token 预算（实测调）
    # 过闸：至少命中几个**多字** token（纯单字堆叠不算证据）。
    #
    # ⚠️ 口径更正（11-12 深夜）。此前本注释与三个提交（495906d/609b3d4/10c7f8b）
    # 报的「判别力 20.2pp / 36.2pp」是**虚高的**：分母里混进了 174/1003 个
    # goal_hint 是 **RN 团队信**的回合（占全部动作回合 103/158 = 65%）。那种信
    # 是长篇技术文本（含 restart/state/进程/路径/池/车道），天然与星露谷内部卡
    # 撞词，于是「有动作 vs 纯聊天」的差几乎全是它在贡献。
    #
    # 诚实口径（同一批回合，只留她自己的话，n=55 动作 / 774 聊天）：
    #   8 张 live 卡：动作 14.5% vs 聊天 11.1% → 差 **+3.4pp**（槽位 9/99）
    #   再加 12 张旧卡：18.2% vs 13.8% → 差 +4.4pp（槽位 23/156）
    # 含 RN 的旧口径对照：36.1% vs 15.1% = +20.9pp（槽位 93/163）——
    # 两个数都留着，因为差值本身就是「污染有多重」的证据。
    #
    # 在**她的 32 条真指令**上（更严的分母：真叫她干活的回合）：
    #   min_multi=1 → 真指令 31.2% / 非指令 45.8% → **−14.5pp**（比瞎猜还差，
    #                 且 732 个噪声槽位；头号噪声源是【角色卡】36 次）
    #   min_multi=2 → 18.8% / 11.0% → +7.7pp（102 槽位）
    #   min_multi=3 → 真指令召回归零（闸关死）
    # 所以 2 是唯一可取的档；但注意 **+7.7pp 是小样本上的小效应**，不是
    # 当初那个 +20pp 的强判别力。
    #
    # 真正的瓶颈不在阈值：她真指令的多字 token 里只有 **8.2%（22/269）** 出现在
    # 8 张卡的 anchor 中（卡是 SD 命令词汇 warp/state/inv/fishbot，她说的是
    # 游戏目标「去药店吃的」「菜熟了」「夜市开始了」）。8.2% 的重叠**没有任何
    # 阈值能救**——要改的是卡锚本身（见 docs 里的待办）。
    # 单字召回也不能当杠杆：实测关掉 CJK 单字判别力一点没变。
    #
    # ⚠️⚠️ 再更正一次分母（11-12 深夜，第三次）：上面那个「32 条真指令」也
    # 是错的。判据用的是「该回合 actions 非空」，但她本来就在干活——主人随口
    # 一句「鱼鱼」/「哈哈真可爱」/一张图片，那一轮照样有 actions。实测
    # 791 条 run 里 actions 非空 46 条，**其中 37 条根本不是指令**（20+ 条
    # 只是叫了她的名字）。逐条手工标注后的真分母：
    #   有卡可召回的指令      9 条  → 召回 4 = 44%
    #   真需求但库里无卡      5 条  → 吃/节日/夜市/远程桌面（不算进召回分母）
    #   不是指令            777 条
    # 于是判别力 = 44% − 5.5% = **+38.9pp**（旧口径报的 +6.2pp 是分母被稀释）。
    # 三次口径更正的方向一致：**每次把分母修干净，判别力就变大**。这说明
    # 之前反复"调参调不动"有很大一部分是在拿假分母做实验。
    #
    # 那 44% 的上限是什么？**不是阈值、也不是锚词**。5 条失败逐条查过：
    #   「鱼鱼菜熟了」  sd-farm 交集只有 `熟了`（卡写 `熟了什么`，词形不匹配）
    #   「鱼鱼在种地吗」/「没事鱼鱼上游戏玩」/「主持说可以了再试试」
    #                    → **全库零多字交集**，卡里根本没有这些词
    # 我试过按她的话补锚（v5 沙箱，每个新增词都有真语料出处）：指令召回
    # 4/9→**9/9**，但留出集 3/16 一条没动、噪声 42→52。那是把测试集背下来
    # 的过拟合，已放弃。**结论：召回天花板由「她的目标词汇是否在卡里」决定，
    # 不是检索算法**。要动的是产卡管线（induct 只从机械可验证的成败产卡，
    # 见 docs/待办），不是这里。
    "min_multi_hits": 2,
}
ACTIVE_STATES = ("candidate", "observed", "verified", "pending_review")
# 进目录行（refs）的状态——pending_review 只进冲突对/total_active（11-12 下一批）
REF_STATES = ("candidate", "observed", "verified")
_STATE_BONUS = {"verified": 2.0, "observed": 1.0, "candidate": 0.0, "pending_review": 0.0}
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
# intel 实测：阈值 1.0 + 单字召回叠加后，中文功能字单字会让任何卡片过闸
# （「我今天是不是该收菜了」→ 我/是/不/了 全是噪声却计分）。功能字不进单字召回，
# bigram 与实词单字照旧。
#
# 11-12 深夜追加「一下个什么吧呀哈么」：真库实测噪声 88/757(11.6%) → 59/757(7.8%)，
# 误召 1→0，**召回一条没掉**。它们和第一批同性质——「一下」「什么」不是内容，
# 是句法。头号案发现场：她随口一句「哈哈，鱼鱼发一下今天的黑盒文章草稿吧」
# 靠 `一下`+`发一` 两个纯虚 bigram 命中 sd-act（df 剥离抓不到它们：卡里只出现
# 在 2 张，够不上 df_cap=5）。
# 纪律：这批字只能**在两侧都是虚字**时才拦掉整个 bigram（见 _tokens），
# 实词 bigram（浇水/钓鱼/对账/菜熟了）必须存活——已逐条验证零误伤。
STOPCHARS = frozenset("的了吗呢是我你他这那不有在就都也还一下个什么吧呀哈么")

# 她的名字/团队称呼——诊断「跨词碎片」时这些一律算已知词。
# 起因：碎片诊断的词表取自卡锚，而卡里永远不写「鱼鱼」（卡是给她的操作手册，
# 不会自称名字）⇒「鱼鱼该浇水了吧」里的「鱼鱼」会被误报成碎片。它不是碎片，
# 它是一个完整的名字。注意「主持」在本库 8 卡里真的没出现，所以它在诊断报告
# 里仍会被报——那是**真事实**（卡里没这个词），不是误报。
_KNOWN_WORDS = frozenset((
    "鱼鱼", "肥鱼", "小肥鱼", "主持", "主人", "星露谷", "我的世界",
))


def estimate_tokens(s: str) -> int:
    """粗估：CJK/全角 ≈1.5 token/字（cmb 实测口径），ASCII ≈0.3 token/字符。"""
    wide = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff"
               or "\u3000" <= ch <= "\u303f" or "\uff00" <= ch <= "\uffef")
    return int(wide * 1.5 + (len(s) - wide) * 0.3) + 1


def _tokens(s: str):
    """cmb-P0：空白分词+整 token 相等在中文上零召回（「怎么买种子」整串一个
    token，与 hay 永不相交，连弱命中都拿不到）。

    修法（Suzu 同款加宽）：复合 token 按 [-_./] 再拆一层（11-12 夜加 '/'：
    卡里 warp/move/face 斜杠连体和查询里图片路径 [图片:/home/...] 都会产出
    'e/''nt''t/' 碎片乱匹配——路径分隔符两侧都必须切开）；破折号族（—–―─－）
    同理切成空格：它是**标点不是词**，留在串里既自己当证据、又把邻字粘成
    '手—''—不''天—' 这种垃圾 bigram（真库实测 8 卡里有 4 张的 description
    写了「——」，聊天回合噪声 top2 全靠它）；CJK 串出 **单字+bigram**
    （单字管召回，bigram 管精度），英文原样。"""
    out = set()
    s = re.sub(r"[\u2014\u2013\u2015\u2500\uff0d]+", " ", (s or "").lower())
    for t in re.split(r"[\s，,。？！?!；;、（）()]+", s):
        if not t:
            continue
        for sub in re.split(r"[-_./]+", t):
            if not sub:
                continue
            # 中英混排必须先按字符族切开再切 bigram（11-12 夜修）。旧写法对含
            # 中文的整段统一切 bigram，跨界处必然造垃圾：`restart` → es/ta/ar/st、
            # `yestar` → ye/es、`读state校验` → 读s/e校。这些碎片**跨卡通用**
            # （凡卡里提过 restart/state 就都产同一批），真库实测
            # `stardew-persist-fish-guard` 靠 es+ta 两个碎片命中 103/158 个
            # **动作回合**和 71/844 个聊天回合——不是它相关，是碎片撞上了。
            # 切开后：CJK 段照旧单字+bigram（召回不变），ASCII 段整段当词
            # （`restart` 仍能与查询里的 `restart` 精确对上，只是不再吐碎片）。
            for run in re.finditer(r"[\u4e00-\u9fff]+|[^\u4e00-\u9fff]+", sub):
                seg = run.group()
                if _CJK_RE.search(seg):
                    out.update(ch for ch in seg if ch not in STOPCHARS)  # 单字管召回（功能字除外）
                    if len(seg) >= 2:
                        # bigram 管精度；但**纯功能字 bigram**（不是/是不/了么）不算证据
                        # ——cmb 实测：卡里写「不是」时 query「是不是」就会命中它
                        out.update(seg[i:i + 2] for i in range(len(seg) - 1)
                                   if not all(ch in STOPCHARS for ch in seg[i:i + 2]))
                else:
                    out.add(seg)
    return out


def load_catalog(skills_dir: str):
    """扫 SKILL.md / *.md 的 frontmatter，返回 active 卡列表。

    两类不进：deprecated（打脸不删卡但不再召回）；counter≤0（ExpeL REMOVE——
    台账清零的卡移出书，文件留盘。缺省 counter=基线 1，旧卡不受影响）。"""
    fronts = []
    paths = sorted(glob.glob(os.path.join(skills_dir, "**", "*.md"), recursive=True))
    for p in paths:
        try:
            with open(p, encoding="utf-8") as f:
                text = f.read()
        except Exception:
            continue
        if not text.startswith("---"):
            continue
        end = text.find("\n---", 3)
        if end < 0:
            continue
        try:
            front = json.loads(text[3:end].strip())
        except Exception:
            continue  # 坏卡跳过但留给 lint 报（这里不抛，检索不许被一张坏卡打死）
        if front.get("state") not in ACTIVE_STATES:
            continue
        try:
            counter = int(front.get("counter", 1) or 0)
        except Exception:
            counter = 1
        if counter <= 0:
            continue
        front["_path"] = p
        fronts.append(front)
    return fronts


def audit_unreachable(skills_dir: str) -> dict:
    """哪些卡**永远进不了检索**——静默盲区审计（11-12 夜）。

    起因：真库 21 张卡里 13 张从来没被召回。它们不是被闸挡的（闸挡了会进
    dropped_weak，看得见），而是**根本没进 catalog**：缺 `state`（旧卡手写，
    frontmatter 只有 description/confidence/evidence），或整个文件没有
    frontmatter。这种卡在检索侧与「不存在」等价，而 `load_catalog` 静默
    `continue`——没有任何地方会报出来，她也永远不知道这本错题本有这几页。

    两种盲区分开报（修法完全不同）：
      - `unreachable`：load_catalog 直接跳过（缺 state / 无 frontmatter /
        坏 JSON / counter≤0）——**加字段就能救**；
      - `weak_hay`：进了 catalog，但缺 retrieval_anchor/task_query ⇒ `_hay`
        只剩 name+description，几乎与任何查询零相交——**要重写锚词**，
        补字段救不了（它本来就在库里，只是召回不了）。

    返回 {"total", "loaded", "unreachable": [...], "weak_hay": [...]}。
    reason 只报**第一处**缺失，便于一次修一个（缺 state 的卡补完 state 往往
    还会暴露缺 name，分两轮比一次报五个好改）。"""
    out = {"total": 0, "loaded": 0, "unreachable": [], "weak_hay": []}
    paths = sorted(glob.glob(os.path.join(skills_dir, "**", "*.md"), recursive=True))
    by_path = {f["_path"]: f for f in load_catalog(skills_dir)}
    for p in paths:
        out["total"] += 1
        loaded = by_path.get(p)
        if loaded is not None:
            out["loaded"] += 1
            missing = [k for k in ("retrieval_anchor", "task_query")
                       if not loaded.get(k)]
            if missing:
                out["weak_hay"].append({
                    "path": p,
                    "reason": "缺 %s（在库里但 _hay 只剩 name+description，"
                              "几乎与任何查询零相交）" % "/".join(missing),
                })
            continue
        reason = None
        try:
            with open(p, encoding="utf-8") as f:
                text = f.read()
        except Exception as e:
            out["unreachable"].append({"path": p, "reason": "读不出来: %r" % e})
            continue
        if not text.startswith("---"):
            reason = "没有 frontmatter（整篇都是正文）"
        else:
            end = text.find("\n---", 3)
            if end < 0:
                reason = "frontmatter 未闭合"
            else:
                try:
                    front = json.loads(text[3:end].strip())
                except Exception:
                    reason = "frontmatter 不是合法 JSON"
                else:
                    if front.get("state") not in ACTIVE_STATES:
                        reason = "state=%r 不在 ACTIVE_STATES（旧卡手写缺这个字段）" % (
                            front.get("state"),)
                    else:
                        try:
                            if int(front.get("counter", 1) or 0) <= 0:
                                reason = "counter≤0（ExpeL 移出书）"
                        except Exception:
                            pass
        out["unreachable"].append({"path": p, "reason": reason or "未知（load_catalog 跳过）"})
    return out


def _hay(front: dict) -> str:
    """**排序面**：全字段。confidence/state bonus 之外的相似度都用它。"""
    return " ".join([front.get("retrieval_anchor", ""), front.get("task_query", ""),
                     front.get("name", ""), front.get("tool_key", ""),
                     front.get("description", "")])


def _evidence_hay(front: dict) -> str:
    """**证据面**：`retrieval_anchor` + 标识符（name / tool_key）。叙述句不算证据。

    11-12 深夜拆分，真库 8 卡实测（34 动作 / 757 纯聊天 / 22 标注）：

        证据面=全字段  动作召回 3/34=8.8%  噪声 57/757=7.5%  ⇒ 判别力 +1.3pp
        证据面=anchor  动作召回 4/34=11.8% 噪声 42/757=5.5%  ⇒ 判别力 +6.2pp

    两个方向同时变好，且没加任何黑名单——这是结构性修法。原因在数据里：
    噪声靠的 token 大半只出现在 `task_query` 的**叙述句**里
    （自己/之前/完成/一步/确认/只读），而 anchor 是人按词挑的。

    为什么带上 name/tool_key：真库上它们与 anchor 重合，加上去**一位数都不变**
    （4/34 · 42/757），所以那轮测不出区别；但它们是**标识符**不是散文，
    `probe after` 这类查询只能从卡名进来（`test_compound_token_split` 就是
    靠这个入口，剥掉 name 会让它退化成 0）。判据是「标识符 vs 散文」，
    不是「anchor vs 其它」——散文（task_query / description）只进排序。

    ⚠️ 别把 task_query 完全废掉：它是 `when`（注入目录行里给她看的那句话）
    的唯一来源，也参与排序。这里剥的只是「它能不能当**过闸证据**」。
    """
    return " ".join([front.get("retrieval_anchor", ""), front.get("name", ""),
                     front.get("tool_key", "")])


def _evidence(q_tokens: set, front: dict, domain=None):
    """闸与排序分家（cmb 验收口径）：本函数只算**命中证据**，不掺 confidence/state。

    返回 (hit, matched)。跨域默认过滤→hit=0（显式点名时由调用方换 domain 参数）。
    证据只从 `_evidence_hay` 取（见那里的实测：全字段进闸等于把卡正文措辞当证据）。
    """
    d = front.get("domain")
    if domain and d and d != domain:
        return 0, set()
    matched = q_tokens & _tokens(_evidence_hay(front))
    return len(matched), matched


def _df_cap(n: int) -> int:
    """通用 token 判别阈值：在 ≥cap 张卡的 hay 里出现过的 token 无鉴别力。

    **固定 5，不随卡数缩放**（11-12 夜修正）。旧写法 `max(4, ceil(0.6N))`
    是反的：N 越大 cap 越大 ⇒ 图书馆越大闸越松。实测 N=8 时 cap=5（「这张」
    级模板词被剥），N=20 时 cap=12、N=50 时 cap=30——那时任何词都够不上
    「通用」，闸形同虚设。零假设对照实锤了这一点：往 8 卡里塞 12 张**纯随机
    噪声卡**（df 全是 1），判别力从 36.2pp 涨到 51.5pp、槽位从 161/236 涨到
    306/297——涨的全是噪声，纯粹因为 cap 从 5 抬到了 12。改成固定 5 后，
    同样塞 12 张随机噪声卡：判别力与槽位**一位数都不变**（噪声卡 df=1 < 5，
    照旧当证据，但它们与任何查询都不相交，所以零命中）。

    5 的来源：现网 8 卡实测与缩放版完全一致，是「不改变现网行为」的那个值；
    固定 3/4 会剥掉真证据，固定 8 略松（但 51-100 字桶更差）。

    ⚠️ 11-12 深夜口径更正：上面那些 pp 数（+21.3pp 等）都含 RN 团队信污染，
    诚实值见 LIMITS["min_multi_hits"] 的注释。**但这个函数的结论不受影响**
    ——它是用「零假设对照」（塞随机噪声卡，看判别力动不动）证明的，那条
    对照与 RN 污染无关。"""
    return max(4, min(5, max(n, 1)))


def cross_word_fragments(q: str, lexicon) -> list:
    """查询里哪些命中是**跨词边界的碎片**——不是词，是滑窗的副产品。

    「鱼鱼该浇水了吧」→ 鱼鱼|该|浇水|了|吧，滑窗造出「鱼该」「该浇」「水了」
    「了吧」四个跨词垃圾，其中「该浇」撞上卡里写的「该浇多少」⇒ 假过闸；
    而「鱼鱼浇水了吗」只造「鱼浇」「水了」，撞不上 ⇒ **真需求反被拒**。
    换句话说：过闸靠的是偶然碎片，不是语义。

    量化（757 条纯聊天 / 她 29 条带动作指令 / 16 条留出问句）：
        滑窗 tokenizer        mm=2 → 召回 3/6  噪声 11.6%
        剥掉跨词碎片           mm=2 → 召回 0/6  噪声  1.3%
    ⇒ **现有召回 3 条里 3 条全是碎片撑起来的**。这不是"优化没做好"，是
    "这个指标一直在被自己的 bug 喂分"。

    本函数只做诊断、不改检索行为（改 tokenizer 会一次性打掉所有召回，
    必须与卡锚重写同批上）。调用方拿它出审计报告：哪些卡是靠碎片进目录的。

    **判据（只认「切分矛盾」，不认「词表缺口」）**：用词表对查询做最长匹配切分，
    一个 bigram 被判为碎片，当且仅当它某次出现里两字**分属不同段**，
    且其中至少一段是**词表命中的多字词**。

      「该浇」：该=单字段，浇∈「浇水」多字段 ⇒ 碎片（查询切分亲口说这两字不同词）
      「钓鱼」：词表只有「钓鱼开关」没有「钓鱼」⇒ 两字都落单字段 ⇒ **不算碎片**
                （那是词表缺口，卡里没写这个词，不是滑窗造出来的）

    ⚠️ 这条区分是踩了两次坑才定下来的。前两版都错：
      ① 裸子串判定：卡写「该浇多少」时「该浇」是它的子串 ⇒ 真碎片被放行，诊断器全绿；
      ② 锚词边界判定：`_hay` 用空格拼词，**每个词首都像边缘** ⇒ 判据退化成恒真。
    根因是「边缘/子串」都只看卡那一侧；真正的信号在**查询那一侧的切分**。

    参数：
      lexicon = 中文词表。建议直接用卡库 anchor+task_query 的整词——人写锚
                时就是按词写的，所以它天然是分词结果，零依赖、零维护。
                词表缺词只会让碎片**漏报**（更保守），不会误报。

    返回碎片列表（字符串）。

    词表补充：她自己的称呼（鱼鱼/肥鱼/主持/小肥鱼…）一律算已知词——
    它们是名字不是跨词碎片，词表来自卡锚时必然漏掉它们（卡里不写自己名字）。
    """
    lex = {w for w in lexicon if len(w) >= 2
           and all("\u4e00" <= c <= "\u9fff" for c in w)}
    lex |= {w for w in _KNOWN_WORDS
            if all("\u4e00" <= c <= "\u9fff" for c in w)}
    if not lex:
        return []
    maxl = max(len(w) for w in lex)
    owner, i = {}, 0
    while i < len(q):
        for L in range(min(maxl, len(q) - i), 0, -1):
            w = q[i:i + L]
            if L == 1 or w in lex:
                for k in range(i, i + L):
                    owner[k] = (i, i + L)
                i += L
                break
        else:
            i += 1
    out = []
    for t in sorted(_tokens(q)):
        if not (len(t) == 2 and all("\u4e00" <= c <= "\u9fff" for c in t)):
            continue
        if t in lex:
            continue          # 词表里的词本身就是词，永远不会是碎片
        pos = [k for k in range(len(q) - 1) if q[k:k + 2] == t]
        hit = False
        for k in pos:
            a, b = owner.get(k), owner.get(k + 1)
            if a is None or b is None or a == b:
                continue
            # 至少一段是词表命中的多字词 ⇒ 查询切分明确说这两字不同词
            if (a[1] - a[0]) >= 2 or (b[1] - b[0]) >= 2:
                hit = True
                break
        if hit:
            out.append(t)
    return out


def audit_fragment_evidence(catalog, queries) -> dict:
    """审计：给定一批查询，哪些**过闸的卡**其实是靠跨词碎片进的目录。

    返回 {"queries": n, "recalled": n, "fragment_only": [{query, card, frags}],
    "clean": n}。fragment_only 非空 = 那几条召回不可信，指标被碎片喂分了。
    lexicon 取自 catalog 自己的锚词（见 cross_word_fragments）。
    """
    lex = set()
    for f in catalog:
        blob = (f.get("retrieval_anchor", "") + " "
                + f.get("task_query", ""))
        for t in blob.split():
            if len(t) >= 2 and all("\u4e00" <= c <= "\u9fff" for c in t):
                lex.add(t)
    # 与生产路径同一把尺子：df 剥离后的 real 才是过闸真正吃到的证据
    df = _df_map(catalog)
    df_cap = _df_cap(len(catalog))
    rep = {"queries": 0, "recalled": 0, "clean": 0, "fragment_only": []}
    for q in queries:
        rep["queries"] += 1
        res = retrieve_skills(q, catalog)
        if not res["refs"]:
            continue
        rep["recalled"] += 1
        frags = set(cross_word_fragments(q, lex))
        if not frags:
            rep["clean"] += 1
            continue
        # 逐卡看：这张卡过闸用的多字命中里，有几个是碎片
        q_tokens = _tokens(q)
        bad = []
        for r in res["refs"]:
            f = next((c for c in catalog if c.get("name") == r["name"]), None)
            if f is None:
                continue
            matched = q_tokens & _tokens(_evidence_hay(f))
            multi = [t for t in matched if len(t) >= 2
                     and df.get(t, 0) < df_cap]      # 剥掉模板词，同 retrieve_skills
            junk = [t for t in multi if t in frags]
            real = [t for t in multi if t not in frags]
            # 判据不是"碎片占多数"，而是**剥掉碎片后还过不过得了闸**：
            # 过闸要 min_multi_hits 条多字命中，剩不到就是这张卡靠碎片进的目录。
            if junk and len(real) < LIMITS["min_multi_hits"]:
                bad.append({"card": r["name"], "fragments": sorted(junk),
                            "real": sorted(real)})
        if bad:
            rep["fragment_only"].append({"query": q, "cards": bad})
        else:
            rep["clean"] += 1      # 有碎片但剥掉后仍过闸 ⇒ 这条召回是真材实料
    return rep


def _df_map(catalog) -> dict:
    """token → 出现在几张卡的**证据面**里（机械闸先跑真产物命中数教训的产物：
    真库 8 卡全含「这张卡」，任何带「这张」的聊天回合能命中全部卡再靠
    confidence 瞎选——手工黑名单追不完模板词，语料自己会招供）。

    11-12 深夜起按 `_evidence_hay` 算（不是 `_hay`）：df 的用途是判断
    「这个 token 有没有鉴别力」，而它只在**证据面**上参与过闸，排序面多宽都
    不影响闸。按全字段算会把只存在于叙述句里的词算进 df，反而放松了剥离。
    """
    df = {}
    for f in catalog:
        for t in _tokens(_evidence_hay(f)):
            df[t] = df.get(t, 0) + 1
    return df


def _passes(hit: int, matched: set) -> bool:
    """过闸判据——只许吃命中证据，confidence 与 state bonus 一律不进（否则零相关卡
    只要 conf 高就能混进目录行，阈值形同虚设）：

    ① 必须至少命中一个**多字** token（bigram/英文词有形状）。纯单字堆叠不算证据——
       cmb 实测「一下」在真卡上 matched=['一','下'] hit=2 就过闸了，而「一下」
       是她最常说的话；
    ② hit≥min_hits：多个词同时打中，噪声概率极低；
    ③ hit==1 时同样要满足①（单字唯一命中，例如「一」「我」，直接不递）。
    """
    if hit == 0:
        return False
    # 证据 = 至少一个多字命中（bigram/英文词有形状）；纯单字堆叠一律不算
    return sum(1 for t in matched if len(t) >= 2) >= LIMITS["min_multi_hits"]


def _rank(hit: int, front: dict) -> float:
    """排序分（可以吃 confidence/state/counter——它决定先后，不决定进不进目录）。
    cmb 夹具③：同等命中下 conf 高的排前面，conf=0 的卡照样过闸。
    ExpeL：counter 降序=注入序——同等证据命中下台账厚的排前面。intel 复审：
    封顶 20（+1.0）=白送一记命中且后续证据压不下去 → 封顶 6（+0.3），
    证据 hit 永远压过台账。"""
    try:
        counter = int(front.get("counter", 1) or 0)
    except Exception:
        counter = 1
    return round(hit + _STATE_BONUS.get(front.get("state", "candidate"), 0.0)
                 + float(front.get("confidence", 0) or 0)
                 + 0.05 * min(max(counter, 0), 6), 2)


def retrieve_skills(q: str, catalog, domain=None, top_k=None):
    """返回 {"refs": [...], "incomplete": bool, "dropped_weak": int,
    "conflict_pairs": [...], "pending_hidden": int, "generic_only": int,
    "total_active": int}。

    refs 已按 name 去重保最高分；deprecated 不进；pending_review 有命中也不进
    refs（只进冲突对，pending_hidden 计数）；过闸只吃命中证据（见 _passes），
    且命中里**全库通用 token（出现在 ≥df_cap 张卡）先剥离**——模板词「这张」
    级命中不算证据，剥完空的不进 dropped_weak（generic_only 单独计数，
    11-12 夜真库反事实实锤：聊天回合靠「这张」命中全卡再靠 confidence 瞎选）。
    dropped_weak = 有命中但没过闸的卡数（cmb 验收：它必须重新具备鉴别力）。
    total_active = 库里 active 卡总数——refs=∅ 时调用方据此发「另有 N 条」提示
    （少递但不瞒着她，cmb：否则「宁缺毋滥」等价于她永远不知道有这本错题本）。"""
    q_tokens = _tokens(q)
    total_active = len(catalog)
    by_name = {}
    for f in catalog:
        nm = f.get("name", "")
        if nm and nm not in by_name:
            by_name[nm] = f
    if not q_tokens:
        return {"refs": [], "incomplete": False, "dropped_weak": 0,
                "conflict_pairs": [], "pending_hidden": 0, "generic_only": 0,
                "total_active": total_active}
    scored, dropped_weak, pending_hidden, generic_only = [], 0, 0, 0
    df = _df_map(catalog)
    df_cap = _df_cap(total_active)
    pairable = []          # 进冲突对判定的卡：过闸 refs + 过闸 pending
    for f in catalog:
        hit, matched = _evidence(q_tokens, f, domain=domain)
        if hit == 0:
            continue
        # 通用 token 剥离（11-12 夜反事实实锤）：命中全由「这张」级模板词凑出
        # 的卡不算证据——剥完空了连 dropped_weak 都不进（纯聊天回合不该给
        # dropped_weak 灌水），单独计数 generic_only
        real = {t for t in matched if df.get(t, 0) < df_cap}
        if not real:
            generic_only += 1
            continue
        if not _passes(len(real), real):
            dropped_weak += 1
            continue
        if f.get("state") == "pending_review":
            pending_hidden += 1
            pairable.append(f)
            continue
        scored.append((_rank(len(real), f), f, len(real)))
        pairable.append(f)
    scored.sort(key=lambda x: (-x[0], x[1].get("name", "")))
    # 多层命中折一条（同 name 保最高分——scored 已排序，首个即最高）
    seen, refs = set(), []
    for s, f, hit in scored:
        nm = f.get("name", "")
        if nm in seen:
            continue
        seen.add(nm)
        refs.append({
            "name": nm, "tool_key": f.get("tool_key", ""), "state": f.get("state", ""),
            "confidence": f.get("confidence", 0), "version": f.get("version", 1),
            "when": (f.get("task_query") or f.get("description") or "")[:LIMITS["when_max_chars"]],
            "score": s, "hits": hit, "contradicts": f.get("contradicts") or [],
        })
    cap = top_k or LIMITS["max_inject"]
    incomplete = len(refs) > cap
    refs = refs[:cap]
    # conflictCard（MemU）：同 tool_key 且 contradicts 互指的两张卡成对呈现，
    # 不替模型裁决。11-12 起：①对手卡查全库（不要求它也过闸/进 refs——注入侧
    # 的 contradicts 是名字指针，对手在库里 active 就值得亮「有两版」）；
    # ②pending_review 卡参与配对（矛盾卡落 pending 后成对警告不许消失）；
    # ③deprecated 对手不算（降废的旧版不算悬案）。
    pairs, pair_seen = [], set()
    for f in pairable:
        a = f.get("name", "")
        for c in f.get("contradicts") or []:
            partner = by_name.get(c)
            if not partner or partner.get("state") not in ACTIVE_STATES:
                continue
            if partner.get("tool_key", "") != f.get("tool_key", ""):
                continue
            p = tuple(sorted([a, c]))
            if p not in pair_seen:
                pair_seen.add(p)
                pairs.append(list(p))
    return {"refs": refs, "incomplete": incomplete,
            "dropped_weak": dropped_weak, "conflict_pairs": pairs,
            "pending_hidden": pending_hidden, "generic_only": generic_only,
            "total_active": total_active}


def empty_hint(result) -> str:
    """refs=∅ 但库里确实有货时给一句话（少递但不瞒着）；库空则留空。

    调用方拿它当「另有 N 条经验技能可用，动手前先查一次」的落点——Suzu 强制检索
    的 +105 就靠这句把「不知道有错题本」变回「知道但不硬塞」。"""
    if result.get("refs") or not result.get("total_active"):
        return ""
    return "另有 %d 条经验技能可用；动手前先 expmem_search 查一次。" % result["total_active"]


def catalog_lines(result, token_budget=None) -> tuple:
    """两级降级成注入文本（OpenViking 预算循环，最坏有界、永不空手）。
    P2-4：返回 (text, level)，level ∈ 1=完整行/2=只留名字/3=单行退守——
    调用方据此知道 degradation 发生在哪一级，别让「降级」变成静默。"""
    budget = token_budget or LIMITS["catalog_token_budget"]
    refs = result.get("refs") or []
    if not refs:
        return "", 0
    def level1():
        lines = []
        for r in refs:
            lines.append("%s｜%s（%s v%s conf%.2f）" % (
                r["name"], r["when"], r["state"], r["version"], r["confidence"]))
        for a, b in result.get("conflict_pairs") or []:
            lines.append("⚠️ %s 与 %s 同键两版，未确认哪版为准" % (a, b))
        return "\n".join(lines)
    def level2():
        return "\n".join(r["name"] for r in refs)
    def level3():
        return "另有 %d 条经验技能可用；动手前先 expmem_search 查一次。" % len(refs)
    for i, cand in enumerate((level1(), level2(), level3()), start=1):
        if estimate_tokens(cand) <= budget:
            return cand, i
    return level3(), 3
