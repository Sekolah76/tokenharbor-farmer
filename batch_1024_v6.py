#!/usr/bin/env python3
"""batch_1024_v6.py — 共享收信轮询器版（架构级优化，简化实现）

核心改动（相对 v4/v5）：
  1. 【邮件/注册重叠】表单一提交就把邮箱交给轮询器，不等建完 key 才开始等信
  2. 【共享轮询器】所有待验地址共用一个待查表，多个查询线程并发扫全场
  3. 【自适应退避】轮询间隔随账号年龄 1s→1.5s→2s→3s
  4. 【原子任务】轮询+点击+落盘在一个任务内完成，无跨线程队列

用法：batch_1024_v6.py <目标> <signup线程> <poll并发> <起始端口> <端口数>
"""
import os,sys,json,time,random,string,threading,queue
from concurrent.futures import ThreadPoolExecutor
os.environ.setdefault('TOR_AUTOSTART','0')
sys.path.insert(0,'.')
import requests
import th_auto_register as tar
import proxyscrape_browser_farm as pf

TARGET=int(sys.argv[1]) if len(sys.argv)>1 else 20
SIGNUP_THREADS=int(sys.argv[2]) if len(sys.argv)>2 else 6
POLL_WORKERS=int(sys.argv[3]) if len(sys.argv)>3 else 64
BASE_PORT=int(sys.argv[4]) if len(sys.argv)>4 else 18200
NPORT=int(sys.argv[5]) if len(sys.argv)>5 else 40
POLL_MAX_WAIT=int(os.environ.get('POLL_MAX_WAIT','240'))

STAMP=time.strftime('%Y%m%d-%H%M%S')
TXT=f'th_keys_1024v6_nt_{STAMP}.txt'
JSONF=f'th_keys_1024v6_nt_{STAMP}.jsonl'
METRICS=f'th_keys_1024v6_metrics_{STAMP}.json'

T0=time.time()
def ts(): return round(time.time()-T0,2)

lock=threading.RLock()
done=[]; attempts=[0]; inflight_signup=[0]
stop=threading.Event()
port_q=queue.Queue()
for p in range(BASE_PORT,BASE_PORT+NPORT): port_q.put(p)

pendings={}

def interval_for(age):
    if age < 15: return 1.0
    if age < 35: return 1.5
    if age < 60: return 2.0
    return 3.0

_TLS=threading.local()
def _sess():
    s_=getattr(_TLS,'sess',None)
    if s_ is None:
        s_=requests.Session(); s_.headers.update({"X-Mail-Token":tar.CF_MAIL_TOKEN})
        _TLS.sess=s_
    return s_

def poll_one(p):
    try:
        r=_sess().get(f"{tar.CF_MAIL_BASE}/inbox",params={"to":p['email']},timeout=15)
        for m in (r.json().get("messages") or []):
            links=tar._harvest_links((m.get("body") or "")+" "+(m.get("subject") or ""))
            if links: return links[0]
    except Exception:
        pass
    return None

def click_link(link):
    mihomo={"http":"http://127.0.0.1:7890","https":"http://127.0.0.1:7890"}
    for proxies in (mihomo,None):
        try:
            rr=requests.get(link,timeout=25,proxies=proxies,allow_redirects=False)
            if rr.status_code<400 or (300<=rr.status_code<400 and "verify=success" in (rr.headers.get("location") or "")):
                return True
        except Exception:
            pass
    return False

_TLSB=threading.local()
def _browser():
    b=getattr(_TLSB,'browser',None)
    if b is None:
        from playwright.sync_api import sync_playwright
        pw=sync_playwright().start()
        b=pw.chromium.launch(headless=True,args=["--disable-blink-features=AutomationControlled","--no-first-run","--disable-dev-shm-usage"])
        _TLSB.browser=b; _TLSB.pw=pw
    return b

