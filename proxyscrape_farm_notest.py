#!/usr/bin/env python3
"""proxyscrape_farm_notest.py — 走 ProxyScrape 机房代理批量注册 TokenHarbor，跳过 key 测通。

用法:
  source cf-mail/env.sh
  .venv/bin/python -u proxyscrape_farm_notest.py [目标] [线程] [最大尝试]

设计:
  · 每个账号一个 ProxyScrape HTTP 代理（100 条池，单条一个出口 IP）
  · 邮箱只用 YYDS（edu.jstyo.com / 204901.xyz），不碰 Cloudflare
  · 注册/收验证信走代理；建 key、开免费模型切干净出口（Cloudflare 对机房 IP 的 /api/keys 会弹 challenge）
  · 不调用 tar.test_model，落盘 test=untested
"""
import os, sys, json, time, random, string, threading, queue, re, urllib.parse
os.environ.setdefault("TOR_AUTOSTART", "0")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import requests
import th_auto_register as tar
import sweep_nodes as sw

TARGET = int(sys.argv[1]) if len(sys.argv) > 1 else 20
THREADS = int(sys.argv[2]) if len(sys.argv) > 2 else 8
MAX_TRY = int(sys.argv[3]) if len(sys.argv) > 3 else 60

API_TOKEN = "ZfI59Xn6RcodgvKN2OElE5151IhiKEBoaedN7NwcygcjbCyXtMWahKbErnkUbSb8"
SUB = "8034a947-d31b-4d9e-8c1e-6e8e230307d2"
API = "https://api.proxyscrape.com"
STAMP = time.strftime("%Y%m%d-%H%M%S")
TXT_OUT = f"th_keys_proxyscrape_nt_{STAMP}.txt"
JSON_OUT = f"th_keys_proxyscrape_nt_{STAMP}.jsonl"

lock = threading.Lock()
done = []
attempts = iter(range(1, 10**9))
stop = threading.Event()
pending = [0]
proxy_q = queue.Queue()
email_q = queue.Queue()

CONSENT_TEXT = ("DeepSeek V4 Flash, MiMo V2.6 Flash, MiMo V2.5, DeepSeek V4.1 Flash and TH-Rudder are "
                "currently available through explicit free routes. Token Harbor may keep the requests and "
                "responses you send through free models and use them for service diagnostics, safety, "
                "optimization, and to evaluate and improve models and products. Your consent also covers "
                "free models we add later. Our staff may read free-route conversations.")


def load_proxies():
    h = {"api-token": API_TOKEN, "Accept": "application/json"}
    ov = requests.get(f"{API}/v4/account/{SUB}/datacenter_shared/overview", headers=h, timeout=30).json()["data"]
    svc = ov["services"]["datacenter_shared"]
    user, pw = svc["proxy_username"], svc["proxy_password"]
    pl = requests.get(f"{API}/v4/account/{SUB}/datacenter_shared/proxy-list?protocol=http&format=json",
                      headers=h, timeout=30).json()["data"]
    out = []
    for row in pl:
        host = row[0]
        out.append({"host": host, "url": f"http://{user}:{pw}@{host}", "country": row[3] if len(row) > 3 else "?"})
    random.shuffle(out)
    return out


def load_emails():
    data = json.load(open("/tmp/yyds_batch_emails.json", encoding="utf-8"))
    random.shuffle(data)
    for x in data:
        email_q.put(x)
    return len(data)


def enable_free_models(s):
    body = {"free_models_enabled": True, "shown_text": CONSENT_TEXT,
            "screen": "consent_modal", "path": "/dashboard"}
    try:
        r = s.post(f"{tar.BASE}/api/me/privacy", json=body,
                   headers={"Accept": "application/json", "Content-Type": "application/json"}, timeout=30)
        if r.status_code == 200:
            return True, r.status_code, r.text[:80]
        # 兼容老 payload
        r2 = s.post(f"{tar.BASE}/api/me/privacy", json={"free_models_enabled": True},
                    headers={"Accept": "application/json", "Content-Type": "application/json"}, timeout=30)
        return r2.status_code == 200, r2.status_code, r2.text[:120]
    except Exception as e:
        return False, 0, f"{type(e).__name__}: {str(e)[:80]}"


