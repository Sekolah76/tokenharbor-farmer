import os, json, random, string, time, requests
os.environ['TOR_AUTOSTART'] = '0'
import th_auto_register as tar

BASE, UA = tar.BASE, tar.UA
SIGNUP_ID, ACTION_KEY, ROUTER = tar.ACTION_ID, tar.ACTION_KEY, tar.ROUTER
P = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}

def try_variant(name, extra=(), drop=(), pre_login=False, tz="Asia/Singapore"):
    fp = "ua-" + ''.join(random.choices(string.ascii_lowercase+string.digits, k=11)) + format(int(time.time()*1000), "x")
    bd = "----WebKitFormBoundary" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=16))
    email, _ = tar.gen_email()
    pwd = "TestPwd123!Aa1"
    base = [("$ACTION_REF_1",""),
            ("$ACTION_1:0", json.dumps({"id": SIGNUP_ID, "bound": "$@1"}, separators=(",",":"))),
            ("$ACTION_1:1", '["$undefined"]'),
            ("$ACTION_KEY", ACTION_KEY),
            ("device_fingerprint", fp), ("timezone", tz), ("next", ""),
            ("email", email), ("password", pwd)]
    fields = [x for x in base if x[0] not in drop] + list(extra)
    parts = [f'--{bd}\r\nContent-Disposition: form-data; name="{n}"\r\n\r\n{v}' for n, v in fields]
    body = "\r\n".join(parts) + f"\r\n--{bd}--\r\n"
    hdrs = {"Content-Type": f"multipart/form-data; boundary={bd}", "Accept": "text/x-component",
            "Next-Action": SIGNUP_ID, "Next-Router-State-Tree": ROUTER,
            "Origin": BASE, "Referer": f"{BASE}/login"}
    s = requests.Session(); s.headers.update({"User-Agent": UA})
    if pre_login:
        s.get(f"{BASE}/login", proxies=P, timeout=25)
    try:
        s.get(f"{BASE}/api/auth/signup-precheck?fp={fp}", headers={"Accept":"application/json"}, proxies=P, timeout=25)
    except Exception:
        pass
    r = s.post(f"{BASE}/login", data=body, headers=hdrs, proxies=P, timeout=40)
    txt = r.text.replace("\n", " ")
    print(f"[{name:38}] {r.status_code} | {txt[:150]}")
    return r.text

print("=== 变体矩阵（mihomo 出口，直连迭代）===")
try_variant("baseline")
time.sleep(3)
try_variant("+ cf-turnstile-response=dummy", extra=[("cf-turnstile-response","dummy-token-xxx")])
time.sleep(3)
try_variant("+ invite_code 空", extra=[("invite_code","")])
time.sleep(3)
try_variant("+ '0' 旧字段", extra=[("0", '["$undefined","$K1"]')])
time.sleep(3)
try_variant("pre_login 先GET /login", pre_login=True)
