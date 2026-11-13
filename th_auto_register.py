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
# --- Next.js Server Action IDs (更新于 2026-09-17，对应站点当前 build) ---
# 站点每次重新部署会轮换这些值；若出现 404 "Server action not found"，
# 说明需要重新提取：见 find_action_ids.py / dig_login_form.py
ACTION_ID = "60ca12ab179b172454e25ddb1dc426aa678f48151f"    # signUp（2026-11-12 重部署 dpl_CSRubXGHz… 后更新）
ACTION_KEY = "kaf752891006e153cd1bf1bdcd14addfd"            # 表单 $ACTION_KEY（本次未变）
NEXT_ACTION = "60ca12ab179b172454e25ddb1dc426aa678f48151f"  # Next-Action 头 = signUp
ROUTER = urllib.parse.quote('["",{"children":["login",{"children":["__PAGE__",{},null,null,4096]},null,null,4096]},null,null,4116]')
SOCKS = {"http": "socks5h://127.0.0.1:9050", "https": "socks5h://127.0.0.1:9050"}
# 验证链接点开时的回退代理：Tor 常不可用，国内直连 tokenharbor 又超时，
# 用 socks_nolocal.py（127.0.0.1:18082 无认证 → duckip relay 18080）当住宅出口。
VERIFY_SOCKS = {"http": os.environ.get("TH_VERIFY_SOCKS", "socks5h://127.0.0.1:18082"),
                "https": os.environ.get("TH_VERIFY_SOCKS", "socks5h://127.0.0.1:18082")}
CONTROL = ("127.0.0.1", 9051)
import db_path
NINE_ROUTER_DB = db_path.find_9router_db()
DIR = os.path.dirname(os.path.abspath(__file__))

# --- 临时邮箱 provider ---
# 优先级：Cloudflare 自建收信（可选）→ YYDS Mail → tempmail.lol
#
# Cloudflare 方案走 catch-all，地址本地生成、不需要建箱 API —— 建箱成本为零，
# 彻底避开 YYDS / tempmail 的建箱配额（我们跑到 200+ 次尝试就被节流了）。
# 部署见 cf-mail/README.md。
CF_MAIL_BASE = os.environ.get("CF_MAIL_BASE", "").rstrip("/")
CF_MAIL_TOKEN = os.environ.get("CF_MAIL_TOKEN", "")
CF_MAIL_DOMAIN = os.environ.get("CF_MAIL_DOMAIN", "")

YYDS_API = os.environ.get("YYDS_API", "https://maliapi.215.im/v1")
YYDS_KEY = os.environ.get("YYDS_API_KEY", "AC-da0ad189729c0215e420007b")
# 优先尝试的 YYDS 域名（收不到 tokenharbor 验证邮件的会被记进 yyds_bad_domains.json 排除）
YYDS_PREFERRED_DOMAINS = [d for d in os.environ.get(
    "YYDS_DOMAINS", "edu.jstyo.com,204901.xyz,100811.xyz,20220108.xyz").split(",") if d]

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

# 实测：这些域名收不到 tokenharbor 的验证邮件（注册成功但邮件永不投递 → key 403）。
# nexushint.com / pointnexus.org / barconic.org 实测 10/10 全部验证成功。
BAD_EMAIL_DOMAINS = ("lowdn.com",)
PREFERRED_EMAIL_DOMAINS = ("nexushint.com", "pointnexus.org", "barconic.org")

def _dom_of(addr):
    return (addr or "").rsplit("@", 1)[-1].lower()

def _bad_domain(addr):
    dom = _dom_of(addr)
    return any(dom == b or dom.endswith("." + b) for b in BAD_EMAIL_DOMAINS)


# --- YYDS Mail 域名可用性记忆 ---
# tokenharbor 的发信方会静默丢弃某些域名（注册成功但邮件永远不到）。
# 每试一个新域名就记一次结果，下次优先用能收信的。
YYDS_BAD_FILE = os.path.join(DIR, "yyds_bad_domains.json")