def registrar(wid):
    while not stop.is_set():
        with lock:
            if len(done) + pending[0] >= TARGET:
                stop.set(); return
            n = next(attempts)
            if n > MAX_TRY:
                stop.set(); return
        try:
            p = proxy_q.get(timeout=2)
        except queue.Empty:
            stop.set(); return
        try:
            e = email_q.get(timeout=2)
        except queue.Empty:
            stop.set(); return

        email, pwd = e["email"], ''.join(random.choices(string.ascii_letters + string.digits, k=12)) + '!Aa1'
        s = requests.Session()
        s.headers.update({"User-Agent": tar.UA})
        s.proxies = {"http": p["url"], "https": p["url"]}
        try:
            s.get(f"{tar.BASE}/login", timeout=35)
            ok, r, errs, cap = sw.signup(s, email, pwd, sw.tz_for("🇺🇸"))
        except Exception as ex:
            print(f"  [r{wid} #{n}] proxy={p['host']} EXC {type(ex).__name__}: {str(ex)[:60]}", flush=True)
            continue
        if not ok:
            tag = "captcha" if cap else (errs[0][:60] if errs else "?")
            print(f"  [r{wid} #{n}] proxy={p['host']} ✗ {tag}", flush=True)
            continue

        uid = re.findall(r'"userId":\s*"([^"]+)"', r.text)
        acct = {"email": email, "password": pwd, "userId": uid[0] if uid else "",
                "proxy": p["host"], "proxy_country": p["country"]}
        print(f"  [r{wid} #{n}] ✓ SIGNUP {email} via {p['host']} → 验证/建key", flush=True)

        # 换到干净出口，避免 Cloudflare 对 /api/keys 的 challenge
        s.proxies = {}
        verified = tar.verify_email(e["token"], max_wait=180)
        acct["verified"] = verified
        if not verified:
            print(f"  [r{wid} #{n}] ✗ 邮箱未验证 {email}", flush=True)
            continue

        key = None
        for ktry in range(3):
            try:
                r3 = s.post(f"{tar.BASE}/api/keys", json={"label": f"ps-{random.randint(100,999)}"},
                            headers={"Accept": "application/json", "Content-Type": "application/json"}, timeout=40)
                if r3.status_code == 201:
                    key = r3.json().get("plaintext"); break
                print(f"  [r{wid} #{n}] key try{ktry+1} {r3.status_code} {r3.text[:80]}", flush=True)
            except Exception as ex:
                print(f"  [r{wid} #{n}] key EXC {type(ex).__name__}", flush=True)
            time.sleep(2)
        if not key:
            print(f"  [r{wid} #{n}] ✗ 建key失败 {email}", flush=True)
            continue
        acct["api_key"] = key

        consent, cstatus, ctext = enable_free_models(s)
        acct["consent"] = consent
        acct["test"] = "untested"
        acct["created_at"] = time.strftime("%Y%m%d-%H%M%S")
        print(f"  [r{wid} #{n}] ★ 有效账号 {email} key=OK consent={cstatus}", flush=True)
        with lock:
            done.append(acct)
            with open(TXT_OUT, "a", encoding="utf-8") as f:
                f.write(f"{email}\t{pwd}\t{key}\tverified=True\ttest=untested\n")
            with open(JSON_OUT, "a", encoding="utf-8") as f:
                f.write(json.dumps(acct, ensure_ascii=False) + "\n")


def main():
    proxies = load_proxies()
    n_emails = load_emails()
    for p in proxies:
        proxy_q.put(p)
    print(f"=== ProxyScrape farm | 目标 {TARGET} | 线程 {THREADS} | 代理 {len(proxies)} | 邮箱 {n_emails} | 尝试上限 {MAX_TRY} ===", flush=True)
    threads = [threading.Thread(target=registrar, args=(i + 1,), daemon=True) for i in range(THREADS)]
    for t in threads: t.start()
    for t in threads: t.join()
    print(f"\n=== DONE: {len(done)}/{TARGET} ===", flush=True)
    print(f"TXT: {TXT_OUT}\nJSON: {JSON_OUT}", flush=True)


if __name__ == "__main__":
    main()
