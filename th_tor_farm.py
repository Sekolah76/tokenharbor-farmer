#!/usr/bin/env python3
"""th_tor_farm.py — TokenHarbor Farm via TOR (unlimited IP rotation).
Loop: signup via Tor → verify email (temp mail) → consent free models → create key
→ test → inject 9router (gabung, tanpa hapus) → NEWNYM rotate → ulang.
1 akun = 1 apikey. Bypass IP rate-limit total (Tor exit node beragam).

Usage: python th_tor_farm.py [jumlah] [--no-inject]
"""
import requests, re, sys, os, time, uuid, random, string, json, sqlite3, socket, urllib.parse
from datetime import datetime, timezone

# --- Konstanta (mandiri — tidak butuh file lain) ---
BASE = "https://tokenharbor.ai"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"
ACTION_ID = "6003703e71fc5dc99543154237e9a9267997419301"
ACTION_KEY = "kb59e6b88b9f36883e58e38e7e48870c6"
NEXT_ACTION = "607ec2c1a962aa81ad67a2483c54b0cfadfda875b2"
ROUTER = urllib.parse.quote('["",{"children":["login",{"children":["__PAGE__",{},null,null,0]},null,null,0]},null,null,20]')

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

SOCKS = {"http": "socks5h://127.0.0.1:9050", "https": "socks5h://127.0.0.1:9050"}
CONTROL = ("127.0.0.1", 9051)
import db_path as _dbp
NINE_ROUTER_DB = _dbp.find_9router_db()
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "th_tor_state.json")
INJECT = "--no-inject" not in sys.argv

def log(msg, level="INFO"):
    print(f"  [{datetime.now().strftime('%H:%M:%S')}] [{level}] {msg}", flush=True)

def newnym():
    """Rotasi circuit Tor → IP exit baru."""
    try:
        s = socket.create_connection(CONTROL, timeout=5)
        s.sendall(b"AUTHENTICATE\r\n"); s.recv(100)
        s.sendall(b"SIGNAL NEWNYM\r\n"); r = s.recv(100).decode()
        s.close()
        return "OK" in r
    except Exception as e:
        log(f"newnym err: {str(e)[:40]}", "WARN")
        return False

def gen_email():
    try:
        r = requests.post("https://api.tempmail.lol/v2/inbox/create", timeout=12)
        return r.json()
    except Exception as e:
        log(f"tempmail err: {str(e)[:40]}", "WARN")
        return None

def verify_email(email_token, max_wait=120):
    start = time.time()
    while time.time() - start < max_wait:
        try:
            r = requests.get(f"https://api.tempmail.lol/v2/inbox?token={email_token}", timeout=12)
            for em in r.json().get("emails", []):
                links = re.findall(r'(https://tokenharbor\.ai/verify-email\?[^\s"<>]+)', em.get("body", ""))
                if links:
                    try:
                        requests.get(links[0], timeout=20, proxies=SOCKS, allow_redirects=True)
                        return True
                    except Exception:
                        try:
                            requests.get(links[0], timeout=20, allow_redirects=True)
                            return True
                        except Exception:
                            pass
        except Exception:
            pass
        time.sleep(8)
    return False

