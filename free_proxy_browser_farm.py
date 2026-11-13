#!/usr/bin/env python3
"""free_proxy_browser_farm.py — 用 ProxyScrape 公共代理列表 + 真浏览器补号（跳过 key 测通）。

说明：付费 datacenter 池被 tokenharbor Cloudflare 整段拦截后，用 ProxyScrape 公共列表里
可用的住宅/ISP 出口做补充。每个代理只注册一次，建 key 走干净出口。
"""
import os, sys, json, time, random, string, threading, queue
os.environ.setdefault("TOR_AUTOSTART", "0")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import th_auto_register as tar
import proxyscrape_browser_farm as pf

TARGET = int(sys.argv[1]) if len(sys.argv) > 1 else 14
THREADS = int(sys.argv[2]) if len(sys.argv) > 2 else 5
MAX_TRY = int(sys.argv[3]) if len(sys.argv) > 3 else 200
STAMP = time.strftime("%Y%m%d-%H%M%S")
TXT_OUT = f"th_keys_freeproxy_browser_nt_{STAMP}.txt"
JSON_OUT = f"th_keys_freeproxy_browser_nt_{STAMP}.jsonl"
PARTIAL = "free_proxy_partial_signups.jsonl"
lock = threading.Lock()
done = []
attempts = iter(range(1, 10**9))
stop = threading.Event()
pending = [0]
proxy_q = queue.Queue()
email_q = queue.Queue()


def load_proxies():
    # page_ok 是第一轮 884 个公共代理中能打开 tokenharbor 页面的 258 个
    out = [x.strip() for x in open('/tmp/free_proxy_page_ok.txt') if x.strip()]
    random.shuffle(out)
    return out


def load_emails():
    data = json.load(open('/tmp/yyds_browser_emails.json', encoding='utf-8'))
    random.shuffle(data)
    for x in data:
        email_q.put(x)


def worker(wid):
    while not stop.is_set():
        with lock:
            if len(done) + pending[0] >= TARGET:
                stop.set(); return
            n = next(attempts)
            if n > MAX_TRY:
                stop.set(); return
        try:
            host = proxy_q.get(timeout=2)
            e = email_q.get(timeout=2)
        except queue.Empty:
            stop.set(); return
        pwd = 'Fr' + ''.join(random.choices(string.digits, k=8)) + '!Xz'
        t0 = time.time()
        try:
            ok, url, info = pf.browser_signup({"host": host, "user": "", "pass": ""}, e["email"], pwd)
        except Exception as ex:
            print(f"  [w{wid} #{n}] {host} EXC {type(ex).__name__}", flush=True)
            continue
        if not ok:
            print(f"  [w{wid} #{n}] {host} ✗ {str(info)[:60]} ({time.time()-t0:.0f}s)", flush=True)
            continue
        print(f"  [w{wid} #{n}] ✓ SIGNUP {e['email']} via {host}", flush=True)
        with lock:
            pending[0] += 1
            with open(PARTIAL, "a", encoding="utf-8") as f:
                f.write(json.dumps({"email": e["email"], "password": pwd, "token": e["token"],
                                    "proxy": host, "ts": time.strftime("%Y%m%d-%H%M%S")},
                                   ensure_ascii=False) + "\n")
        try:
            verified = tar.verify_email(e["token"], max_wait=180)
            if not verified:
                print(f"  [w{wid} #{n}] ✗ 邮箱未验证", flush=True)
                continue
            key = consent = None
            kinfo = ""
            for kt in range(3):
                key, consent, kinfo = pf.clean_key_and_consent(e["email"], pwd)
                if key:
                    break
                time.sleep(12)
            if not key:
                print(f"  [w{wid} #{n}] ✗ 建key失败 {kinfo}", flush=True)
                continue
            acct = {"email": e["email"], "password": pwd, "api_key": key, "verified": True,
                    "consent": consent, "test": "untested", "proxy": host,
                    "created_at": time.strftime("%Y%m%d-%H%M%S")}
            with lock:
                done.append(acct)
                with open(TXT_OUT, "a", encoding="utf-8") as f:
                    f.write(f"{e['email']}\t{pwd}\t{key}\tverified=True\ttest=untested\n")
                with open(JSON_OUT, "a", encoding="utf-8") as f:
                    f.write(json.dumps(acct, ensure_ascii=False) + "\n")
            print(f"  [w{wid} #{n}] ★ 有效账号 {e['email']} {kinfo}", flush=True)
        finally:
            with lock:
                pending[0] -= 1


def main():
    proxies = load_proxies()
    load_emails()
    for p in proxies:
        proxy_q.put(p)
    print(f"=== free proxy browser farm | 目标 {TARGET} | 线程 {THREADS} | 候选 {len(proxies)} ===", flush=True)
    ts = [threading.Thread(target=worker, args=(i + 1,), daemon=True) for i in range(THREADS)]
    for t in ts: t.start()
    for t in ts: t.join()
    print(f"\n=== DONE: {len(done)}/{TARGET} ===", flush=True)
    print(f"TXT: {TXT_OUT}\nJSON: {JSON_OUT}", flush=True)


if __name__ == "__main__":
    main()
