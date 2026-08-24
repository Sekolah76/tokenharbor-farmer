#!/usr/bin/env python3
"""th_farm_multi.py — TokenHarbor Farm PARALEL (multi-Tor).
Farm N akun menggunakan M instance Tor (paralel) → speed Mx.
Default: 10 akun, 3 Tor paralel. Jumlah bebas (genap/ganjil).

Usage:
  python th_farm_multi.py              # 10 akun, 3 Tor
  python th_farm_multi.py 50           # 50 akun
  python th_farm_multi.py 50 --tors 5  # 50 akun, 5 Tor paralel
  python th_farm_multi.py --no-inject  # tanpa inject 9router

Requirement: tor.exe + lyrebird di ~/.local/tor/ (atau TOR_DIR env).
Script spawn instance Tor sendiri (port otomatis) — TIDAK butuh Tor manual.
"""
import os, sys, json, time, threading, subprocess, socket, random, string, uuid, sqlite3
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
TOR_DIR = os.environ.get("TOR_DIR", os.path.expanduser("~/.local/tor/tor"))
NINE_ROUTER_DB = os.environ.get("NINE_ROUTER_DB", r"C:\Users\Arsyad\AppData\Roaming\9router\db\data.sqlite")
STATE_FILE = os.path.join(HERE, "th_tor_state.json")
INJECT = "--no-inject" not in sys.argv

# base ports utk instance Tor (tiap instance: socks + control)
BASE_SOCKS = [9050, 9150, 9250, 9350, 9450, 9550, 9650, 9750]
BASE_CTRL = [9051, 9151, 9251, 9351, 9451, 9551, 9651, 9751]

def log(msg, lv="INFO"):
    print(f"  [{datetime.now().strftime('%H:%M:%S')}] [{lv}] {msg}", flush=True)

def port_free(port):
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=1)
        s.close()
        return False
    except Exception:
        return True