def register_one():
    email_data = gen_email()
    if not email_data:
        return None, "email fail"
    email = email_data["address"]; email_token = email_data["token"]
    pwd = ''.join(random.choices(string.ascii_letters + string.digits, k=12)) + '!Aa1'
    log(f"Email: {email}")
    s = requests.Session(); s.headers.update({"User-Agent": UA})
    try:
        s.get(f"{BASE}/login", proxies=SOCKS, timeout=40)
    except Exception as e:
        log(f"login page: retry via direct...")
        try:
            s.get(f"{BASE}/login", timeout=25)
        except Exception:
            pass
    body, headers = make_signup_body(email, pwd)
    r = None
    for attempt in range(3):
        try:
            r = s.post(f"{BASE}/login", data=body, headers=headers, proxies=SOCKS, timeout=50)
            break
        except Exception as e:
            log(f"  signup retry {attempt+1}: {str(e)[:30]}")
            time.sleep(5)
    if r is None:
        return None, "tor timeout"
    if "signedIn" not in r.text:
        errs = [e for e in re.findall(r'"error":"([^"]+)"', r.text) if e not in ("$f", "$undefined")]
        err = errs[0] if errs else f"HTTP {r.status_code}"
        log(f"  signup FAIL: {err[:80]}", "ERROR")
        # "couldn't create" = exit node buruk → rotate langsung
        if "couldn't create" in err.lower() or "human check" in err.lower():
            log("  Exit node buruk — rotate circuit", "WARN")
            newnym()
            time.sleep(8)
        return None, err
    uid = re.findall(r'"userId":\s*"([^"]+)"', r.text)
    log(f"  Signup OK - userId: {uid[0] if uid else '?'}")
    # cleanup auto keys (1 akun 1 key)
    try:
        r2 = s.get(f"{BASE}/api/keys", headers={"Accept": "application/json"}, proxies=SOCKS, timeout=30)
        for k in r2.json().get("keys", []):
            try:
                s.delete(f"{BASE}/api/keys/{k['id']}", proxies=SOCKS, timeout=15)
            except: pass
    except: pass
    # create key
    r3 = s.post(f"{BASE}/api/keys", json={"label": f"th-{random.randint(100,999)}"},
                headers={"Accept": "application/json", "Content-Type": "application/json"}, proxies=SOCKS, timeout=40)
    if r3.status_code != 201:
        return None, f"key create failed {r3.status_code}"
    key = r3.json().get("plaintext")
    if not key:
        return None, "no plaintext"
    log(f"  Key: {key[:35]}...")
    # consent free models
    rc = s.post(f"{BASE}/api/me/privacy", json={"free_models_enabled": True},
                headers={"Accept": "application/json", "Content-Type": "application/json"}, proxies=SOCKS, timeout=30)
    consent = rc.status_code == 200 and '"ok":true' in rc.text
    log(f"  Free models: {'Y' if consent else 'N'} ({rc.status_code})")
    # verify email
    log("  Verify email (120s)...")
    verified = verify_email(email_token)
    log(f"  Verified: {'Y' if verified else 'N'}")
    return {"email": email, "password": pwd, "userId": uid[0] if uid else "",
            "api_key": key, "verified": verified, "consent": consent}, None

def inject_9router(api_key, email):
    try:
        conn = sqlite3.connect(NINE_ROUTER_DB); cur = conn.cursor()
        nid = 'conn-' + str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        count = cur.execute("SELECT COUNT(*) FROM providerConnections WHERE data LIKE '%tokenharbor.ai%'").fetchone()[0]
        label = f"TH th{count+1}"
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

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"accounts": []}

def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 100
    state = load_state()
    exist = {a["email"] for a in state["accounts"]}
    print(f"=== TOR FARM — target {n}, sudah {len(state['accounts'])} ===", flush=True)
    log(f"Inject 9router: {'ON' if INJECT else 'OFF'}")
    success = 0; fail_count = 0
    last_progress = time.time()
    while len(state["accounts"]) < n:
        # smart pause: 10 menit tanpa progress → istirahat 5 menit (redam human-check wave)
        if time.time() - last_progress > 600 and fail_count > 0:
            log(f"10 menit tanpa progress ({fail_count} fail) — pause 5 menit...", "WARN")
            time.sleep(300)
            newnym()
            last_progress = time.time()
            fail_count = 0
        if fail_count >= 5:
            log("5 gagal berturut — rotate circuit", "WARN")
            newnym(); time.sleep(10); fail_count = 0
        print(f"\n  [{len(state['accounts'])+1}/{n}] {'='*40}")
        acct, err = register_one()
        if not acct:
            fail_count += 1
            log(f"Gagal: {err}", "ERROR")
            newnym(); time.sleep(5)
            # cek ulang email — kadang tempmail gagal
            continue
        if acct["email"] in exist:
            fail_count += 1; continue
        ok, info = test_model(acct["api_key"])
        log(f"  Test: {'OK' if ok else 'FAIL'} {info}")
        acct["test_result"] = info
        inj = False; imsg = ""
        if INJECT:
            inj, imsg = inject_9router(acct["api_key"], acct["email"])
            log(f"  Inject: {'OK' if inj else 'FAIL'} {imsg}")
        acct["injected"] = inj
        state["accounts"].append(acct); exist.add(acct["email"])
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
        success += 1; fail_count = 0
        last_progress = time.time()
        v = "Y" if acct["verified"] else "N"; c = "Y" if acct["consent"] else "N"
        print(f"  RESULT: {acct['email']} [v:{v}] [c:{c}] [inj:{'Y' if inj else 'N'}]", flush=True)
        # rotate circuit tiap 2 akun (IP beda utk rate-limit)
        if success % 2 == 0:
            log("Rotate circuit (NEWNYM)...")
            newnym()
        time.sleep(random.randint(5, 15))
    print(f"\n=== SELESAI: {success} akun baru (total {len(state['accounts'])}) ===")

if __name__ == "__main__":
    main()