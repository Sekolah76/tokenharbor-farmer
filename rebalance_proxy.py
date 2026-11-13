import sys, time, collections
sys.path.insert(0,"/Users/zxc/tokenharbor-farmer")
from ss2a_fast import api, pmap
TARGET=set()
for f in ['th_keys_1024v4_nt_20261113-235542.txt','th_keys_1024v6_nt_20261114-004651.txt','th_keys_1024v6_nt_20261114-005733.txt']:
    for line in open(f):
        p=line.rstrip('\n').split('\t')
        if len(p)>=3: TARGET.add(p[0])
def fetch_all():
    for _ in range(10):
        st,js=api("GET","/api/v1/accounts",params={"search":"tokenhour-","page":1,"page_size":900},timeout=120)
        d=(js or {}).get('data') or {}; items=d.get('items') or []
        if isinstance(d.get('total'),int) and len(items)>=d['total']: return items
        time.sleep(1.5)
    return items
def put_retry(aid,body,tries=5):
    for i in range(tries):
        st,js=api("PUT",f"/api/v1/accounts/{aid}",body=body)
        if st==200: return True
        time.sleep(0.7*(i+1))
    return False
items=fetch_all()
mine=sorted([a for a in items if (a.get('extra') or {}).get('email') in TARGET], key=lambda x:x['id'])
st,js=api("GET","/api/v1/proxies",params={"page":1,"page_size":500},timeout=90)
priv=[p for p in ((js.get('data') or {}).get('items') or []) if not p.get('is_public')]
healthy=sorted([p for p in priv if p.get('latency_status')!='failed' and (p.get('status') or 'active')=='active'], key=lambda p:p['id'])
print(f"账号 {len(mine)}，健康私有代理 {len(healthy)}")
# 完美均匀：i % N 轮转 → 前 r 个各多 1 个
assign={a['id']: healthy[i % len(healthy)]['id'] for i,a in enumerate(mine)}
tgt=collections.Counter(assign.values())
print("目标分布:",collections.Counter(tgt.values()))
todo=[a for a in mine if a.get('proxy_id')!=assign[a['id']]]
print(f"需要改 {len(todo)} 个")
res=pmap(lambda a: put_retry(a['id'],{"proxy_id":assign[a['id']]}), todo, workers=12,
         progress=lambda d,t: print(f"  {d}/{t}",flush=True) if d%50==0 else None)
print(f"改成功 {sum(1 for x in res if x is True)}/{len(todo)}")
