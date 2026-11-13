#!/usr/bin/env python3
"""farm_rotate_nodes.py — 节点轮换版注册（等价 Tor NEWNYM 轮换）
按节点列表切换 Clash 出口 → 每节点试一次 C 变体注册 → 成功后完成全流程
"""
import requests, re, json, random, string, uuid, time, subprocess, sys
import urllib.parse
import th_auto_register as tar

tar.SOCKS = {}
BASE = tar.BASE
UA = tar.UA
ROUTER = tar.ROUTER

SIGNUP_ID = "601deb87587212e262e9f8b231f1cf29b6a227a0ad"
ACTION_KEY = "kb3df6a1d48e1a01307dd1ff043d81c6e"
SOCK = "/var/folders/13/mjjw8v4s63qdq4xy9051f9700000gp/T/verge-mihomo.sock"
SECRET = "set-your-secret"
GROUP = urllib.parse.quote("宝可梦")

NODES = [
    "🇺🇸【北美洲】美国02原生丨直连【2x】",
    "🇺🇸【北美洲】美国03原生丨直连【2x】",
    "🇺🇸【北美洲】美国04原生丨直连【2x】",
    "🇺🇸【北美洲】美国01原生丨直连【2x】",  # 回跳（时间过去可能放行）
    "🇯🇵【亚洲】日本01丨Vless",
    "🇯🇵【亚洲】日本02丨Vless",
    "🇸🇬【亚洲】新加坡01丨直连",
    "🇭🇰【亚洲】香港05原生丨移动直连",
]


def switch_node(node):
    quoted = urllib.parse.quote(node)
    r = subprocess.run(
        ["curl", "-s", "-X", "PUT", "--unix-socket", SOCK,
         "-H", f"Authorization: Bearer {SECRET}", "-H", "Content-Type: application/json",
         "-d", json.dumps({"name": node}), f"http://localhost/proxies/{GROUP}"],
        capture_output=True, timeout=15)
    time.sleep(3)


def current_ip():
    try:
        return requests.get("https://api.ipify.org", timeout=20).text
    except Exception:
        return "?"


def boundary():
    return "----WebKitFormBoundary" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=16))


def signup(s, email, pwd):
    fp = str(uuid.uuid4())
    bd = boundary()
    fields = [
        ("1_$ACTION_REF_1", ""),
        ("1_$ACTION_1:0", json.dumps({"id": SIGNUP_ID, "bound": "$@1"})),
        ("1_$ACTION_1:1", '["$undefined"]'),
        ("1_$ACTION_KEY", ACTION_KEY),
        ("1_device_fingerprint", fp), ("1_timezone", ""), ("1_next", ""),
        ("1_email", email), ("1_password", pwd), ("1_invite_code", ""),
        ("0", '["$undefined","$K1"]'),
    ]
    parts = [f'--{bd}\r\nContent-Disposition: form-data; name="{n}"\r\n\r\n{v}' for n, v in fields]
    body = "\r\n".join(parts) + f"\r\n--{bd}--\r\n"
    headers = {
        "Content-Type": f"multipart/form-data; boundary={bd}",
        "Accept": "text/x-component",
        "Next-Action": SIGNUP_ID,
        "Next-Router-State-Tree": ROUTER,
        "Origin": BASE, "Referer": f"{BASE}/login",
    }
    # 先登记 fp：跳过这步服务端 signUp 一律 500
    if tar.signup_precheck(s, fp):
        return False, None, ["captcha required"]
    r = s.post(f"{BASE}/login", data=body, headers=headers, timeout=50)
    ok = "signedIn" in r.text
    return ok, r, tar.flight_errors(r.text)


def main():
    for i, node in enumerate(NODES, 1):
        print(f"\n=== [{i}/{len(NODES)}] switch -> {node}")
        switch_node(node)
        ip = current_ip()
        print(f"    egress: {ip}")

        email, etok = tar.gen_email()
        pwd = ''.join(random.choices(string.ascii_letters + string.digits, k=12)) + '!Aa1'
        print(f"    email: {email}")

        s = requests.Session()
        s.headers.update({"User-Agent": UA})
        try:
            s.get(f"{BASE}/login", timeout=40)
        except Exception as e:
            print(f"    GET login fail: {str(e)[:60]}")
            continue

        try:
            ok, r, errs = signup(s, email, pwd)
        except Exception as e:
            print(f"    signup EXC: {type(e).__name__} {str(e)[:60]}")
            continue

        print(f"    signedIn={ok} errors={errs[:1]}")
        if errs and "not supported" in errs[0]:
            print("    → IP 仍被拒，换下一节点")
            continue
        if errs and "couldn't create" in errs[0]:
            print("    → 节点被风控标记，换下一节点")
            continue
        if not ok:
            print("    → 未知错误，换下一节点")
            continue

        uid = re.findall(r'"userId":\s*"([^"]+)"', r.text)
        print(f"    ✓ SIGNUP OK userId={uid[0] if uid else '?'}")
        acct = {"email": email, "password": pwd, "userId": uid[0] if uid else "",
                "node": node, "ip": ip}

        # cleanup auto keys
        try:
            r2 = s.get(f"{BASE}/api/keys", headers={"Accept": "application/json"}, timeout=30)
            for k in r2.json().get("keys", []):
                try: s.delete(f"{BASE}/api/keys/{k['id']}", timeout=15)
                except Exception: pass
        except Exception as e:
            print(f"    cleanup warn: {str(e)[:50]}")

        # create key
        r3 = s.post(f"{BASE}/api/keys", json={"label": f"th-{random.randint(100,999)}"},
                    headers={"Accept": "application/json", "Content-Type": "application/json"}, timeout=40)
        if r3.status_code != 201:
            print(f"    KEY CREATE FAIL: {r3.status_code} {r3.text[:100]}")
            with open("th_direct_results.jsonl", "a") as f: f.write(json.dumps(acct) + "\n")
            return
        key = r3.json().get("plaintext")
        acct["api_key"] = key
        print(f"    Key: {key[:35]}...")

        # consent
        rc = s.post(f"{BASE}/api/me/privacy", json={"free_models_enabled": True},
                    headers={"Accept": "application/json", "Content-Type": "application/json"}, timeout=30)
        consent = rc.status_code == 200 and '"ok":true' in rc.text
        acct["consent"] = consent
        print(f"    Free models: {'Y' if consent else 'N'} ({rc.status_code})")

        # verify email
        print("    Verify email (max 300s)...")
        verified = tar.verify_email(etok)
        acct["verified"] = verified
        print(f"    Verified: {'Y' if verified else 'N'}")

        # test model
        okc, info = tar.test_model(key)
        acct["test"] = info
        print(f"    Model test: {'OK' if okc else 'FAIL'} ({info})")

        # inject（本机无 9router DB → 优雅失败）
        inj, imsg = tar.inject_9router(key, email)
        print(f"    Inject 9Router: {'OK' if inj else 'SKIP'} {imsg}")

        with open("th_direct_results.jsonl", "a") as f:
            f.write(json.dumps(acct) + "\n")
        print("\n=== RESULT ===")
        for k, v in acct.items():
            print(f"  {k}: {v}")
        return

    print("\nALL NODES EXHAUSTED — 每个出口都被拒")


if __name__ == "__main__":
    main()
