#!/usr/bin/env python3
"""proxy_register.py — 走外部代理池的 TokenHarbor 注册器（不用 Tor）

用法:
  .venv/bin/python -u proxy_register.py [目标数] [线程数] [代理文件]

流程: 每个代理 = 独立出口 IP → precheck + signup（经代理）→
      建 key + consent（经代理）→ CF 邮箱验证（直连）→ 测 key（直连）
产出: th_keys_proxy_<时间戳>.txt / .jsonl（实时落盘）
"""
import os, sys, json, time, random, string, threading, queue, uuid
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import th_auto_register as tar

TARGET   = int(sys.argv[1]) if len(sys.argv) > 1 else 86
THREADS  = int(sys.argv[2]) if len(sys.argv) > 2 else 8
PFILE    = sys.argv[3] if len(sys.argv) > 3 else "proxies/free_proxy_live_20261017.txt"
PASSES   = 2                                 # 代理列表最多轮几遍

STAMP   = time.strftime("%Y%m%d-%H%M%S")
TXT_OUT = f"th_keys_proxy_{STAMP}.txt"
JSON_OUT= f"th_keys_proxy_{STAMP}.jsonl"

_lock = threading.Lock()
n_all, n_valid = [0], [0]
bad_proxies = set()                          # 硬失败（被封/地区拒）不再用
stop = threading.Event()


def load_proxies():
    lines = [l.strip() for l in open(PFILE) if l.strip()]
    random.shuffle(lines)
    return lines


def classify(resp_text, exc=None):
    """返回 (是否硬失败, 标签)"""
    t = (resp_text or "") + (str(exc) if exc else "")
    low = t.lower()
    if "region" in low or "not supported" in low:
        return True, "region_blocked"
    if "too many sign-ups" in low:
        return True, "network_quota"
    if "human check" in low or "captcha" in low:
        return True, "captcha"
    if exc and isinstance(exc, (requests.exceptions.ConnectTimeout,
                                requests.exceptions.ReadTimeout,
                                requests.exceptions.ConnectionError,
                                requests.exceptions.ProxyError)):
        return False, f"conn:{type(exc).__name__}"   # 软失败，换下家
    return False, (tar.flight_errors(resp_text) or ["?"])[0][:40] if not exc else f"EXC:{type(exc).__name__}"


def register_via_proxy(purl, wid, n):
    fp = str(uuid.uuid4())
    email, etok = tar.gen_email()
    pwd = ''.join(random.choices(string.ascii_letters + string.digits, k=12)) + '!Aa1'
    s = requests.Session()
    s.headers.update({"User-Agent": tar.UA})
    s.proxies = {"http": purl, "https": purl}

    # precheck（经代理）
    try:
        cap = s.get(f"{tar.BASE}/api/auth/signup-precheck?fp={fp}",
                    headers={"Accept": "application/json"}, timeout=25)
        if cap.json().get("needCaptcha"):
            return False, "captcha", None
    except Exception as e:
        hard, tag = classify("", e)
        return hard, tag, None

    body, headers, fp = tar.make_signup_body(email, pwd, fp)
    try:
        r = s.post(f"{tar.BASE}/login", data=body, headers=headers, timeout=45)
    except Exception as e:
        hard, tag = classify("", e)
        return hard, tag, None
    if "signedIn" not in r.text:
        hard, tag = classify(r.text)
        return hard, tag, None

    acct = {"email": email, "password": pwd,
            "userId": (re.findall(r'"userId":\s*"([^"]+)"', r.text) or [""])[0],
            "node": f"proxy:{purl}", "ip": purl.split("//")[-1]}

    # 建 key + consent（经代理）
    try:
        r3 = s.post(f"{tar.BASE}/api/keys", json={"label": f"th-px-{random.randint(100,999)}"},
                    headers={"Accept": "application/json", "Content-Type": "application/json"}, timeout=40)
        if r3.status_code != 201:
            hard, tag = classify(r3.text)
            return hard, tag, None
        acct["api_key"] = r3.json().get("plaintext")
        rc = s.post(f"{tar.BASE}/api/me/privacy", json={"free_models_enabled": True}, timeout=30)
        acct["consent"] = rc.status_code == 200 and '"ok":true' in rc.text
    except Exception as e:
        hard, tag = classify("", e)
        return hard, tag, None

    # 验证邮箱（直连 CF worker）+ 测 key（直连）
    acct["verified"] = tar.verify_email(etok)
    okc, info = tar.test_model(acct["api_key"])
    acct["test"] = info
    return True, "ok", acct


import re  # noqa: E402  (signup userId 提取用)


def main():
    print(f"=== 代理池注册 | 目标 {TARGET} | 线程 {THREADS} | 代理文件 {PFILE} ===", flush=True)
    print(f"    导出 → {TXT_OUT} / {JSON_OUT}", flush=True)
    allp = load_proxies()
    print(f"代理加载: {len(allp)} 条", flush=True)

    q = queue.Queue()
    for p in allp:
        q.put(p)

    def worker(wid):
        while not stop.is_set():
            with _lock:
                if n_valid[0] >= TARGET:
                    stop.set(); return
            try:
                p = q.get_nowait()
            except queue.Empty:
                return
            if p in bad_proxies:
                continue
            t0 = time.time()
            try:
                ok, tag, acct = register_via_proxy(p, wid, 0)
            except Exception as e:
                ok, tag, acct = False, f"EXC:{type(e).__name__}", None
            dt = time.time() - t0
            with _lock:
                if ok and acct:
                    n_all[0] += 1
                    valid = acct.get("verified") and acct.get("test") == "200"
                    if valid: n_valid[0] += 1
                    with open(JSON_OUT, "a") as f:
                        f.write(json.dumps(acct, ensure_ascii=False) + "\n")
                    if valid:
                        with open(TXT_OUT, "a") as f:
                            f.write(f"{acct['email']}\t{acct['password']}\t{acct['api_key']}"
                                    f"\tverified={acct.get('verified')}\ttest={acct.get('test')}\n")
                    print(f"  [w{wid}] {p:28} ✓ {'有效' if valid else '注册但未可用'} "
                          f"({n_valid[0]}/{TARGET}) {dt:.0f}s", flush=True)
                else:
                    if tag in ("region_blocked", "network_quota", "captcha"):
                        bad_proxies.add(p)
                    print(f"  [w{wid}] {p:28} ✗ {tag} {dt:.0f}s", flush=True)

    ts = [threading.Thread(target=worker, args=(i + 1,), daemon=True) for i in range(THREADS)]
    for t in ts: t.start()
    t0 = time.time()
    for t in ts: t.join()

    print(f"\n=== DONE: 有效 {n_valid[0]} / 注册成功 {n_all[0]} | 耗时 {(time.time()-t0)/60:.1f} 分钟 ===", flush=True)
    print(f"  有效 key → {TXT_OUT}", flush=True)
    print(f"  全量记录 → {JSON_OUT}", flush=True)


if __name__ == "__main__":
    main()
