#!/usr/bin/env python3
"""proxyscrape_browser_farm.py — 用 ProxyScrape 代理 + 真浏览器批量注册 TokenHarbor。

为什么用浏览器：
  · 2026-11-12 实测：直接 HTTP 提交对 ProxyScrape 机房 IP 一律返回 needCaptcha；
  · 真浏览器（Chromium）+ 勾选 18 岁确认后，ProxyScrape 的 IP 能正常过注册；
  · 注册后建 key 走干净出口（避免 /api/keys 的 Cloudflare challenge）。

用法:
  .venv/bin/python -u proxyscrape_browser_farm.py [目标] [线程] [最大尝试]
"""
import os, sys, json, time, random, string, threading, queue, re
os.environ.setdefault("TOR_AUTOSTART", "0")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import requests
import th_auto_register as tar

TARGET = int(sys.argv[1]) if len(sys.argv) > 1 else 20
THREADS = int(sys.argv[2]) if len(sys.argv) > 2 else 4
MAX_TRY = int(sys.argv[3]) if len(sys.argv) > 3 else 80

API_TOKEN = "ZfI59Xn6RcodgvKN2OElE5151IhiKEBoaedN7NwcygcjbCyXtMWahKbErnkUbSb8"
SUB = "8034a947-d31b-4d9e-8c1e-6e8e230307d2"
API = "https://api.proxyscrape.com"
STAMP = time.strftime("%Y%m%d-%H%M%S")
TXT_OUT = f"th_keys_proxyscrape_browser_nt_{STAMP}.txt"
JSON_OUT = f"th_keys_proxyscrape_browser_nt_{STAMP}.jsonl"

lock = threading.Lock()
done = []
attempts = iter(range(1, 10**9))
stop = threading.Event()
pending = [0]
proxy_q = queue.Queue()
email_q = queue.Queue()

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
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
    out = [{"host": r[0], "user": user, "pass": pw} for r in pl]
    random.shuffle(out)
    return out


def load_emails(path):
    data = json.load(open(path, encoding="utf-8"))
    random.shuffle(data)
    for x in data:
        email_q.put(x)


def browser_signup(proxy, email, pwd):
    """返回 (ok, url, body_tail)。"""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled",
                                                   "--no-first-run", "--disable-dev-shm-usage"])
        try:
            pcfg = {"server": f"http://{proxy['host']}"}
            if proxy.get("user"):
                pcfg["username"] = proxy["user"]
                pcfg["password"] = proxy["pass"]
            ctx = b.new_context(user_agent=UA, viewport={"width": 1440, "height": 900}, proxy=pcfg)
            ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
                                "window.chrome=window.chrome||{runtime:{}};"
                                "Object.defineProperty(navigator,'languages',{get:()=>['zh-CN','zh','en']});")
            pg = ctx.new_page()
            pg.goto("https://tokenharbor.ai/login?mode=signup", wait_until="domcontentloaded", timeout=60000)
            if "Token Harbor" not in pg.title():
                return False, pg.url, "title=" + pg.title()[:60]
            for _ in range(30):
                if pg.locator('input[name=email]').count():
                    break
                pg.wait_for_timeout(400)
            if not pg.locator('input[name=email]').count():
                return False, pg.url, "no-form"
            pg.wait_for_timeout(500)
            # 18岁确认是客户端必填；必须真点击触发 React 状态
            cb = pg.locator('input[type=checkbox]')
            if cb.count():
                cb.first.click(force=True)
            pg.fill('input[name=email]', email)
            pg.fill('input[name=password]', pwd)
            pg.get_by_role('button', name='Create account').click()
            for _ in range(40):
                pg.wait_for_timeout(500)
                if "dashboard" in pg.url:
                    return True, pg.url, "dashboard"
                body = pg.inner_text("body")
                if "Sign out" in body or ("Overview" in body and "API keys" in body):
                    return True, pg.url, "dashboard-body"
                for marker in ["We can't create another account", "Please complete the human check",
                               "Too many sign-ups", "not supported"]:
                    if marker in body:
                        return False, pg.url, marker
            return False, pg.url, pg.inner_text("body")[-200:].replace("\n", " ")
        finally:
            try:
                b.close()
            except Exception:
                pass


