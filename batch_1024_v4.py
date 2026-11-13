#!/usr/bin/env python3
"""batch_1024_v4.py — 1024proxy 住宅 + 新 CF 域名 + 流水线注册
优化：
  · signup 成功后同一个浏览器 page 直接 fetch /api/keys + privacy（不再二次登录）
  · verify 独立线程池，signup 线程不等邮件
  · 1024 连接层失败（Error/net::/TimeoutError）自动换端口重试 1 次
  · CF mail 轮询 6s→2s；verify 只走 mihomo→direct（跳过必死 residential/tor）
"""
import os,sys,json,time,random,string,threading,queue,re
os.environ.setdefault('TOR_AUTOSTART','0')
sys.path.insert(0,'.')
import requests
import th_auto_register as tar
import proxyscrape_browser_farm as pf

TARGET=int(sys.argv[1]) if len(sys.argv)>1 else 20
SIGNUP_THREADS=int(sys.argv[2]) if len(sys.argv)>2 else 6
VERIFY_THREADS=int(sys.argv[3]) if len(sys.argv)>3 else 4
BASE_PORT=int(sys.argv[4]) if len(sys.argv)>4 else 18130
NPORT=int(sys.argv[5]) if len(sys.argv)>5 else 40
STAMP=time.strftime('%Y%m%d-%H%M%S')
TXT=f'th_keys_1024v4_nt_{STAMP}.txt'; JSONF=f'th_keys_1024v4_nt_{STAMP}.jsonl'
lock=threading.Lock()
done=[]; attempts=[0]; pending_signup=[0]; pending_verify=[0]
stop=threading.Event()
port_q=queue.Queue(); verify_q=queue.Queue()
for p in range(BASE_PORT,BASE_PORT+NPORT): port_q.put(p)

def fast_cfmail(addr, max_wait):
    start=time.time()
    headers={"X-Mail-Token":tar.CF_MAIL_TOKEN}
    while time.time()-start < max_wait:
        for params in ({"to":addr,"grep":"verify-email"},{"to":addr}):
            try:
                r=requests.get(f"{tar.CF_MAIL_BASE}/inbox",params=params,headers=headers,timeout=15)
                for m in (r.json().get("messages") or []):
                    links=tar._harvest_links((m.get("body") or "")+" "+(m.get("subject") or ""))
                    if links: return fast_click(links[0],int(time.time()-start))
            except Exception: pass
        time.sleep(2)
    return False

def fast_click(link, waited):
    mihomo={"http":"http://127.0.0.1:7890","https":"http://127.0.0.1:7890"}
    for tag,proxies in (("mihomo",mihomo),("direct",None)):
        try:
            rr=requests.get(link,timeout=25,proxies=proxies,allow_redirects=False)
            if rr.status_code<400 or (300<=rr.status_code<400 and "verify=success" in (rr.headers.get("location") or "")):
                return True
        except Exception: pass
    return False

def patch_verify():
    tar._verify_cfmail=fast_cfmail
    tar._click_verify_link=fast_click

def browser_signup_and_key(proxy_host,email,pwd):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b=p.chromium.launch(headless=True,args=["--disable-blink-features=AutomationControlled","--no-first-run","--disable-dev-shm-usage"])
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
                res=pg.evaluate("""async () => { const r=await fetch('/api/keys',{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json'},body:JSON.stringify({label:'th-1024v4'})}); return {status:r.status,text:await r.text()}; }""")
                if res['status']==201:
                    key=json.loads(res['text']).get('plaintext'); break
                pg.wait_for_timeout(1500)
            if not key: return None,None,f"key-{res['status']}"
            cres=pg.evaluate("""async () => { const r=await fetch('/api/me/privacy',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({free_models_enabled:true,shown_text:'consent modal shown',screen:'consent_modal',path:'/dashboard'})}); return {status:r.status}; }""")
            return key,(cres.get('status')==200), "ok"
        finally:
            try:b.close()
            except Exception:pass

def is_conn_error(info=None, exc=None):
    """1024 chain / Playwright 连接层失败：只有这类才换端口重试。"""
    if exc is not None:
        n=type(exc).__name__
        return n in ("Error", "TimeoutError", "TargetClosedError", "BrowserTypeError") or "net::" in str(exc)
    s=str(info or "")
    return s in ("Error", "TimeoutError", "TargetClosedError") or s.startswith("net::")

def signup_worker(w):
    while not stop.is_set():
        with lock:
            if len(done)+pending_verify[0]+pending_signup[0]>=TARGET: stop.set(); return
            attempts[0]+=1
            if attempts[0]>TARGET*3: stop.set(); return
        email,tok=tar._cfmail_create()
        pwd='Zx'+''.join(random.choices(string.digits,k=8))+'!Wq'
        with lock: pending_signup[0]+=1
        t0=time.time()
        try:
            # 每个账号最多 2 次浏览器尝试：连接失败换一个新端口/SID 再试
            for try_i in range(2):
                try: port=port_q.get(timeout=2)
                except queue.Empty:
                    print(f'[s{w}] ✗ 端口池耗尽 {email}',flush=True); break
                try:
                    key,consent,info=browser_signup_and_key(f'127.0.0.1:{port}',email,pwd)
                except Exception as ex:
                    key,consent,info=None,None,type(ex).__name__
                if key:
                    print(f'[s{w}] {port} ✓ SIGNUP+KEY {email} ({time.time()-t0:.0f}s)',flush=True)
                    with lock: pending_verify[0]+=1
                    verify_q.put((email,pwd,key,tok,port,consent,t0))
                    break
                if is_conn_error(info) and try_i==0:
                    print(f'[s{w}] {port} ↻ 连接失败({info})，换端口重试 {email}',flush=True)
                    continue
                print(f'[s{w}] {port} ✗ {email} {str(info)[:50]} ({time.time()-t0:.0f}s)',flush=True)
                break
        finally:
            with lock: pending_signup[0]-=1

def verify_worker(w):
    while True:
        if stop.is_set() and pending_verify[0]==0: return
        try: job=verify_q.get(timeout=2)
        except queue.Empty:
            if stop.is_set() and pending_verify[0]==0: return
            continue
        email,pwd,key,tok,port,consent,t0=job
        v=False
        try: v=tar.verify_email(tok,max_wait=180)
        except Exception: pass
        with lock:
            pending_verify[0]-=1
            if v:
                a={'email':email,'password':pwd,'api_key':key,'verified':True,'consent':consent,'test':'untested','proxy':f'1024-US:{port}'}
                done.append(a)
                open(TXT,'a',encoding='utf-8').write(f"{email}\t{pwd}\t{key}\tverified=True\ttest=untested\n")
                open(JSONF,'a',encoding='utf-8').write(json.dumps(a,ensure_ascii=False)+'\n')
            print(f'[v{w}] {"★ OK" if v else "✗ verify"} {email} ({time.time()-t0:.0f}s)',flush=True)

def main():
    patch_verify()
    print(f'=== v4 | target={TARGET} signup={SIGNUP_THREADS} verify={VERIFY_THREADS} ports={NPORT} ===',flush=True)
    s=[threading.Thread(target=signup_worker,args=(i+1,),daemon=True) for i in range(SIGNUP_THREADS)]
    v=[threading.Thread(target=verify_worker,args=(i+1,),daemon=True) for i in range(VERIFY_THREADS)]
    for t in s+v:t.start()
    for t in s:t.join()
    # signup结束，等验证队列消化
    for t in v:t.join()
    print(f'DONE {len(done)}/{TARGET} {TXT}',flush=True)

if __name__ == '__main__':
    main()