def browser_signup_and_key(proxy_host,email,pwd,on_submit=None):
    b=_browser(); ctx=None
    try:
        ctx=b.new_context(user_agent=pf.UA,viewport={"width":1440,"height":900},proxy={"server":f"http://{proxy_host}"})
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});window.chrome=window.chrome||{runtime:{}};Object.defineProperty(navigator,'languages',{get:()=>['zh-CN','zh','en']});")
        pg=ctx.new_page()
        pg.goto("https://tokenharbor.ai/login?mode=signup",wait_until="domcontentloaded",timeout=60000)
        if "Token Harbor" not in pg.title(): return None,None,"title="+pg.title()[:50]
        for _ in range(30):
            if pg.locator('input[name=email]').count(): break
            pg.wait_for_timeout(300)
        if not pg.locator('input[name=email]').count(): return None,None,"no-form"
        pg.wait_for_timeout(400)
        cb=pg.locator('input[type=checkbox]')
        if cb.count(): cb.first.click(force=True)
        pg.fill('input[name=email]',email); pg.fill('input[name=password]',pwd)
        pg.get_by_role('button',name='Create account').click()
        if on_submit: on_submit()
        ok=False
        for _ in range(40):
            pg.wait_for_timeout(400)
            if "dashboard" in pg.url: ok=True; break
            body=pg.inner_text("body")
            if "Sign out" in body or ("Overview" in body and "API keys" in body): ok=True; break
            for marker in ["We can't create another account","Please complete the human check","Too many sign-ups","not supported"]:
                if marker in body: return None,None,marker
        if not ok: return None,None,pg.inner_text("body")[-160:].replace("\n"," ")
        pg.wait_for_timeout(800)
        key=None
        for _ in range(3):
            res=pg.evaluate("""async () => { const r=await fetch('/api/keys',{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json'},body:JSON.stringify({label:'th-1024v6'})}); return {status:r.status,text:await r.text()}; }""")
            if res['status']==201:
                key=json.loads(res['text']).get('plaintext'); break
            pg.wait_for_timeout(1500)
        if not key: return None,None,f"key-{res['status']}"
        cres=pg.evaluate("""async () => { const r=await fetch('/api/me/privacy',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({free_models_enabled:true,shown_text:'consent modal shown',screen:'consent_modal',path:'/dashboard'})}); return {status:r.status}; }""")
        return key,(cres.get('status')==200), "ok"
    finally:
        try:
            if ctx: ctx.close()
        except Exception: pass

def is_conn_error(info=None, exc=None):
    if exc is not None:
        n=type(exc).__name__
        return n in ("Error","TimeoutError","TargetClosedError","BrowserTypeError") or "net::" in str(exc)
    s=str(info or "")
    return s in ("Error","TimeoutError","TargetClosedError") or s.startswith("net::")

def record(p):
    if p['recorded'] or not (p['has_key'] and p['verified']): return
    p['recorded']=True
    a={'email':p['email'],'password':p['pwd'],'api_key':p['key'],
       'verified':True,'consent':p['consent'],'test':'untested',
       'proxy':f"1024-US:{p['port']}",
       'signup_at':p['signup_at'],'verify_at':p['verify_at']}
    done.append(a)
    with open(TXT,'a',encoding='utf-8') as f:
        f.write(f"{p['email']}\t{p['pwd']}\t{p['key']}\tverified=True\ttest=untested\n")
    with open(JSONF,'a',encoding='utf-8') as f:
        f.write(json.dumps(a,ensure_ascii=False)+'\n')

stats={'polls':0,'found':0,'clicked':0,'click_fail':0}

def watch_task(p):
    link=poll_one(p)
    with lock: stats['polls']+=1
    if link:
        with lock:
            p['mail_found_at']=ts(); stats['found']+=1
        ok=False
        for _ in range(2):
            ok=click_link(link)
            if ok: break
        with lock:
            p['verified']=bool(ok); p['verify_at']=ts(); p['polling']=False
            if ok: stats['clicked']+=1
            else: stats['click_fail']+=1
            record(p)
        print(f'[v] {"OK" if ok else "FAIL"} {p["email"]} mail_at={p["mail_found_at"]}s done_at={ts()}s',flush=True)
    else:
        with lock:
            if not p['verified'] and p['submitted_at']:
                p['polling']=True
                p['next_poll']=time.time()+interval_for(time.time()-p['submitted_at'])

