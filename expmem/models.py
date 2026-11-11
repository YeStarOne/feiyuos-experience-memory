# -*- coding: utf-8 -*-
"""数据结构。字段就这些，别加（方案 §2 冻结版）。"""
import json
from dataclasses import dataclass, field, asdict


@dataclass
class Action:
    """一次工具调用（或一次从 bash 里抠出的 sd 动作）。"""
    id: str
    ts: int
    session: str
    turn: int
    step: int
    tool: str          # 'bash' / 'pet_say' / ...
    adapter: str       # 'stardew_sd' / 'generic'
    call_id: str       # dsh 的 callId（证据链的锚点）
    sub_idx: int       # 同一条 bash 里第几个 sd 动作（非 sd 动作恒 0）
    verb: str = ""     # sd 动词；非 sd 动作为空
    raw_path: str = "" # sd raw /path 时的路径
    segment: str = ""  # 原始命令片段（截断存，可点回）
    endpoint: str = "" # 归一后的端点（GET /state / RAW /menu_close）
    is_error: bool = False
    latency_ms: int = -1
    cwd: str = ""
    out_head: str = ""  # 结果文本头 160 字（S1 outcome 的证据来源）；JSON 另附 ⟨关注键摘要⟩
    out_sha: str = ""   # 结果全文 sha1[:16]（cmb-G3：evidence 链必须能点回，schema 承诺兑现）
    poll_repeat: bool = False  # 同 verb 同参数间隔<30s 的轮询重复（cmb-G2：等待风格，别算进 calls-to-goal）
    # S3 前采集修定（additive，不破上面任何字段）：后台化 bash 的终态回填
    out_source: str = ""   # ''=原样 / job_output=由 job_output 终态覆盖
    job_id: str = ""       # 后台 job id（bash-xxxx）
    job_status: str = ""   # completed|killed|…（job/settled 或 job_output 尾巴）
    job_exit: int = None   # 退出码（拿不到为 None，别写成 0）
    job_done_ts: int = None  # job/settled 的 finishedAt（cmb：采信条件拿事件时间判，别拿来源判）

    def row(self) -> dict:
        return asdict(self)


@dataclass
class Episode:
    """一个回合 = 一"局"：turn/start..turn/end 之间的动作集合。"""
    episode_id: str
    session: str
    turn: int
    t_from: int
    t_to: int
    game_day: str = ""            # S1 补：从 /state 抓，双标日期（§7-5）
    goal_hint: str = ""
    actions: list = field(default_factory=list)   # Action.id 列表
    verdict: str = "unknown"      # 四值：success|failure|unknown|interrupted（外部打断≠她的失败）
    # 证据来源（11-12 下一批，additive）：execution=真跑日志（唯一进归纳轨的）；
    # bench/fixture 等其他来源给评测用，consolidate 侧一律拒绝——基准证据
    # 不许冒充她的执行经验（execution_evidence vs benchmark_evidence 分离）
    source: str = "execution"
    probe_status: str = "unavailable"  # ok|unavailable：unavailable→unknown，不进 bench 分母（cmb-B）
    failure_class: str = ""       # noop|blocked|stale|partial（wrong_arg 11-11 退役：钱包共享/隔日结算，余额差不可判扣款；stale/partial 留给 S2）
    act_count: int = 0            # 便利列：本集动作数；game = act_count 且含 sd 动作
    game: bool = False
    outcome: dict = field(default_factory=dict)   # 命中规则的证据（signal/action）
    skill_refs: list = field(default_factory=list)

    def row(self) -> dict:
        return asdict(self)


def jdump(row: dict) -> str:
    """稳定序列化：键排序、无空格差异，保证同输入两次 md5 相同。"""
    return json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