def _yyds_bad_domains():
    try:
        return set(json.load(open(YYDS_BAD_FILE)))
    except Exception:
        return set()


def _yyds_mark_bad(addr):
    dom = _dom_of(addr)
    bad = _yyds_bad_domains()
    bad.add(dom)
    try:
        with open(YYDS_BAD_FILE, "w") as f:
            json.dump(sorted(bad), f, indent=1)
    except Exception:
        pass
    log(f"  YYDS 域名 {dom} 记为收不到验证邮件")


def _yyds_create(domain=None, local=None, tries=8):
    """创建 YYDS 收件箱。返回 (address, "yyds:<address>")

    domain 省略 → 从 YYDS_PREFERRED_DOMAINS 里挑（跳过已拉黑的）；
    local  省略 → 随机生成。两者都给就是完全自定义地址。
    注意：自定义子域（subdomain / /accounts/wildcard）需要更高权限，
    当前 key 会返回 permission_denied。
    """
    bad = _yyds_bad_domains()
    last_err = "?"
    for i in range(tries):
        lp = local or ''.join(random.choices(string.ascii_lowercase + string.digits, k=12))
        body = {"localPart": lp}
        dom = domain
        if dom is None:
            pool = [d for d in YYDS_PREFERRED_DOMAINS if d not in bad]
            dom = pool[i % len(pool)] if pool else None
        if dom:
            body["domain"] = dom
        try:
            r = requests.post(f"{YYDS_API}/accounts", json=body,
                              headers={"X-API-Key": YYDS_KEY}, timeout=25)
            d = r.json()
        except Exception as e:
            last_err = f"{type(e).__name__}"
            time.sleep(2)
            continue
        if not d.get("success"):
            last_err = d.get("error") or f"http {r.status_code}"
            if r.status_code == 429:
                time.sleep(3 + i * 3)
            elif local:
                break        # 指定的地址建不出来就别重试了，直接报错
            continue
        data = d.get("data") or {}
        addr = data.get("address", "")
        if addr and not _bad_domain(addr) and _dom_of(addr) not in bad:
            return addr, "yyds:" + addr
        if addr:
            _yyds_mark_bad(addr)          # 已知收不到 → 换个域名再来
    raise RuntimeError(f"YYDS create failed: {last_err}")


def _tempmail_create(tries=6):
    """回退方案：tempmail.lol。返回 (address, token)"""
    for i in range(tries):
        dom = PREFERRED_EMAIL_DOMAINS[i % len(PREFERRED_EMAIL_DOMAINS)]
        try:
            r = requests.post("https://api.tempmail.lol/v2/inbox/create",
                              json={"domain": dom}, timeout=15)
            if r.status_code == 429:
                time.sleep(6 + i * 6)
                continue
            d = r.json()
            addr = d.get("address", "")
            if addr and not _bad_domain(addr):
                return addr, d["token"]
        except Exception:
            time.sleep(3)
    for i in range(tries):
        try:
            r = requests.post("https://api.tempmail.lol/v2/inbox/create", timeout=15)
            if r.status_code == 429:
                time.sleep(8 + i * 6)
                continue
            d = r.json()
            addr = d.get("address", "")
            if addr and not _bad_domain(addr):
                return addr, d["token"]
        except Exception:
            time.sleep(3)
    raise RuntimeError("tempmail create failed (rate limited or all domains bad)")


def cfmail_ready():
    return bool(CF_MAIL_BASE and CF_MAIL_TOKEN and CF_MAIL_DOMAIN)


def _cfmail_create(local=None):
    """Cloudflare catch-all：地址在本地拼出来，不需要任何 API 调用。

    这正是它相对 YYDS / tempmail 的核心优势 —— 建箱成本为零，
    不会有"Too many account creation requests"这种节流。
    返回 (address, "cf:<address>")。
    """
    if not cfmail_ready():
        raise RuntimeError("CF Mail 未配置（需要 CF_MAIL_BASE / CF_MAIL_TOKEN / CF_MAIL_DOMAIN）")
    lp = local or ''.join(random.choices(string.ascii_lowercase + string.digits, k=12))
    addr = f"{lp}@{CF_MAIL_DOMAIN}"
    if _bad_domain(addr):
        raise RuntimeError(f"CF_MAIL_DOMAIN {CF_MAIL_DOMAIN} 在坏域名名单里")
    return addr, "cf:" + addr


