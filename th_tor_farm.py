#!/usr/bin/env python3
"""th_tor_farm.py — TokenHarbor Farm via TOR (unlimited IP rotation).
Loop: signup via Tor → verify email (temp mail) → consent free models → create key
→ test → inject 9router (gabung, tanpa hapus) → NEWNYM rotate → ulang.
1 akun = 1 apikey. Bypass IP rate-limit total (Tor exit node beragam).

Usage: python th_tor_farm.py [jumlah] [--no-inject]
"""
import requests, re, sys, os, time, uuid, random, string, json, sqlite3, socket
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import th_farm_pro as m  # reuse make_signup_body, UA, dll

SOCKS = {"http": "socks5h://127.0.0.1:9050", "https": "socks5h://127.0.0.1:9050"}
CONTROL = ("127.0.0.1", 9051)
NINE_ROUTER_DB = m.NINE_ROUTER_DB
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
    s = requests.Session(); s.headers.update({"User-Agent": m.UA})
    try:
        s.get(f"{m.BASE}/login", proxies=SOCKS, timeout=40)
    except Exception as e:
        log(f"login page: retry via direct...")
        try:
            s.get(f"{m.BASE}/login", timeout=25)
        except Exception:
            pass
    body, headers = m.make_signup_body(email, pwd)
    r = None
    for attempt in range(3):
        try:
            r = s.post(f"{m.BASE}/login", data=body, headers=headers, proxies=SOCKS, timeout=50)
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
        r2 = s.get(f"{m.BASE}/api/keys", headers={"Accept": "application/json"}, proxies=SOCKS, timeout=30)
        for k in r2.json().get("keys", []):
            try:
                s.delete(f"{m.BASE}/api/keys/{k['id']}", proxies=SOCKS, timeout=15)
            except: pass
    except: pass
    # create key
    r3 = s.post(f"{m.BASE}/api/keys", json={"label": f"th-{random.randint(100,999)}"},
                headers={"Accept": "application/json", "Content-Type": "application/json"}, proxies=SOCKS, timeout=40)
    if r3.status_code != 201:
        return None, f"key create failed {r3.status_code}"
    key = r3.json().get("plaintext")
    if not key:
        return None, "no plaintext"
    log(f"  Key: {key[:35]}...")
    # consent free models
    rc = s.post(f"{m.BASE}/api/me/privacy", json={"free_models_enabled": True},
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
            "modelLock_mimo-v2.5:free": 1, "modelLock_deepseek-v4-flash:free": 1
        })
        cur.execute("INSERT INTO providerConnections (id, provider, authType, name, email, priority, isActive, data, createdAt, updatedAt) VALUES (?, 'openai-compatible', 'api_key', ?, ?, 0, 1, ?, ?, ?)",
            (nid, label, email, data, now, now))
        conn.commit(); conn.close()
        return True, label
    except Exception as e:
        return False, str(e)[:60]

def test_model(key):
    try:
        r = requests.post(f"{m.BASE}/v1/chat/completions",
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
    while len(state["accounts"]) < n:
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