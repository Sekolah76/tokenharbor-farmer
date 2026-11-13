#!/usr/bin/env python3
"""dig_login_form.py — 提取登录页表单结构 + action key + 全部 server reference"""
import requests, re

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"
BASE = "https://tokenharbor.ai"

r = requests.get(f"{BASE}/login", headers={"User-Agent": UA}, timeout=30)
html = r.text

print("== hidden inputs / ACTION markers in HTML ==")
for pat in (r'\$ACTION[^"\'<>\s]*', r'action="[^"]*"', r'name="\$[^"]+"'):
    hits = sorted(set(re.findall(pat, html)))
    for h in hits[:20]:
        print(f"  [{pat[:12]}] {h}")

print("\n== form tags ==")
for m in re.finditer(r'<form[^>]*>', html):
    print(" ", m.group(0)[:200])

print("\n== flight data: action key / encoded refs ==")
# $ACTION_KEY 通常出现在 flight 流里; 也找 ACTION_ 字段名样例
for kw in ("ACTION_KEY", '"1_$ACTION', 'ACTION_ID', '"$ACTION_REF'):
    idxs = [m.start() for m in re.finditer(re.escape(kw), html)]
    print(f"  {kw}: {len(idxs)} hits")
    for i in idxs[:2]:
        print(f"    ctx: ...{html[max(0,i-120):i+180]}...".replace("\n", " "))

# 全部 createServerReference 与名字
chunk = requests.get(f"{BASE}/_next/static/chunks/0r7280ofu-98y.js", headers={"User-Agent": UA}, timeout=30).text
print("\n== all createServerReference in main chunk ==")
for m in re.finditer(r'createServerReference\)\("([0-9a-f]{40,42})",[^)]*?"(\w+)"\)', chunk):
    print(f"  {m.group(2)} = {m.group(1)}")
for m in re.finditer(r'"([0-9a-f]{40,42})",g\.callServer,void 0,g\.findSourceMapURL,"(\w+)"', chunk):
    print(f"  {m.group(2)} = {m.group(1)}")

# signUp 调用上下文：看 signUp action 被怎么包（bind 参数、hidden 字段名）
print("\n== signUp usage context ==")
for m in re.finditer(r'signUp', chunk):
    s = max(0, m.start() - 200); e = min(len(chunk), m.end() + 200)
    ctx = chunk[s:e].replace("\n", " ")
    print(f"  ...{ctx}...\n")
