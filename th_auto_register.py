#!/usr/bin/env python3
"""th_auto_register.py — TokenHarbor Auto-Register (clean, final).
Mode:
  single                → register 1 akun + verify + inject (tampilkan detail)
  loop N                → register N akun (register→verify→logout→ulang)
  loop N --no-inject    → tanpa inject 9router
  logout-test           → test endpoint logout TH
Requirement: Tor jalan (9050 socks + 9051 control) utk bypass IP.
"""
import requests, re, json, random, string, uuid, urllib.parse, time, os, sys, sqlite3, socket
from datetime import datetime, timezone

BASE = "https://tokenharbor.ai"
AUTH = "https://auth.tokenharbor.ai"
SB = "https://isbnzmwjmtiuipesgmmg.supabase.co"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"
ACTION_ID = "6003703e71fc5dc99543154237e9a9267997419301"
ACTION_KEY = "kb59e6b88b9f36883e58e38e7e48870c6"
NEXT_ACTION = "607ec2c1a962aa81ad67a2483c54b0cfadfda875b2"
ROUTER = urllib.parse.quote('["",{"children":["login",{"children":["__PAGE__",{},null,null,0]},null,null,0]},null,null,20]')
SOCKS = {"http": "socks5h://127.0.0.1:9050", "https": "socks5h://127.0.0.1:9050"}
CONTROL = ("127.0.0.1", 9051)
import db_path
NINE_ROUTER_DB = db_path.find_9router_db()
DIR = os.path.dirname(os.path.abspath(__file__))

# Supabase anon key — env TH_ANON_KEY atau file supabase_config.json (opsional)
def _anon():
    import os as _os
    k = _os.environ.get("TH_ANON_KEY", "")
    if k:
        return k
    for p in ["supabase_config.json"]:
        try:
            return json.load(open(p))["anon_key"]
        except Exception:
            continue
    return ""

def log(msg, lv="INFO"):
    print(f"  [{datetime.now().strftime('%H:%M:%S')}] [{lv}] {msg}", flush=True)

def newnym():
    try:
        s = socket.create_connection(CONTROL, timeout=5)
        s.sendall(b"AUTHENTICATE\r\n"); s.recv(100)
        s.sendall(b"SIGNAL NEWNYM\r\n"); r = s.recv(100).decode()
        s.close(); return "OK" in r
    except Exception:
        return False

def gen_email():
    r = requests.post("https://api.tempmail.lol/v2/inbox/create", timeout=12)
    d = r.json()
    return d["address"], d["token"]

def make_signup_body(email, pwd):
    fp = str(uuid.uuid4())
    bd = "----WebKitFormBoundary" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=16))
    parts = []
    def af(n, v=""):
        parts.append(f'--{bd}\r\nContent-Disposition: form-data; name="{n}"\r\n\r\n{v}')
    af("1_$ACTION_REF_1")
    af("1_$ACTION_1:0", json.dumps({"id": ACTION_ID, "bound": "$@1"}))
    af("1_$ACTION_1:1", '["$undefined"]')
    af("1_$ACTION_KEY", ACTION_KEY)
    af("1_device_fingerprint", fp); af("1_timezone"); af("1_next")
    af("1_email", email); af("1_password", pwd); af("1_invite_code")
    af("0", '["$undefined","$K1"]')
    body = "\r\n".join(parts) + f"\r\n--{bd}--\r\n"
    headers = {
        "Content-Type": f"multipart/form-data; boundary={bd}",
        "Accept": "text/x-component", "Next-Action": NEXT_ACTION,
        "Next-Router-State-Tree": ROUTER, "Origin": BASE, "Referer": f"{BASE}/login",
    }
    return body, headers

def verify_email(email_token, max_wait=120):
    start = time.time()
    while time.time() - start < max_wait:
        try:
            r = requests.get(f"https://api.tempmail.lol/v2/inbox?token={email_token}", timeout=12)
            for em in r.json().get("emails", []):
                links = re.findall(r'(https://tokenharbor\.ai/verify-email\?[^\s"<>]+)', em.get("body", ""))
                if links:
                    try: requests.get(links[0], timeout=20, proxies=SOCKS, allow_redirects=True)
                    except Exception:
                        try: requests.get(links[0], timeout=20, allow_redirects=True)
                        except Exception: pass
                    return True
        except Exception: pass
        time.sleep(8)
    return False

