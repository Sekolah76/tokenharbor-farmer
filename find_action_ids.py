#!/usr/bin/env python3
"""find_action_ids.py — 从 tokenharbor.ai 最新前端提取 Next.js Server Action ID"""
import requests, re, sys, json

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"
BASE = "https://tokenharbor.ai"

def fetch(url):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    return r.text

html = fetch(f"{BASE}/login")

# 1) HTML/flight data 中的 40+ 位 hex（action id 形态）
ids = re.findall(r'"([0-9a-f]{40,42})"', html)
uniq = sorted(set(ids))
print("== hex ids in HTML ==")
for i in uniq:
    print(" ", i)

# 2) 找 JS chunks
chunks = sorted(set(re.findall(r'(/_next/static/chunks/[^"\s]+?\.js)', html)))
print(f"\n== {len(chunks)} chunks, scan for action ids ==")
found = {}
for c in chunks:
    try:
        js = fetch(f"{BASE}{c}")
    except Exception as e:
        print(f"  SKIP {c}: {str(e)[:40]}")
        continue
    # Next.js 客户端 action 注册形态: createServerReference("<id>") 或 (0,...)("<id>")
    m = re.findall(r'createServerReference\("([0-9a-f]{40,42})"', js)
    m2 = re.findall(r'"([0-9a-f]{40,42})"', js)
    if m or m2:
        allm = sorted(set(m)) or sorted(set(m2))
        found[c] = allm
        print(f"  {c.split('/')[-1]}: {allm}")

# 3) 找包含 signup/register 关键词的 chunk
print("\n== chunks containing signup/keywords ==")
for c in chunks:
    try:
        js = fetch(f"{BASE}{c}")
    except Exception:
        continue
    if any(k in js.lower() for k in ("signup", "invite_code", "device_fingerprint", "register")):
        hits = sorted(set(re.findall(r'"([0-9a-f]{40,42})"', js)))
        print(f"  {c.split('/')[-1]}: ids={hits} keywords=yes")
        # 找出上下文
        for kw in ("signup", "device_fingerprint"):
            for mm in re.finditer(kw, js, re.I):
                s = max(0, mm.start() - 150); e = min(len(js), mm.end() + 150)
                print(f"    ctx[{kw}]: ...{js[s:e]}...")
                break
