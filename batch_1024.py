#!/usr/bin/env python3
import os,sys,json,time,random,string,threading,queue
os.environ.setdefault('TOR_AUTOSTART','0')
sys.path.insert(0,'.')
import th_auto_register as tar, proxyscrape_browser_farm as pf
TARGET=int(sys.argv[1]) if len(sys.argv)>1 else 20
THREADS=int(sys.argv[2]) if len(sys.argv)>2 else 3
MAX_TRY=int(sys.argv[3]) if len(sys.argv)>3 else 60
STAMP=time.strftime('%Y%m%d-%H%M%S')
TXT=f'th_keys_1024_nt_{STAMP}.txt'; JSONF=f'th_keys_1024_nt_{STAMP}.jsonl'
lock=threading.Lock(); done=[]; attempts=iter(range(1,10**9)); stop=threading.Event(); pend=[0]
emails=json.load(open('/tmp/yyds_browser_emails.json')); random.shuffle(emails)
q=queue.Queue(); [q.put(e) for e in emails]
def worker(w):
  while not stop.is_set():
    with lock:
      if len(done)+pend[0]>=TARGET or next(attempts,10**9)>MAX_TRY: stop.set(); return
      n=attempts.__next__() if False else None
    # use a global counter separately
    try: e=q.get(timeout=2)
    except queue.Empty: stop.set(); return
    pwd='Qw'+''.join(random.choices(string.digits,k=8))+'!Zx'
    try: ok,url,info=pf.browser_signup({'host':'127.0.0.1:18091','user':'','pass':''},e['email'],pwd)
    except Exception as ex: print(f'[w{w}] EXC {type(ex).__name__}',flush=True); continue
    if not ok: print(f'[w{w}] ✗ {e["email"]} {str(info)[:60]}',flush=True); continue
    print(f'[w{w}] ✓ SIGNUP {e["email"]}',flush=True)
    with lock: pend[0]+=1
    try:
      v=tar.verify_email(e['token'],max_wait=180)
      if not v: print(f'[w{w}] ✗ verify',flush=True); continue
      key=consent=None
      for _ in range(3):
        key,consent,info=pf.clean_key_and_consent(e['email'],pwd)
        if key: break
        time.sleep(10)
      if not key: print(f'[w{w}] ✗ key {info}',flush=True); continue
      a={'email':e['email'],'password':pwd,'api_key':key,'verified':True,'consent':consent,'test':'untested'}
      with lock:
        done.append(a)
        open(TXT,'a').write(f"{e['email']}\t{pwd}\t{key}\tverified=True\ttest=untested\n")
        open(JSONF,'a').write(json.dumps(a,ensure_ascii=False)+'\n')
      print(f'[w{w}] ★ OK {e["email"]}',flush=True)
    finally:
      with lock: pend[0]-=1
threads=[threading.Thread(target=worker,args=(i+1,),daemon=True) for i in range(THREADS)]
for t in threads:t.start()
for t in threads:t.join()
print(f'DONE {len(done)}/{TARGET} {TXT}')