def logout_session(s):
    """Logout via Supabase — hapus session. Return True kalau ok/401."""
    anon = _anon()
    if not anon:
        return False
    try:
        r = s.post(f"{AUTH}/auth/v1/logout", headers={"apikey": anon, "Authorization": f"Bearer {anon}"},
                   proxies=SOCKS, timeout=20)
        return r.status_code in (200, 204, 401)
    except Exception:
        return False

def register_one(do_logout=False):
    email, etok = gen_email()
    pwd = ''.join(random.choices(string.ascii_letters + string.digits, k=12)) + '!Aa1'
    log(f"Email: {email}")
    s = requests.Session(); s.headers.update({"User-Agent": UA})
    for a in range(3):
        try: s.get(f"{BASE}/login", proxies=SOCKS, timeout=40); break
        except Exception: time.sleep(4)
    body, headers = make_signup_body(email, pwd)
    r = None
    for a in range(3):
        try:
            r = s.post(f"{BASE}/login", data=body, headers=headers, proxies=SOCKS, timeout=50); break
        except Exception as e:
            log(f"  retry {a+1}: {str(e)[:40]}")
            time.sleep(5)
    if r is None or "signedIn" not in r.text:
        errs = [e for e in re.findall(r'"error":"([^"]+)"', getattr(r, 'text', '') or '') if e not in ("$f", "$undefined")]
        err = errs[0] if errs else "http/network fail"
        if "couldn't create" in err.lower() or "human check" in err.lower():
            newnym(); time.sleep(6)
        return None, err
    uid = re.findall(r'"userId":\s*"([^"]+)"', r.text)
    log(f"  Signup OK - userId: {uid[0] if uid else '?'}")
    try:
        r2 = s.get(f"{BASE}/api/keys", headers={"Accept": "application/json"}, proxies=SOCKS, timeout=30)
        for k in r2.json().get("keys", []):
            try: s.delete(f"{BASE}/api/keys/{k['id']}", proxies=SOCKS, timeout=15)
            except Exception: pass
    except Exception: pass
    r3 = s.post(f"{BASE}/api/keys", json={"label": f"th-{random.randint(100,999)}"},
                headers={"Accept": "application/json", "Content-Type": "application/json"}, proxies=SOCKS, timeout=40)
    if r3.status_code != 201:
        return None, f"key create {r3.status_code}"
    key = r3.json().get("plaintext")
    if not key:
        return None, "no plaintext"
    log(f"  Key: {key[:35]}...")
    rc = s.post(f"{BASE}/api/me/privacy", json={"free_models_enabled": True},
                headers={"Accept": "application/json", "Content-Type": "application/json"}, proxies=SOCKS, timeout=30)
    consent = rc.status_code == 200 and '"ok":true' in rc.text
    log(f"  Free models: {'Y' if consent else 'N'} ({rc.status_code})")
    log("  Verify email (120s)...")
    verified = verify_email(etok)
    log(f"  Verified: {'Y' if verified else 'N'}")
    acct = {"email": email, "password": pwd, "userId": uid[0] if uid else "",
            "api_key": key, "verified": verified, "consent": consent}
    if do_logout:
        lo = logout_session(s)
        log(f"  Logout: {'Y' if lo else 'N'}")
        acct["logout"] = lo
    return acct, None

