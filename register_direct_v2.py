#!/usr/bin/env python3
"""register_direct_v2.py — 直连注册（新版 Next.js action 协议）
Tor 在当前网络不可用；本脚本用直连 + 新提取的 action id。
变体 A-D 依次尝试，成功后继续: cleanup keys → create key → consent → verify → test → inject
"""
import requests, re, json, random, string, uuid, time, sys
import th_auto_register as tar

tar.SOCKS = {}  # 全程直连

BASE = tar.BASE
UA = tar.UA
ROUTER = tar.ROUTER

SIGNUP_ID = "601deb87587212e262e9f8b231f1cf29b6a227a0ad"   # signUp (当前 build)
SIGNIN_ID = "607164493180ce5d8eb93f6872116b22c3f4433e02"   # signIn (当前 build)
ACTION_KEY = "kb3df6a1d48e1a01307dd1ff043d81c6e"           # 未变


def boundary():
    return "----WebKitFormBoundary" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=16))


def mp(fields, bd):
    parts = []
    for n, v in fields:
        parts.append(f'--{bd}\r\nContent-Disposition: form-data; name="{n}"\r\n\r\n{v}')
    return "\r\n".join(parts) + f"\r\n--{bd}--\r\n"


def base_headers(bd):
    return {
        "Content-Type": f"multipart/form-data; boundary={bd}",
        "Accept": "text/x-component",
        "Next-Router-State-Tree": ROUTER,
        "Origin": BASE, "Referer": f"{BASE}/login",
    }


def try_variant(name, s, email, pwd):
    fp = str(uuid.uuid4())
    bd = boundary()

    if name in ("A", "B"):  # 无前缀（当前 SSR 形态）
        fields = [
            ("$ACTION_REF_1", ""),
            ("$ACTION_1:0", json.dumps({"id": SIGNUP_ID, "bound": "$@1"})),
            ("$ACTION_1:1", '["$undefined"]'),
            ("$ACTION_KEY", ACTION_KEY),
            ("device_fingerprint", fp),
            ("timezone", ""),
            ("next", ""),
            ("email", email), ("password", pwd), ("invite_code", ""),
        ]
    else:  # C/D: 老形态 1_ 前缀
        fields = [
            ("1_$ACTION_REF_1", ""),
            ("1_$ACTION_1:0", json.dumps({"id": (SIGNUP_ID if name == "C" else SIGNIN_ID), "bound": "$@1"})),
            ("1_$ACTION_1:1", '["$undefined"]'),
            ("1_$ACTION_KEY", ACTION_KEY),
            ("1_device_fingerprint", fp), ("1_timezone", ""), ("1_next", ""),
            ("1_email", email), ("1_password", pwd), ("1_invite_code", ""),
            ("0", '["$undefined","$K1"]'),
        ]

    headers = base_headers(bd)
    if name != "A":  # A 无 Next-Action 头（纯 no-JS POST）
        headers["Next-Action"] = SIGNUP_ID

    # 先登记 fp：跳过这步服务端 signUp 一律 500
    if tar.signup_precheck(s, fp):
        print(f"  [{name}] needCaptcha=true（该出口需 Turnstile），跳过")
        return False, None

    body = mp(fields, bd)
    r = s.post(f"{BASE}/login", data=body, headers=headers, timeout=50)
    ok = "signedIn" in r.text
    errs = tar.flight_errors(r.text)
    print(f"  [{name}] status={r.status_code} signedIn={ok} errors={errs[:2]} len={len(r.text)}")
    if not ok and r.status_code not in (200,):
        print(f"       body[:160]: {r.text[:160]!r}")
    return ok, r


def full_flow():
    email, etok = tar.gen_email()
    pwd = ''.join(random.choices(string.ascii_letters + string.digits, k=12)) + '!Aa1'
    print(f"Email: {email}")
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    s.get(f"{BASE}/login", timeout=40)

    acct = None
    for v in ("A", "B", "C", "D"):
        try:
            ok, r = try_variant(v, s, email, pwd)
        except Exception as e:
            print(f"  [{v}] EXC {type(e).__name__}: {str(e)[:80]}")
            continue
        if ok:
            uid = re.findall(r'"userId":\s*"([^"]+)"', r.text)
            print(f"  SIGNUP OK via variant {v}, userId={uid[0] if uid else '?'}")
            acct = {"email": email, "password": pwd, "userId": uid[0] if uid else "", "variant": v}
            break
        time.sleep(2)

    if not acct:
        print("ALL VARIANTS FAILED")
        return

    # cleanup auto keys
    try:
        r2 = s.get(f"{BASE}/api/keys", headers={"Accept": "application/json"}, timeout=30)
        for k in r2.json().get("keys", []):
            try:
                s.delete(f"{BASE}/api/keys/{k['id']}", timeout=15)
            except Exception:
                pass
    except Exception as e:
        print(f"  cleanup keys warn: {str(e)[:60]}")

    # create key
    r3 = s.post(f"{BASE}/api/keys", json={"label": f"th-{random.randint(100,999)}"},
                headers={"Accept": "application/json", "Content-Type": "application/json"}, timeout=40)
    if r3.status_code != 201:
        print(f"KEY CREATE FAIL: {r3.status_code} {r3.text[:120]}")
        return
    key = r3.json().get("plaintext")
    acct["api_key"] = key
    print(f"  Key: {key[:35]}...")

    # consent free models
    rc = s.post(f"{BASE}/api/me/privacy", json={"free_models_enabled": True},
                headers={"Accept": "application/json", "Content-Type": "application/json"}, timeout=30)
    consent = rc.status_code == 200 and '"ok":true' in rc.text
    acct["consent"] = consent
    print(f"  Free models consent: {'Y' if consent else 'N'} ({rc.status_code})")

    # verify email
    print("  Verify email (max 300s)...")
    verified = tar.verify_email(etok)
    acct["verified"] = verified
    print(f"  Verified: {'Y' if verified else 'N'}")

    # test model
    ok, info = tar.test_model(key)
    acct["test"] = info
    print(f"  Model test: {'OK' if ok else 'FAIL'} ({info})")

    # inject 9router（DB 不在则优雅失败）
    inj, imsg = tar.inject_9router(key, email)
    print(f"  Inject 9Router: {'OK' if inj else 'SKIP/FAIL'} {imsg}")

    with open("th_direct_results.jsonl", "a") as f:
        f.write(json.dumps(acct) + "\n")
    print("\n=== RESULT ===")
    for k, v in acct.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    full_flow()
