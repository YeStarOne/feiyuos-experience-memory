"""expmem —— 肥鱼 OS 游戏技能记忆（行为过程 → 技能卡）。

S0 范围（observe-only）：只读会话日志，重放出 actions/episodes + 基线数字。
不写任何线上状态，不进 prompt，不需要重启。"""

__version__ = "0.1.0"