def inject_9router(api_key, email, label=None):
    try:
        conn = sqlite3.connect(NINE_ROUTER_DB); cur = conn.cursor()
        nid = 'conn-' + str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        count = cur.execute("SELECT COUNT(*) FROM providerConnections WHERE data LIKE '%tokenharbor.ai%'").fetchone()[0]
        label = label or f"TH th{count+1}"
        data = json.dumps({
            "apiKey": api_key, "label": label, "defaultModel": "deepseek-v4-flash",
            "testStatus": "active",
            "providerSpecificData": {"prefix": "th", "apiType": "chat",
                                     "baseUrl": "https://tokenharbor.ai/v1", "nodeName": "tokenharbor"},
            "errorCode": None, "backoffLevel": 0, "lastUsedAt": None, "consecutiveUseCount": 0,
            "modelLock_mimo-v2.5:free": 1, "modelLock_deepseek-v4-flash:free": 1, "modelLock_qwen3.8-27b:free": 1
        })
        cur.execute("INSERT INTO providerConnections (id, provider, authType, name, email, priority, isActive, data, createdAt, updatedAt) VALUES (?, 'openai-compatible', 'api_key', ?, ?, 0, 1, ?, ?, ?)",
            (nid, label, email, data, now, now))
        conn.commit(); conn.close()
        return True, label
    except Exception as e:
        return False, str(e)[:60]

def test_model(key):
    try:
        r = requests.post(f"{BASE}/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            timeout=50, json={"model": "mimo-v2.5:free", "messages": [{"role": "user", "content": "say ok"}], "max_tokens": 50})
        return r.status_code == 200, f"{r.status_code}"
    except Exception as e:
        return False, f"ERR {str(e)[:20]}"

def cmd_single():
    print("=== Register 1 akun (Tor) ===")
    acct, err = register_one(do_logout=False)
    if not acct:
        print(f"FAILED: {err}"); return
    ok, info = test_model(acct["api_key"])
    inj, imsg = inject_9router(acct["api_key"], acct["email"])
    print(f"\nEmail:    {acct['email']}")
    print(f"Password: {acct['password']}")
    print(f"Key:      {acct['api_key']}")
    print(f"Verify:   {'Y' if acct['verified'] else 'N'}")
    print(f"Consent:  {'Y' if acct['consent'] else 'N'}")
    print(f"Model:    {'OK' if ok else 'FAIL'} {info}")
    print(f"Inject:   {'OK' if inj else 'FAIL'} {imsg}")

def cmd_loop(n, inject=True, do_logout=True):
    print(f"=== Loop register→logout×{n} (inject={inject}) ===")
    stats = {"ok": 0, "fail": 0, "verified": 0}
    results = []
    i = 0
    while stats["ok"] < n:
        i += 1
        print(f"\n  [{i}] ({stats['ok']}/{n}) {'='*30}")
        acct, err = register_one(do_logout=do_logout)
        if not acct:
            stats["fail"] += 1
            print(f"  FAIL: {err}")
            continue
        if acct["verified"]: stats["verified"] += 1
        stats["ok"] += 1
        ok, info = test_model(acct["api_key"])
        acct["test_result"] = info
        if inject:
            inj, imsg = inject_9router(acct["api_key"], acct["email"])
            acct["injected"] = inj
            print(f"  Inject: {'OK' if inj else 'FAIL'} {imsg}")
        results.append(acct)
        with open(os.path.join(DIR, "th_auto_results.jsonl"), "a") as f:
            f.write(json.dumps(acct) + "\n")
        v = "Y" if acct["verified"] else "N"; c = "Y" if acct["consent"] else "N"
        print(f"  RESULT: {acct['email']} [v:{v}] [c:{c}] [model:{'OK' if ok else 'FAIL'}]")
        if stats["ok"] % 2 == 0:
            log("Rotate circuit...")
            newnym()
        time.sleep(random.randint(5, 15))
    print(f"\n=== DONE: {stats['ok']} ok, {stats['fail']} fail, {stats['verified']} verified ===")

def cmd_logout_test():
    print("=== Test logout TH (Supabase) ===")
    acct, err = register_one(do_logout=True)
    if not acct:
        print(f"FAILED: {err}"); return
    print(f"Email: {acct['email']} | logout: {acct.get('logout')}")

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print("Usage: th_auto_register.py [single | loop N [--no-inject] | logout-test]")
    elif args[0] == "single":
        cmd_single()
    elif args[0] == "loop":
        n = int(args[1]) if len(args) > 1 and args[1].isdigit() else 5
        cmd_loop(n, inject="--no-inject" not in args)
    elif args[0] == "logout-test":
        cmd_logout_test()
    else:
        print("Usage: th_auto_register.py [single | loop N [--no-inject] | logout-test]")