#!/usr/bin/env python3
"""fix_bind.py — 补绑分组 + 均匀绑定自己名下私有代理（带重试、低并发）"""
import sys, time, json, collections
sys.path.insert(0, "/Users/zxc/tokenharbor-farmer")
from ss2a_fast import api, pmap

GROUP_ID = 4687
TARGET_EMAILS = set()
for f in ['th_keys_1024v4_nt_20261113-235542.txt','th_keys_1024v6_nt_20261114-004651.txt','th_keys_1024v6_nt_20261114-005733.txt']:
    for line in open(f):
        p = line.rstrip('\n').split('\t')
        if len(p) >= 3: TARGET_EMAILS.add(p[0])

def fetch_all():
    for _ in range(8):
        st, js = api("GET", "/api/v1/accounts", params={"search":"tokenhour-","page":1,"page_size":900}, timeout=120)
        d = (js or {}).get("data") or {}
        items = d.get("items") or []
        if isinstance(d.get("total"), int) and len(items) >= d["total"]:
            return items
        time.sleep(1.5)
    return items

def put_retry(aid, body, tries=4):
    for i in range(tries):
        st, js = api("PUT", f"/api/v1/accounts/{aid}", body=body)
        if st == 200: return True
        time.sleep(0.6 * (i + 1))
    return False

items = fetch_all()
mine = [a for a in items if (a.get('extra') or {}).get('email') in TARGET_EMAILS]
print(f"本批账号 {len(mine)} 个")

# ---- 1) 补分组 ----
missing = [a for a in mine if GROUP_ID not in (a.get('group_ids') or [])]
print(f"缺分组 {len(missing)} 个 → 8 线程补绑")
res = pmap(lambda a: put_retry(a['id'], {"group_ids":[GROUP_ID], "confirm_mixed_channel_risk": True}),
           missing, workers=8, progress=lambda d,t: print(f"  分组 {d}/{t}", flush=True) if d%25==0 else None)
print(f"  补绑成功 {sum(1 for x in res if x is True)}/{len(missing)}")

# ---- 2) 代理 ----
st, js = api("GET", "/api/v1/proxies", params={"page":1,"page_size":500}, timeout=90)
prox = (js.get('data') or {}).get('items') or []
private = [p for p in prox if not p.get('is_public')]
healthy = [p for p in private if p.get('latency_status') != 'failed' and (p.get('status') or 'active')=='active']
healthy.sort(key=lambda p: p['id'])
print(f"私有代理 {len(private)}，健康 {len(healthy)}")
failed_ids = [p['id'] for p in private if p.get('latency_status')=='failed']
print(f"  跳过 failed 代理 {failed_ids}")

# 均匀分配：按 id 排序轮转（每个代理 2-3 个）
assign = {a['id']: healthy[i % len(healthy)]['id'] for i, a in enumerate(sorted(mine, key=lambda x: x['id']))}
dist = collections.Counter(assign.values())
print(f"  分配方案：{len(dist)} 个代理，每个 {min(dist.values())}-{max(dist.values())} 个账号")

res2 = pmap(lambda a: put_retry(a['id'], {"proxy_id": assign[a['id']]}),
            mine, workers=12, progress=lambda d,t: print(f"  代理 {d}/{t}", flush=True) if d%50==0 else None)
print(f"  绑定成功 {sum(1 for x in res2 if x is True)}/{len(mine)}")