def spawn_tor(idx, socks_port, ctrl_port):
    """Spawn 1 instance Tor. Return process or None."""
    data_dir = os.path.join(HERE, f".tor{idx}")
    os.makedirs(data_dir, exist_ok=True)
    torrc = os.path.join(data_dir, "torrc")
    with open(torrc, "w") as f:
        f.write(f"SocksPort 127.0.0.1:{socks_port}\n"
                f"ControlPort {ctrl_port}\n"
                f"CookieAuthentication 0\n"
                f"DataDirectory {data_dir}\n")
    tor_exe = os.path.join(TOR_DIR, "tor.exe")
    if not os.path.exists(tor_exe):
        log(f"tor.exe tidak ada di {TOR_DIR}", "ERROR")
        return None
    try:
        proc = subprocess.Popen([tor_exe, "-f", torrc],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return proc
    except Exception as e:
        log(f"spawn tor {idx} gagal: {str(e)[:50]}", "ERROR")
        return None

def wait_tor_ready(socks_port, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not port_free(socks_port):
            return True
        time.sleep(2)
    return False

def newnym_ctrl(ctrl_port):
    try:
        s = socket.create_connection(("127.0.0.1", ctrl_port), timeout=5)
        s.sendall(b"AUTHENTICATE\r\n"); s.recv(100)
        s.sendall(b"SIGNAL NEWNYM\r\n"); r = s.recv(100).decode()
        s.close()
        return "OK" in r
    except Exception:
        return False

# ============ REUSE register logic dari th_tor_farm (copy mandiri) ============
import requests, urllib.parse, re

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

def gen_email():
    try:
        r = requests.post("https://api.tempmail.lol/v2/inbox/create", timeout=12)
        d = r.json()
        return d.get("address", ""), d.get("token", "")
    except Exception:
        return None, None

def verify_email(email_token, socks_port, max_wait=120):
    P = {"http": f"socks5h://127.0.0.1:{socks_port}", "https": f"socks5h://127.0.0.1:{socks_port}"}
    start = time.time()
    while time.time() - start < max_wait:
        try:
            r = requests.get(f"https://api.tempmail.lol/v2/inbox?token={email_token}", timeout=12)
            for em in r.json().get("emails", []):
                links = re.findall(r'(https://tokenharbor\.ai/verify-email\?[^\s"<>]+)', em.get("body", ""))
                if links:
                    try: requests.get(links[0], timeout=20, proxies=P, allow_redirects=True)
                    except Exception:
                        try: requests.get(links[0], timeout=20, allow_redirects=True)
                        except Exception: pass
                    return True
        except Exception: pass
        time.sleep(8)
    return False

def register_one(socks_port, ctrl_port, stats, lock, state, exist):
    """Register via satu Tor instance. Thread-safe via lock."""
    P = {"http": f"socks5h://127.0.0.1:{socks_port}", "https": f"socks5h://127.0.0.1:{socks_port}"}
    email, etok = gen_email()
    if not email:
        return
    pwd = ''.join(random.choices(string.ascii_letters + string.digits, k=12)) + '!Aa1'
    s = requests.Session(); s.headers.update({"User-Agent": UA})
    try:
        s.get(f"{BASE}/login", proxies=P, timeout=40)
    except Exception:
        pass
    body, headers = make_signup_body(email, pwd)
    r = None
    for a in range(3):
        try:
            r = s.post(f"{BASE}/login", data=body, headers=headers, proxies=P, timeout=50)
            break
        except Exception:
            time.sleep(5)
    if r is None or "signedIn" not in r.text:
        errs = [e for e in re.findall(r'"error":"([^"]+)"', getattr(r, "text", "") or "") if e not in ("$f", "$undefined")]
        err = errs[0] if errs else "net"
        if "couldn't create" in err.lower() or "human check" in err.lower():
            newnym_ctrl(ctrl_port)
        return
    uid = re.findall(r'"userId":\s*"([^"]+)"', r.text)
    # cleanup auto keys
    try:
        r2 = s.get(f"{BASE}/api/keys", headers={"Accept": "application/json"}, proxies=P, timeout=30)
        for k in r2.json().get("keys", []):
            try: s.delete(f"{BASE}/api/keys/{k['id']}", proxies=P, timeout=15)
            except Exception: pass
    except Exception: pass
    # create key
    r3 = s.post(f"{BASE}/api/keys", json={"label": f"th-{random.randint(100,999)}"},
                headers={"Accept": "application/json", "Content-Type": "application/json"}, proxies=P, timeout=40)
    if r3.status_code != 201:
        return
    key = r3.json().get("plaintext")
    if not key:
        return
    # consent
    rc = s.post(f"{BASE}/api/me/privacy", json={"free_models_enabled": True},
                headers={"Accept": "application/json", "Content-Type": "application/json"}, proxies=P, timeout=30)
    consent = rc.status_code == 200 and '"ok":true' in rc.text
    # verify
    verified = verify_email(etok, socks_port)
    acct = {"email": email, "password": pwd, "userId": uid[0] if uid else "",
            "api_key": key, "verified": verified, "consent": consent}
    # inject if enabled
    injected = False
    if INJECT:
        injected, _ = inject_9router(key, email)
    acct["injected"] = injected
    with lock:
        if email not in exist:
            state["accounts"].append(acct)
            exist.add(email)
            with open(STATE_FILE, "w") as f:
                json.dump(state, f, indent=2)
            stats["ok"] += 1
            v = "Y" if verified else "N"; c = "Y" if consent else "N"
            print(f"  ✅ [{stats['ok']}] {email[:35]} [v:{v}] [c:{c}] [inj:{'Y' if injected else 'N'}] [tor:{socks_port}]", flush=True)
    # rotate
    newnym_ctrl(ctrl_port)

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
        cur.execute("INSERT INTO providerConnections (id, provider, authType, name, email, priority, isActive, data, createdAt, updatedAt) VALUES (?, 'openai-compatible-chat-52f0bc28-abb2-4d13-8bdb-b7c8d448dc90', 'apikey', ?, ?, 0, 1, ?, ?, ?)",
            (nid, label, email, data, now, now))
        conn.commit(); conn.close()
        return True, label
    except Exception as e:
        return False, str(e)[:60]

def worker(socks_port, ctrl_port, target, stats, lock, state, exist, stop_event):
    """Worker loop: register sampai target tercapai."""
    while not stop_event.is_set():
        with lock:
            if stats["ok"] >= target:
                return
            nxt = stats["ok"]
        # register 1
        register_one(socks_port, ctrl_port, stats, lock, state, exist)
        with lock:
            if stats["ok"] >= target:
                return
        time.sleep(2)

def main():
    # parse args
    n_target = 10
    n_tors = 3
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if args and args[0].isdigit():
        n_target = int(args[0])
    if "--tors" in sys.argv:
        i = sys.argv.index("--tors")
        if i + 1 < len(sys.argv) and sys.argv[i+1].isdigit():
            n_tors = int(sys.argv[i+1])
    n_tors = min(n_tors, len(BASE_SOCKS))
    print(f"=== FARM PARALEL: {n_target} akun, {n_tors} Tor ===", flush=True)
    log(f"Inject 9Router: {'ON' if INJECT else 'OFF'}")

    # state
    state = {"accounts": []}
    if os.path.exists(STATE_FILE):
        state = json.load(open(STATE_FILE))
    exist = {a.get("email") for a in state.get("accounts", [])}
    stats = {"ok": len(state.get("accounts", []))}
    print(f"Sudah ada: {stats['ok']} akun di state", flush=True)
    target = n_target + stats["ok"]

    # spawn Tor instances
    procs = []
    active_tors = []
    for i in range(n_tors):
        try:
            sp = BASE_SOCKS[i]; cp = BASE_CTRL[i]
            if not port_free(sp):
                # sudah ada instance jalan di port ini — reuse
                active_tors.append((sp, cp))
                log(f"Tor {i+1}: reuse port {sp}")
                continue
            proc = spawn_tor(i, sp, cp)
            if proc:
                procs.append(proc)
                if wait_tor_ready(sp, timeout=90):
                    active_tors.append((sp, cp))
                    log(f"Tor {i+1}: spawn OK port {sp}")
                else:
                    log(f"Tor {i+1}: timeout ready", "WARN")
            time.sleep(2)
        except Exception as e:
            log(f"Tor {i+1} setup err: {str(e)[:50]}", "WARN")

    if not active_tors:
        log("TIDAK ada Tor aktif — cek TOR_DIR", "ERROR")
        return

    # run workers
    stop = threading.Event()
    shared_lock = threading.Lock()
    threads = []
    for sp, cp in active_tors:
        t = threading.Thread(target=worker, args=(sp, cp, target, stats, shared_lock, state, exist, stop), daemon=True)
        t.start()
        threads.append(t)
        time.sleep(1)

    # monitor sampai selesai
    try:
        while stats["ok"] < target:
            time.sleep(5)
    except KeyboardInterrupt:
        log("Interrupt — stop", "WARN")
    stop.set()
    for t in threads:
        t.join(timeout=5)

    # cleanup torch
    for p in procs:
        try: p.terminate()
        except Exception: pass
    final = len(state.get("accounts", []))
    print(f"\n=== SELESAI: total {final} akun (target {target}) ===", flush=True)

if __name__ == "__main__":
    main()