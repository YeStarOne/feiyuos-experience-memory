# -*- coding: utf-8 -*-
"""Adapter 协议：采集的唯一写口归 api.record_action，adapter 只做匹配与解析。

协议（方案 §3）：
  NAME            adapter 标识
  match(name,args)-> bool      该条 tool/call 是否归我管
  parse(args)     -> dict      解析出动作明细（stardew_sd: verbs/raw_paths）
"""