def gen_email(tries=6, local=None, domain=None):
    """创建临时邮箱，返回 (address, token)。

    自定义地址（固定域名范围内可用）：
      gen_email(local="myname", domain="edu.jstyo.com")  →  you@example.com
      或用环境变量：
        YYDS_LOCAL=myname          固定地址（只适合单账号，重名会失败）
        YYDS_LOCAL_PREFIX=thf      前缀 + 随机后缀（批量推荐，保持唯一）
        YYDS_DOMAINS=a.com,b.com   候选域名（跳过已拉黑的）

    token 前缀区分 provider："cf:"（Cloudflare 自建收信）/
    "yyds:"（YYDS Mail）/ tempmail 的原始 token。"""
    if not local:
        prefix = os.environ.get("YYDS_LOCAL_PREFIX", "")
        local = os.environ.get("YYDS_LOCAL") or (
            prefix + ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
            if prefix else None)
    if cfmail_ready():
        return _cfmail_create(local)
    try:
        return _yyds_create(domain=domain, local=local)
    except Exception as e:
        log(f"  YYDS 创建失败（{str(e)[:50]}），回退 tempmail.lol")
        return _tempmail_create(tries)


def flight_errors(text):
    """从 Next.js flight 响应里抽出真实错误文案。

    坑：页面里会出现 "error":"$11" 这种引用，它指向的是 React 的 error
    boundary 组件（$NN 是行 ID 引用），不是错误消息 —— 必须解引用后确认
    那一行真的是 {"error": "..."}，否则就是假阳性，直接丢弃。
    真实文案可能在另一行，所以调用方要遍历全部结果而不是只看第 0 个。
    """
    lines = {}
    for line in (text or "").split("\n"):
        line = line.strip()
        if not line:
            continue
        m = re.match(r'^([0-9a-f]+):(.*)$', line)
        if m:
            lines[m.group(1)] = m.group(2)

    out = []
    for m in re.finditer(r'"error":"([^"]*)"', text or ""):
        v = m.group(1)
        if v in ("$f", "$undefined"):
            continue
        if v.startswith("$"):
            # 引用：解出来确认是不是真的错误对象
            data = None
            ref = v[1:]
            if ref in lines:
                raw = lines[ref]
                # flight 行格式是 "<id>:<类型前缀><payload>"，如 1:E{"digest":...}
                if raw[:1] in ("E", "I", "T", "D", "R") and not raw[:1].isdigit():
                    raw = raw[1:]
                try:
                    data = json.loads(raw)
                except Exception:
                    data = None
            if isinstance(data, dict):
                msg = data.get("error") or data.get("message")
                if msg and not str(msg).startswith("$"):
                    out.append(str(msg))
                elif data.get("digest"):
                    out.append(f"Server error (digest {data['digest']})")
            # 解不出来 / 不是错误对象 → 组件引用，丢弃（避免假阳性）
            continue
        out.append(v)
    # 去重，保持顺序
    seen, uniq = set(), []
    for e in out:
        if e not in seen:
            seen.add(e)
            uniq.append(e)
    return uniq

def signup_precheck(s, fp):
    """注册前置：把 device_fingerprint 交给站点登记。跳过这步 signUp 会直接 500。
    返回 needCaptcha（True 表示该出口需要过 Cloudflare Turnstile）。"""
    try:
        r = s.get(f"{BASE}/api/auth/signup-precheck?fp={urllib.parse.quote(fp)}",
                  headers={"Accept": "application/json"}, timeout=25)
        return bool(r.json().get("needCaptcha"))
    except Exception:
        return None

