#!/usr/bin/env python3
"""duckip_farm.py — 走 duckip 住宅隧道批量注册（每条连接自动换出口 IP）

用法: .venv/bin/python -u duckip_farm.py [目标] [线程]
前置: duckip_relay.py 在跑（127.0.0.1:18080 → mihomo → proxy.duckip.net:1000 SOCKS5）
"""
import os, sys, json, time, random, string, threading, queue, re
os.environ.setdefault("TOR_AUTOSTART", "0")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import requests
import tor_parallel_farm as t
import th_auto_register as tar

TARGET  = int(sys.argv[1]) if len(sys.argv) > 1 else 20
THREADS = int(sys.argv[2]) if len(sys.argv) > 2 else 8
MAX_TRY = int(sys.argv[3]) if len(sys.argv) > 3 else TARGET * 30

PROXY = "socks5h://lwj62a9yiu9v:DBSQJIalyBhvMh55@127.0.0.1:18080"
PX = {"http": PROXY, "https": PROXY}

STAMP = time.strftime("%Y%m%d-%H%M%S")
TXT_OUT = f"th_keys_duckip_{STAMP}.txt"
JSON_OUT = f"th_keys_duckip_{STAMP}.jsonl"

lock = threading.Lock()
done = []
attempts = iter(range(1, 10**9))
stop = threading.Event()
jobs = queue.Queue()
pending = [0]  # 在途验证作业数


def registrar(wid):
    while not stop.is_set():
        with lock:
            if len(done) + pending[0] >= TARGET: stop.set(); return
            n = next(attempts)
            if n > MAX_TRY: stop.set(); return
        try:
            email, etok = tar._cfmail_create()
            pwd = ''.join(random.choices(string.ascii_letters + string.digits, k=12)) + '!Aa1'
            s, ok, errs, cap, txt = t.signup_tor(PX, email, pwd)
            if not ok:
                tag = "captcha" if cap else (errs[0][:44] if errs else "?")
                print(f"  [r{wid} #{n}] ✗ {tag}", flush=True)
                continue
            uid = re.findall(r'"userId":\s*"([^"]+)"', txt)
            print(f"  [r{wid} #{n}] ✓ SIGNUP OK → 验证线程", flush=True)
            with lock:
                pending[0] += 1
            jobs.put((s, {"email": email, "password": pwd, "userId": uid[0] if uid else ""}, etok, wid, n))
        except Exception as e:
            print(f"  [r{wid} #{n}] EXC {type(e).__name__}: {str(e)[:50]}", flush=True)


def verifier(vid):
    while True:
        with lock:
            if pending[0] == 0 and stop.is_set():
                return
        try:
            job = jobs.get(timeout=1)
        except queue.Empty:
            if stop.is_set() and pending[0] == 0:
                return
            continue
        s, acct, etok, wid, n = job
        try:
            # 建 key：3 次退避重试（修 45% 流失的主因）
            key = None
            for attempt in range(3):
                try:
                    r3 = s.post(f"{tar.BASE}/api/keys", json={"label": f"th-rp-{random.randint(100,999)}"},
                                headers={"Accept":"application/json","Content-Type":"application/json"},
                                proxies=PX, timeout=40)
                    if r3.status_code == 201:
                        key = r3.json().get("plaintext"); break
                    print(f"  [v{vid} #{n}] key {r3.status_code} (try {attempt+1}/3)", flush=True)
                except Exception as e:
                    print(f"  [v{vid} #{n}] key EXC {type(e).__name__} (try {attempt+1}/3)", flush=True)
                time.sleep(2 + attempt * 2)
            if not key:
                print(f"  [v{vid} #{n}] 放弃（建 key 失败）", flush=True)
                continue
            acct["api_key"] = key
            try:
                rc = s.post(f"{tar.BASE}/api/me/privacy", json={"free_models_enabled": True}, proxies=PX, timeout=30)
                acct["consent"] = rc.status_code == 200
            except Exception:
                acct["consent"] = False
            acct["verified"] = tar.verify_email(etok)
            okc, info = tar.test_model(key)
            acct["test"] = info
            with lock:
                with open(JSON_OUT, "a") as f2: f2.write(json.dumps(acct, ensure_ascii=False) + "\n")
                if acct.get("verified") and info == "200":
                    done.append(acct)
                    with open(TXT_OUT, "a") as f2:
                        f2.write(f"{acct['email']}\t{acct['password']}\t{acct['api_key']}\tverified=True\ttest=200\n")
                    print(f"  ★ 有效账号 {len(done)}/{TARGET}: {acct['email']}", flush=True)
                else:
                    print(f"  [v{vid} #{n}] 未通过 verified={acct.get('verified')} test={info}", flush=True)
        except Exception as e:
            print(f"  [v{vid} #{n}] EXC {type(e).__name__}: {str(e)[:60]}", flush=True)
        finally:
            with lock:
                pending[0] -= 1


def main():
    print(f"=== duckip 住宅农场 | 目标 {TARGET} | 注册线程 {THREADS} | 验证线程 4 | 上限 {MAX_TRY} ===", flush=True)
    print(f"    导出 → {TXT_OUT} / {JSON_OUT}", flush=True)
    regs = [threading.Thread(target=registrar, args=(i+1,), daemon=True) for i in range(THREADS)]
    vers = [threading.Thread(target=verifier, args=(i+1,), daemon=True) for i in range(4)]
    for x in regs + vers: x.start()
    for x in regs: x.join()
    stop.set()
    while True:
        with lock:
            if pending[0] == 0: break
        time.sleep(2)
    time.sleep(1)
    print(f"\n=== DONE: {len(done)}/{TARGET} 有效 ===", flush=True)
    print(f"  有效 key → {TXT_OUT}", flush=True)


if __name__ == "__main__":
    main()