def clean_key_and_consent(email, pwd):
    """干净出口登录 → 建 key + 开免费模型。返回 (key, consent_ok, info)。"""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=["--no-first-run", "--disable-dev-shm-usage"])
        try:
            ctx = b.new_context(user_agent=UA, viewport={"width": 1280, "height": 900})
            pg = ctx.new_page()
            pg.goto("https://tokenharbor.ai/login", wait_until="domcontentloaded", timeout=60000)
            pg.fill('input[name=email]', email)
            pg.fill('input[name=password]', pwd)
            pg.click('button[type=submit]')
            for _ in range(30):
                pg.wait_for_timeout(500)
                if "dashboard" in pg.url:
                    break
            if "dashboard" not in pg.url:
                return None, False, "login-fail " + pg.url
            res = pg.evaluate("""async () => {
                const r = await fetch('/api/keys', {method:'POST',
                    headers:{'Content-Type':'application/json','Accept':'application/json'},
                    body: JSON.stringify({label:'ps-br-'+Math.floor(Math.random()*900+100)})});
                return {status:r.status, text:await r.text()};
            }""")
            if res.get("status") != 201:
                return None, False, f"key-{res.get('status')} {res.get('text','')[:80]}"
            key = json.loads(res["text"]).get("plaintext")
            cres = pg.evaluate("""async (txt) => {
                const r = await fetch('/api/me/privacy', {method:'POST',
                    headers:{'content-type':'application/json'},
                    body: JSON.stringify({free_models_enabled:true, shown_text:txt,
                                          screen:'consent_modal', path:'/dashboard'})});
                return {status:r.status, text:await r.text()};
            }""", CONSENT_TEXT)
            consent = cres.get("status") == 200
            return key, consent, f"key=201 consent={cres.get('status')}"
        finally:
            try:
                b.close()
            except Exception:
                pass


def worker(wid):
    while not stop.is_set():
        with lock:
            if len(done) + pending[0] >= TARGET:
                stop.set(); return
            n = next(attempts)
            if n > MAX_TRY:
                stop.set(); return
        try:
            proxy = proxy_q.get(timeout=2)
            e = email_q.get(timeout=2)
        except queue.Empty:
            stop.set(); return
        email, pwd = e["email"], ''.join(random.choices(string.ascii_letters + string.digits, k=12)) + '!Aa1'
        t0 = time.time()
        try:
            ok, url, info = browser_signup(proxy, email, pwd)
        except Exception as ex:
            print(f"  [w{wid} #{n}] {proxy['host']} EXC {type(ex).__name__}: {str(ex)[:70]}", flush=True)
            continue
        if not ok:
            print(f"  [w{wid} #{n}] {proxy['host']} ✗ {info[:80]} ({time.time()-t0:.0f}s)", flush=True)
            continue
        print(f"  [w{wid} #{n}] ✓ SIGNUP {email} via {proxy['host']} → 验证/建key", flush=True)
        with lock:
            pending[0] += 1
            with open("ps_partial_signups.jsonl", "a", encoding="utf-8") as pf:
                pf.write(json.dumps({"email": email, "password": pwd, "token": e["token"],
                                     "proxy": proxy["host"],
                                     "created_at": time.strftime("%Y%m%d-%H%M%S")},
                                    ensure_ascii=False) + "\n")
        try:
            verified = tar.verify_email(e["token"], max_wait=180)
            if not verified:
                print(f"  [w{wid} #{n}] ✗ 邮箱未验证 {email}", flush=True)
                continue
            key, consent, kinfo = clean_key_and_consent(email, pwd)
            if not key and "step_up_required" in kinfo:
                time.sleep(12)
                key, consent, kinfo = clean_key_and_consent(email, pwd)
            if not key:
                print(f"  [w{wid} #{n}] ✗ 建key失败 {email} {kinfo}", flush=True)
                continue
            acct = {"email": email, "password": pwd, "api_key": key, "verified": True,
                    "consent": consent, "test": "untested", "proxy": proxy["host"],
                    "created_at": time.strftime("%Y%m%d-%H%M%S")}
            with lock:
                done.append(acct)
                with open(TXT_OUT, "a", encoding="utf-8") as f:
                    f.write(f"{email}\t{pwd}\t{key}\tverified=True\ttest=untested\n")
                with open(JSON_OUT, "a", encoding="utf-8") as f:
                    f.write(json.dumps(acct, ensure_ascii=False) + "\n")
            print(f"  [w{wid} #{n}] ★ 有效账号 {email} {kinfo} ({time.time()-t0:.0f}s)", flush=True)
        finally:
            with lock:
                pending[0] -= 1


def main():
    proxies = load_proxies()
    load_emails("/tmp/yyds_browser_emails.json")
    for p in proxies:
        proxy_q.put(p)
    print(f"=== ProxyScrape browser farm | 目标 {TARGET} | 线程 {THREADS} | 代理 {len(proxies)} | 尝试上限 {MAX_TRY} ===", flush=True)
    ts = [threading.Thread(target=worker, args=(i + 1,), daemon=True) for i in range(THREADS)]
    for t in ts: t.start()
    for t in ts: t.join()
    print(f"\n=== DONE: {len(done)}/{TARGET} ===", flush=True)
    print(f"TXT: {TXT_OUT}\nJSON: {JSON_OUT}", flush=True)


if __name__ == "__main__":
    main()