def make_signup_body(email, pwd, fp=None):
    fp = fp or str(uuid.uuid4())
    bd = "----WebKitFormBoundary" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=16))
    parts = []
    def af(n, v=""):
        parts.append(f'--{bd}\r\nContent-Disposition: form-data; name="{n}"\r\n\r\n{v}')
    af("_1_$ACTION_REF_1")
    af("_1_$ACTION_1:0", json.dumps({"id": ACTION_ID, "bound": "$@1"}, separators=(",", ":")))
    af("_1_$ACTION_1:1", '["$undefined"]')
    af("_1_$ACTION_KEY", ACTION_KEY)
    af("_1_device_fingerprint", fp); af("_1_timezone"); af("_1_next")
    af("_1_email", email); af("_1_password", pwd); af("_1_invite_code")
    af("0", '["$undefined","$K1"]')
    body = "\r\n".join(parts) + f"\r\n--{bd}--\r\n"
    headers = {
        "Content-Type": f"multipart/form-data; boundary={bd}",
        "Accept": "text/x-component", "Next-Action": NEXT_ACTION,
        "Next-Router-State-Tree": ROUTER, "Origin": BASE, "Referer": f"{BASE}/login",
    }
    return body, headers, fp

def _click_verify_link(link, waited):
    """点验证链接。多出口依次试：住宅 socks → Tor → mihomo → 直连。"""
    log(f"  verify link found after {waited}s")
    mihomo = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}
    for tag, proxies in (("residential", VERIFY_SOCKS), ("tor", SOCKS),
                         ("mihomo", mihomo), ("direct", None)):
        try:
            # 不跟随跳转：/verify-email 成功时返回 307 → /dashboard?verify=success，
            # 跟随后 dashboard 对未登录会话是 403，会被误判为验证失败。
            rr = requests.get(link, timeout=25, proxies=proxies, allow_redirects=False)
            log(f"  verify GET[{tag}] -> {rr.status_code} {rr.headers.get('location','')[:60]}")
            if rr.status_code < 400 or (300 <= rr.status_code < 400
                                        and "verify=success" in (rr.headers.get("location") or "")):
                return True
        except Exception as e:
            log(f"  verify GET[{tag}] err: {str(e)[:50]}")
    return False


def _harvest_links(blob):
    return [l.replace("&amp;", "&") for l in
            re.findall(r'https://tokenharbor\.ai/verify-email\?[^\s"<>\'\)]+', blob or "")]


def _verify_cfmail(addr, max_wait):
    """Cloudflare 自建收信：轮询 worker 的 /inbox 接口。"""
    start = time.time()
    headers = {"X-Mail-Token": CF_MAIL_TOKEN}
    while time.time() - start < max_wait:
        for params in ({"to": addr, "grep": "verify-email"}, {"to": addr}):
            try:
                r = requests.get(f"{CF_MAIL_BASE}/inbox", params=params,
                                 headers=headers, timeout=20)
                d = r.json()
                for m in (d.get("messages") or []):
                    blob = (m.get("body") or "") + " " + (m.get("subject") or "")
                    links = _harvest_links(blob)
                    if links:
                        return _click_verify_link(links[0], int(time.time() - start))
            except Exception:
                pass
        time.sleep(6)
    return False


def _verify_yyds(addr, max_wait):
    """YYDS Mail：用 /v1/messages 按地址查信。"""
    start = time.time()
    headers = {"X-API-Key": YYDS_KEY}
    while time.time() - start < max_wait:
        try:
            r = requests.get(f"{YYDS_API}/messages", params={"address": addr},
                             headers=headers, timeout=20)
            d = r.json()
            for em in (d.get("data") or {}).get("messages", []) or []:
                blob = " ".join(str(em.get(k) or "") for k in
                                ("subject", "text", "html", "body", "preview", "snippet"))
                links = _harvest_links(blob)
                if not links:
                    # 列表里可能只给摘要，取全文再试一次
                    mid = em.get("id")
                    if mid:
                        try:
                            rd = requests.get(f"{YYDS_API}/messages/{mid}",
                                              params={"address": addr},
                                              headers=headers, timeout=20).json()
                            full = (rd.get("data") or {}).get("message") or rd.get("data") or {}
                            blob2 = " ".join(str(full.get(k) or "") for k in
                                             ("subject", "text", "html", "body"))
                            links = _harvest_links(blob2)
                        except Exception:
                            pass
                if links:
                    return _click_verify_link(links[0], int(time.time() - start))
        except Exception:
            pass
        time.sleep(6)
    return False


