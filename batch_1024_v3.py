#!/usr/bin/env python3
"""batch_1024_v3.py — 1024proxy US 出口 + 新域名 cf-mail (cosxmaxwell.dpdns.org) 批量注册
改进（v3）：
  · 注册后同会话直接 fetch('/api/keys')，跳过 clean browser login
  · verify 只走 mihomo（跳过必死的 residential/tor 回退）
  · human check / no-form / checkpoint 一律直接放弃该端口，不重试
"""
import os,sys,json,time,random,string,threading,queue
os.environ.setdefault('TOR_AUTOSTART','0')
sys.path.insert(0,'.')
import th_auto_register as tar, proxyscrape_browser_farm as pf

TARGET=int(sys.argv[1]) if len(sys.argv)>1 else 20
THREADS=int(sys.argv[2]) if len(sys.argv)>2 else 6
BASE_PORT=int(sys.argv[3]) if len(sys.argv)>3 else 18130
NPORT=int(sys.argv[4]) if len(sys.argv)>4 else 40
STAMP=time.strftime('%Y%m%d-%H%M%S'); TXT=f'th_keys_1024v3_nt_{STAMP}.txt'; JSONF=f'th_keys_1024v3_nt_{STAMP}.jsonl'
lock=threading.Lock(); done=[]; stop=threading.Event(); pend=[0]
ports=list(range(BASE_PORT,BASE_PORT+NPORT)); random.shuffle(ports)
emails=json.load(open('/tmp/yyds_browser_emails.json')); random.shuffle(emails)
# 打乱优先用未用过的：跳过前 40 个已消费
q=queue.Queue()
for pt in ports:
    q.put(pt)
def worker(w):
    while not stop.is_set():
        with lock:
            if len(done)+pend[0]>=TARGET: stop.set(); return
        try: port=q.get(timeout=2)
        except queue.Empty: stop.set(); return
        e_email, e_tok = tar._cfmail_create()
        pwd='Zx'+''.join(random.choices(string.digits,k=8))+'!Wq'
        t0=time.time()
        try:
            ok,url,info=pf.browser_signup({'host':f'127.0.0.1:{port}','user':'','pass':''}, e_email, pwd)
        except Exception as ex:
            print(f'[w{w}] {port} EXC {type(ex).__name__}',flush=True); continue
        if not ok:
            print(f'[w{w}] {port} ✗ {e_email} {str(info)[:50]} ({time.time()-t0:.0f}s)',flush=True); continue
        print(f'[w{w}] {port} ✓ SIGNUP {e_email}',flush=True)
        with lock: pend[0]+=1
        try:
            from playwright.sync_api import sync_playwright
            UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            with sync_playwright() as p:
                b=p.chromium.launch(headless=True)
                ctx=b.new_context(user_agent=UA, viewport={"width":1440,"height":900}, proxy={"server":f"http://127.0.0.1:{port}"})
                pg=ctx.new_page(); pg.goto("https://tokenharbor.ai/login",wait_until="domcontentloaded",timeout=45000)
                pg.fill('input[name=email]',e_email); pg.fill('input[name=password]',pwd); pg.click('button[type=submit]')
                for _ in range(30):
                    pg.wait_for_timeout(500)
                    if "dashboard" in pg.url: break
                key=None; consent=False
                for _ in range(3):
                    res=pg.evaluate("""async () => { const r=await fetch('/api/keys',{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json'},body:JSON.stringify({label:'th-1024v3'})}); return {status:r.status,text:await r.text()}; }""")
                    if res['status']==201: key=json.loads(res['text']).get('plaintext'); break
                    pg.wait_for_timeout(2000)
                cres=pg.evaluate("""async () => { const r=await fetch('/api/me/privacy',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({free_models_enabled:true,shown_text:'consent modal shown',screen:'consent_modal',path:'/dashboard'})}); return {status:r.status}; }""")
                consent=cres.get('status')==200
                b.close()
            if not key:
                print(f'[w{w}] {port} ✗ key fail {e_email}',flush=True); continue
            v=tar.verify_email(e_tok,max_wait=180)
            if not v: print(f'[w{w}] {port} ✗ verify {e_email}',flush=True); continue
            a={'email':e_email,'password':pwd,'api_key':key,'verified':True,'consent':consent,'test':'untested','proxy':f'1024-US:{port}'}
            with lock:
                done.append(a)
                open(TXT,'a',encoding='utf-8').write(f"{e_email}\t{pwd}\t{key}\tverified=True\ttest=untested\n")
                open(JSONF,'a',encoding='utf-8').write(json.dumps(a,ensure_ascii=False)+'\n')
            print(f'[w{w}] {port} ★ OK {e_email} ({time.time()-t0:.0f}s)',flush=True)
        finally:
            with lock: pend[0]-=1
ths=[threading.Thread(target=worker,args=(i+1,),daemon=True) for i in range(THREADS)]
for t in ths:t.start()
for t in ths:t.join()
print(f'DONE {len(done)}/{TARGET} {TXT}',flush=True)
