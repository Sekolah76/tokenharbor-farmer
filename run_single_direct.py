#!/usr/bin/env python3
"""run_single_direct.py — 一次性 shim：直连跑 th_auto_register.register_one()
Tor 在当前网络不可用（relay/bridge 全被封），SOCKS 置空 = 直连。
不改 th_auto_register.py 本体。
"""
import sys
import th_auto_register as tar

# 关键：把硬编码的 SOCKS 代理置空 → requests 走直连
tar.SOCKS = {}

if __name__ == "__main__":
    tar.cmd_single()
