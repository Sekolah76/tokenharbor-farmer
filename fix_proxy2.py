import sys, time, collections
sys.path.insert(0,"/Users/zxc/tokenharbor-farmer")
from ss2a_fast import api, pmap
GROUP_ID=4687
TARGET=set()
for f in ['th_keys_1024v4_nt_20261113-235542.txt','th_keys_1024v6_nt_20261114-004651.txt','th_keys_1024v6_nt_20261114-005733.txt']:
    for line in open(f):
        p=line.rstrip('\n').split('\t')
        if len(p)>=3: TARGET.add(p[0])
def fetch_all():
    for _ in range(8):
        st,js=api("GET","/api/v1/accounts",params={"search":"tokenhour-","page":1,"page_size":900},timeout=120)
        d=(js or {}).get('data') or {}; items=d.get('items') or []
        if isinstance(d.get('total'),int) and len(items)>=d['total']: return items
        time.sleep(1.5)
    return items
def put_retry(aid,body,tries=6):
    for i in range(tries):
        st,js=api("PUT",f"/api/v1/accounts/{aid}",body=body)
        if st==200: return True
        time.sleep(0.8*(i+1))
    return False
items=fetch_all()
mine=[a for a in items if (a.get('extra') or {}).get('email') in TARGET]
missing=[a for a in mine if not a.get('proxy_id')]
print(f"本批 {len(mine)}，缺代理 {len(missing)}")
if missing:
    st,js=api("GET","/api/v1/proxies",params={"page":1,"page_size":500},timeout=90)
    priv=[p for p in ((js.get('data') or {}).get('items') or []) if not p.get('is_public')]
    healthy=sorted([p for p in priv if p.get('latency_status')!='failed' and (p.get('status') or 'active')=='active'],key=lambda p:p['id'])
    cnt=collections.Counter(a['proxy_id'] for a in mine if a.get('proxy_id'))
    # 挑当前承载最少的代理均匀补
    seq=[healthy[i%len(healthy)]['id'] for i in range(len(missing))]
    seq.sort(key=lambda pid: cnt.get(pid,0))
    res=pmap(lambda pair: put_retry(pair[0]['id'],{"proxy_id":pair[1]}),list(zip(missing,seq)),workers=6)
    print(f"补绑成功 {sum(1 for x in res if x is True)}/{len(missing)}")
