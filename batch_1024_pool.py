#!/usr/bin/env python3
import os,sys,json,time,random,string,threading,queue
os.environ.setdefault('TOR_AUTOSTART','0')
sys.path.insert(0,'.')
import th_auto_register as tar, proxyscrape_browser_farm as pf
TARGET=int(sys.argv[1]) if len(sys.argv)>1 else 20
THREADS=int(sys.argv[2]) if len(sys.argv)>2 else 4
PORTS=list(range(18146,18186))
STAMP=time.strftime('%Y%m%d-%H%M%S'); TXT=f'th_keys_1024_nt_{STAMP}.txt'; JSONF=f'th_keys_1024_nt_{STAMP}.jsonl'
lock=threading.Lock(); KEY_LOCK=threading.Lock(); done=[]; stop=threading.Event(); pend=[0]
emails=json.load(open('/tmp/yyds_browser_emails.json')); random.shuffle(emails)
q=queue.Queue(); [q.put((p,emails[i])) for i,p in enumerate(PORTS)]
def worker(w):
 while not stop.is_set():
  with lock:
   if len(done)+pend[0]>=TARGET: stop.set(); return
  try: port,e=q.get(timeout=2)
  except queue.Empty: stop.set(); return
  pwd='Px'+''.join(random.choices(string.digits,k=8))+'!Qz'
  try: ok,url,info=pf.browser_signup({'host':f'127.0.0.1:{port}','user':'','pass':''},e['email'],pwd)
  except Exception as ex: print(f'[w{w}] {port} EXC {type(ex).__name__}',flush=True); continue
  if not ok: print(f'[w{w}] {port} ✗ {e["email"]} {str(info)[:60]}',flush=True); continue
  print(f'[w{w}] {port} ✓ SIGNUP {e["email"]}',flush=True)
  with lock: pend[0]+=1
  try:
   v=tar.verify_email(e['token'],max_wait=180)
   if not v: print(f'[w{w}] ✗ verify',flush=True); continue
   key=consent=None; kinfo=''
   for _ in range(3):
    with KEY_LOCK:
     key,consent,kinfo=pf.clean_key_and_consent(e['email'],pwd)
    if key: break
    time.sleep(10)
   if not key: print(f'[w{w}] ✗ key {kinfo}',flush=True); continue
   a={'email':e['email'],'password':pwd,'api_key':key,'verified':True,'consent':consent,'test':'untested','proxy':f'1024:{port}'}
   with lock:
    done.append(a); open(TXT,'a').write(f"{e['email']}\t{pwd}\t{key}\tverified=True\ttest=untested\n"); open(JSONF,'a').write(json.dumps(a,ensure_ascii=False)+'\n')
   print(f'[w{w}] ★ OK {e["email"]}',flush=True)
  finally:
   with lock: pend[0]-=1
ths=[threading.Thread(target=worker,args=(i+1,),daemon=True) for i in range(THREADS)]
for t in ths:t.start()
for t in ths:t.join()
print(f'DONE {len(done)}/{TARGET} {TXT}')