def verify_email(email_token, max_wait=300):
    """轮询收件箱并点掉验证链接。
    provider 由 token 前缀区分："cf:"（Cloudflare 自建）/ "yyds:"（YYDS Mail）/ 其他=tempmail.lol。
    邮件投递有波动（实测 7s~数分钟），所以默认给 5 分钟。
    返回 True 表示链接被成功访问（HTTP < 400）。"""
    if email_token.startswith("cf:"):
        return _verify_cfmail(email_token[3:], max_wait)
    if email_token.startswith("yyds:"):
        return _verify_yyds(email_token[5:], max_wait)

    start = time.time()
    while time.time() - start < max_wait:
        try:
            r = requests.get(f"https://api.tempmail.lol/v2/inbox?token={email_token}", timeout=15)
            for em in r.json().get("emails", []):
                blob = (em.get("body") or "") + (em.get("html") or "")
                links = _harvest_links(blob)
                if links:
                    return _click_verify_link(links[0], int(time.time() - start))
        except Exception:
            pass
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
    # 优先用自己提供的邮箱/密码（env TH_EMAIL / TH_PASSWORD）——单账号正常注册用这个。
    # 未提供时才退回临时邮箱（tempmail），此时才能自动收验证邮件。
    env_email = os.environ.get("TH_EMAIL", "").strip()
    env_pwd = os.environ.get("TH_PASSWORD", "").strip()
    if env_email and env_pwd:
        email, etok, pwd = env_email, "", env_pwd
        log(f"Email (manual): {email}")
    else:
        email, etok = gen_email()
        pwd = ''.join(random.choices(string.ascii_letters + string.digits, k=12)) + '!Aa1'
        log(f"Email: {email}")
    s = requests.Session(); s.headers.update({"User-Agent": UA})
    for a in range(3):
        try: s.get(f"{BASE}/login", proxies=SOCKS, timeout=40); break
        except Exception: time.sleep(4)
    body, headers, fp = make_signup_body(email, pwd)
    need_captcha = signup_precheck(s, fp)
    if need_captcha:
        return None, "captcha required (Cloudflare Turnstile) — ganti node"
    r = None
    for a in range(3):
        try:
            r = s.post(f"{BASE}/login", data=body, headers=headers, proxies=SOCKS, timeout=50); break
        except Exception as e:
            log(f"  retry {a+1}: {str(e)[:40]}")
            time.sleep(5)
    if r is None or "signedIn" not in r.text:
        errs = flight_errors(getattr(r, 'text', '') or '')
        err = errs[0] if errs else "http/network fail"
        if "too many sign-ups" in err.lower():
            log("  → 该出口注册额度用尽（网络级限流）")
        elif "couldn't create" in err.lower() or "human check" in err.lower():
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
    if etok:
        log("  Verify email (120s)...")
        verified = verify_email(etok)
        log(f"  Verified: {'Y' if verified else 'N'}")
    else:
        verified = None
        log("  Verify email: 跳过（用了自己的邮箱，请到收件箱手动点验证链接）")
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
    """测 key 是否可用。返回 (ok, info)。
    info 带错误码，便于区分是 key 问题还是出口地区问题（region_blocked）。"""
    try:
        r = requests.post(f"{BASE}/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            timeout=50, json={"model": "mimo-v2.5:free", "messages": [{"role": "user", "content": "say ok"}], "max_tokens": 50})
        if r.status_code == 200:
            return True, "200"
        code = ""
        try:
            code = r.json().get("error", {}).get("code") or ""
        except Exception:
            pass
        return False, f"{r.status_code}" + (f" {code}" if code else "")
    except Exception as e:
        return False, f"ERR {type(e).__name__}"

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