#!/usr/bin/env python3
"""fix_new110.py — 修正本批 110 个新账号：续号命名 + 绑分组 + 绑代理 + 复核。

起因：首次 GET /api/v1/accounts 返回空（接口偶发），序号从 1 重编，与历史 tokenhour-1..110 撞名。
本脚本用 runs/new110_ids.json（按 email 精确定位的 id 列表）逐个改名到 384 起。

用法: python3 fix_new110.py [--start 384] [--dry-run]
"""
import argparse, json, sys, time, collections
sys.path.insert(0, "/Users/zxc/tokenharbor-farmer")
from ss2a_client import api

GROUP_ID = 4687
BASE_URL = "https://tokenharbor.ai"
MODEL_MAPPING = {"deepseek-v4.1-flash": "deepseek-v4.1-flash:free",
                 "mimo-v2.6-flash": "mimo-v2.6-flash:free"}
TEMP_RULES = [{"error_code": 429, "keywords": ["free_tier_limit_reached"],
               "duration_minutes": 10080, "scope": "account",
               "description": "免费额度用尽(滚动7天) → 停7天"}]
BAD_CC = {"CN", "HK", "MO"}

ap = argparse.ArgumentParser()
ap.add_argument("--start", type=int, default=384)
ap.add_argument("--dry-run", action="store_true")
A = ap.parse_args()

doc = json.load(open("runs/import_duckip_20261018-213644.json"))
src = doc["data"]["accounts"]
by_email_key = {}
for a in src:
    by_email_key[(a["extra"] or {}).get("email")] = a

m = json.load(open("runs/new110_ids.json"))
targets = m["map"]
# 按导入时的原始序号（tokenhour-1..110）排，保证新序号顺序与原批次一致
import re as _re
def _n(x):
    mm = _re.match(r"tokenhour-(\d+)", x.get("name") or "")
    return int(mm.group(1)) if mm else 0
targets.sort(key=_n)
print(f"待修正账号 {len(targets)} 个，起始序号 {A.start}")

# 现有代理占用
st, js = api("GET", "/api/v1/proxies", params={"page": 1, "page_size": 300})
d = js.get("data") or {}
plist = d.get("items") or d.get("proxies") or []
st2, js2 = api("GET", "/api/v1/accounts", params={"search": "tokenhour-", "page": 1, "page_size": 600})
cur = (js2.get("data") or {}).get("items") or []
used = collections.Counter(c["proxy_id"] for c in cur if c.get("proxy_id"))
ok = [p for p in plist if (p.get("country_code") or "").upper() not in BAD_CC
      and (p.get("status") or "active") == "active"]
ok.sort(key=lambda p: ((p.get("latency_ms") or 99999),
                       used.get(p["id"], 0) + (p.get("account_count") or 0)))
print(f"可用代理 {len(ok)} 个")
if A.dry_run:
    for i, t in enumerate(targets[:3]):
        print(f"  {t['name']} → tokenhour-{A.start+i}|{t['email']}  proxy={ok[i%len(ok)]['id']}")
    sys.exit(0)

gok = gf = pok = pf = rok = rf = 0
for i, t in enumerate(targets):
    seq = A.start + i
    src_acc = by_email_key.get(t["email"])
    newname = f"tokenhour-{seq}|{t['email']}"
    # 整存 credentials（PUT 是替换语义，必须写全）
    body = {
        "name": newname,
        "notes": f"TokenHarbor | 分组: tokenhour | 节点: duckip",
        "platform": "openai",
        "type": "apikey",
        "credentials": {
            "api_key": src_acc["credentials"]["api_key"],
            "base_url": BASE_URL,
            "model_mapping": MODEL_MAPPING,
            "temp_unschedulable_enabled": True,
            "temp_unschedulable_rules": TEMP_RULES,
        },
        "extra": src_acc["extra"],
        "concurrency": 1,
        "priority": 50,
        "rate_multiplier": 1,
        "auto_pause_on_expired": True,
        "group_ids": [GROUP_ID],
        "confirm_mixed_channel_risk": True,
        "proxy_id": ok[i % len(ok)]["id"],
    }
    try:
        s, r = api("PUT", f"/api/v1/accounts/{t['id']}", body=body)
        if s == 200:
            rok += 1
        else:
            rf += 1
            print(f"  ✗ {newname}: HTTP {s} {str(r)[:100]}")
    except Exception as e:
        rf += 1
        print(f"  ✗ {newname}: {str(e)[:80]}")
    if (i + 1) % 20 == 0:
        print(f"  ... {i+1}/{len(targets)} 已改名 {rok} 失败 {rf}", flush=True)
        time.sleep(1)

print(f"\n改名+绑组+绑代理: 成功 {rok} / 失败 {rf}")

# 复核
time.sleep(2)
st3, js3 = api("GET", "/api/v1/accounts", params={"search": "tokenhour-", "page": 1, "page_size": 600})
final = (js3.get("data") or {}).get("items") or []
ids = {t["id"] for t in targets}
mine = [a for a in final if a["id"] in ids]
print(f"复核 {len(mine)}/{len(targets)} 个")
bad = []
for a in mine:
    probs = []
    if GROUP_ID not in (a.get("group_ids") or []):
        probs.append(f"groups={a.get('group_ids')}")
    if not a.get("proxy_id"):
        probs.append("无代理")
    if not (a.get("credentials_status") or {}).get("has_api_key"):
        probs.append("无key")
    if a.get("concurrency") != 1:
        probs.append(f"conc={a.get('concurrency')}")
    if a.get("priority") != 50:
        probs.append(f"pri={a.get('priority')}")
    if not ((a.get("credentials") or {}).get("model_mapping")):
        probs.append("无映射")
    if probs:
        bad.append((a.get("name"), probs))
print(f"全部合规: {len(mine)-len(bad)}/{len(mine)}")
for n, p in bad[:15]:
    print("  ⚠", n, p)
nm = [a["name"] for a in mine]
print("序号范围:", min(nm)[:20], "→", max(nm)[:20])