def dispatcher():
    ex=ThreadPoolExecutor(max_workers=POLL_WORKERS)
    futs={}; hb=0
    try:
        while not stop.is_set() or pendings:
            now=time.time()
            with lock:
                due=[p for p in pendings.values()
                     if p['polling'] and p['submitted_at'] and now>=p.get('next_poll',0)]
            for p in due:
                p['polling']=False
                futs[ex.submit(watch_task,p)]=p
            for f in [f for f in list(futs) if f.done()]:
                futs.pop(f)
            if due:
                hb+=1
                if hb%12==0:
                    with lock:
                        pend=len([p for p in pendings.values() if p['polling']]); recd=len(done)
                    print(f'[d] t={ts()}s waiting={pend} inflight={len(futs)} polls={stats["polls"]} hits={stats["found"]} saved={recd}',flush=True)
            time.sleep(0.15)
    finally:
        ex.shutdown(wait=False)

def signup_worker(w):
    while not stop.is_set():
        with lock:
            outstanding=len([p for p in pendings.values() if not p['recorded'] and p['submitted_at']])
            if len(done)+outstanding+inflight_signup[0]>=TARGET: stop.set(); return
            attempts[0]+=1
            if attempts[0]>TARGET*3: stop.set(); return
        email,tok=tar._cfmail_create()
        pwd='Zx'+''.join(random.choices(string.digits,k=8))+'!Wq'
        p={'email':email,'pwd':pwd,'key':None,'port':None,'consent':None,
           'submitted_at':None,'signup_at':None,'has_key':False,'verified':False,
           'polling':False,'recorded':False,'mail_found_at':None,'verify_at':None,
           'next_poll':0}
        with lock:
            pendings[email]=p; inflight_signup[0]+=1
        t0=time.time()
        def on_submit():
            with lock:
                p['submitted_at']=time.time()
                p['polling']=True
                p['next_poll']=time.time()
        try:
            for try_i in range(2):
                try: port=port_q.get(timeout=2)
                except queue.Empty:
                    print(f'[s{w}] PORT-EXHAUSTED {email}',flush=True); break
                try:
                    key,consent,info=browser_signup_and_key(f'127.0.0.1:{port}',email,pwd,on_submit)
                except Exception as ex:
                    key,consent,info=None,None,type(ex).__name__
                if key:
                    with lock:
                        p['key']=key; p['consent']=consent; p['port']=port
                        p['has_key']=True; p['signup_at']=ts()
                        record(p)
                    print(f'[s{w}] {port} SIGNUP+KEY {email} ({time.time()-t0:.0f}s)',flush=True)
                    break
                if is_conn_error(info) and try_i==0:
                    print(f'[s{w}] {port} RETRY-CONN({info}) {email}',flush=True)
                    continue
                print(f'[s{w}] {port} FAIL {email} {str(info)[:50]} ({time.time()-t0:.0f}s)',flush=True)
                with lock:
                    p['polling']=False; p['submitted_at']=None
                break
        finally:
            with lock: inflight_signup[0]-=1

def main():
    print(f'=== v6 | target={TARGET} signup={SIGNUP_THREADS} poll={POLL_WORKERS} ports={NPORT} ===',flush=True)
    d=threading.Thread(target=dispatcher,daemon=True); d.start()
    ss=[threading.Thread(target=signup_worker,args=(i+1,),daemon=True) for i in range(SIGNUP_THREADS)]
    for t in ss: t.start()
    for t in ss: t.join()
    print(f'[main] signup done t={ts()}s, draining mail...',flush=True)
    deadline=time.time()+POLL_MAX_WAIT
    while time.time()<deadline:
        with lock:
            pending=[p for p in pendings.values() if not p['recorded'] and p['submitted_at']]
        if not pending: break
        time.sleep(1)
    with lock:
        recd=len(done)
    stop.set()
    time.sleep(0.5)
    wall=time.time()-T0
    with open(METRICS,'w',encoding='utf-8') as f:
        json.dump({'target':TARGET,'done':recd,'wall_s':round(wall,1),
                   'rate_per_min':round(recd/wall*60,1) if wall else 0,
                   'polls':stats['polls'],'found':stats['found'],
                   'clicked':stats['clicked'],'click_fail':stats['click_fail']},f,ensure_ascii=False,indent=2)
    print(f'DONE {recd}/{TARGET} {TXT} wall={wall:.0f}s rate={recd/wall*60:.1f}/min',flush=True)
    print(f'metrics={METRICS}',flush=True)

if __name__=='__main__':
    main()
