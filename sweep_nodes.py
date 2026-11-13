#!/usr/bin/env python3
"""sweep_nodes.py — 批量注册（流水线并行）

用法:  python3 sweep_nodes.py [数量] [工作线程数]
       默认 1 个账号 / 2 个工作线程

设计：切节点是全局独占的（mihomo 只有一个 selector，Match 规则全指向它），
所以注册必须串行；但验证邮件轮询最长 300s 才是瓶颈 —— 主线程注册完立刻
把账号丢给工作线程池，验证/测 key 并行跑。
"""
import requests, re, json, random, string, uuid, time, subprocess, sys
import threading, queue
import urllib.parse
import th_auto_register as tar

tar.SOCKS = {}
BASE = tar.BASE
UA = tar.UA
ROUTER = tar.ROUTER
SAVE_LOCK = threading.Lock()

SIGNUP_ID = tar.ACTION_ID
ACTION_KEY = tar.ACTION_KEY
SOCK = "/var/folders/13/mjjw8v4s63qdq4xy9051f9700000gp/T/verge-mihomo.sock"
SECRET = "set-your-secret"
GROUP = urllib.parse.quote("良心云")

# 宝可梦组节点（路由 Match -> 🐟 漏网之鱼 -> 宝可梦）
#
# 香港/澳门节点已全部移除 —— 实测 signup 返回 "Your IP ... is not supported"，
# 而且 tokenharbor 对 /v1 API 也做地区封锁（region_blocked 明确列出
# "Mainland China, Hong Kong and Macau"）。留着只会浪费请求。
# 不通/超时的节点由 node_state.json 自动记录并冷却，不用手写进列表。
NODES = [
    # 良心云组成员（SG/JP 优先；HK/MO/CN 地区封锁已排除）
    "🇸🇬新加坡高速01|BGP|CTCUCM",
    "🇸🇬新加坡高速02|BGP|CTCUCM",
    "🇸🇬新加坡高速03|BGP|CTCUCM",
    "🇸🇬新加坡高速04|BGP|CTCUCM",
    "🇸🇬新加坡高速05|BGP|CTCUCM",
    "🇸🇬新加坡高速06|BGP|CTCU",
    "🇸🇬新加坡高速07|BGP|CTCU",
    "🇸🇬新加坡高速08|BGP|CTCU",
    "🇸🇬新加坡专线01|BGP|流媒体",
    "🇸🇬新加坡专线02|BGP|流媒体",
    "🇸🇬新加坡专线03|BGP|流媒体",
    "🇸🇬新加坡专线04|BGP|流媒体",
    "🇯🇵日本高速01|CTCU|0.5x",
    "🇯🇵日本高速02|CTCU|0.5x",
    "🇯🇵日本高速01|BGP|CUCM",
    "🇯🇵日本高速02|BGP|CUCM",
    "🇯🇵日本高速03|BGP|CUCM",
    "🇯🇵日本高速04|BGP|CUCM",
    "🇯🇵日本专线01|BGP|流媒体",
    "🇯🇵日本专线02|BGP|流媒体",
    "🇺🇸美国高速01|CTCU|0.1x",
    "🇺🇸美国高速02|CTCU|0.1x",
    "🇺🇸美国高速03|CTCU|0.1x",
    "🇺🇸美国洛杉矶01|流媒体|0.01x",
    "🇺🇸美国01|流媒体|0.1x",
]


def switch_node(node):
    quoted = urllib.parse.quote(node)
    subprocess.run(
        ["curl", "-s", "-X", "PUT", "--unix-socket", SOCK,
         "-H", f"Authorization: Bearer {SECRET}", "-H", "Content-Type: application/json",
         "-d", json.dumps({"name": node}), f"http://localhost/proxies/{GROUP}"],
        capture_output=True, timeout=15)
    time.sleep(3)


def current_ip():
    for _ in range(2):
        try:
            return requests.get("https://api.ipify.org", timeout=15).text
        except Exception:
            time.sleep(2)
    return "?"


# 实测：这些域名收不到 tokenharbor 的验证邮件（注册成功但邮件永不投递 → key 403）。
# 其他域名（nexushint.com / pointnexus.org / barconic.org）10/10 全部验证成功。
BAD_EMAIL_DOMAINS = ("lowdn.com",)


def make_email():
    """创建可收信临时邮箱。域名过滤 + 429 退避逻辑都在 tar.gen_email 里。"""
    try:
        return tar.gen_email()
    except Exception as e:
        print(f"    → 临时邮箱创建失败: {str(e)[:70]}")
        return None, None


# --- 节点状态缓存：别重复试已知被限的节点 ---
# 站点限额判定是建号前最后一道关卡，没有"不建号就能探测"的便宜信号
# （参数校验、重复邮箱校验都在它前面），所以唯一办法就是真发一次 signup。
# 那就把结果记下来，下次直接跳过还在冷却期的节点。
STATE_FILE = "node_state.json"
COOLDOWN_LIMITED = 3600      # 服务端提示 "try again in an hour"
COOLDOWN_CAPTCHA = 1800      # 被要求过 Cloudflare Turnstile
COOLDOWN_BLOCKED = 86400     # 出口国家不支持，基本不会变
COOLDOWN_UNREACHABLE = 900   # 节点不通/超时，15 分钟后允许重试一次


def load_state():
    try:
        return json.load(open(STATE_FILE))
    except Exception:
        return {}


def save_state(st):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(st, f, indent=1, ensure_ascii=False)
    except Exception:
        pass


def cooldown_left(st, node):
    """该节点还需冷却的秒数（0 = 可以试）"""
    e = st.get(node) or {}
    return max(0, int(max(e.get("limited_until", 0), e.get("captcha_until", 0),
                          e.get("blocked_until", 0), e.get("unreachable_until", 0))
                      - time.time()))


def mark(st, node, reason):
    """记录节点状态。reason: limited / captcha / blocked / unreachable / ok"""
    e = st.setdefault(node, {})
    now = time.time()
    if reason == "limited":
        e["limited_until"] = now + COOLDOWN_LIMITED
    elif reason == "captcha":
        e["captcha_until"] = now + COOLDOWN_CAPTCHA
    elif reason == "blocked":
        e["blocked_until"] = now + COOLDOWN_BLOCKED
    elif reason == "unreachable":
        e["unreachable_until"] = now + COOLDOWN_UNREACHABLE
    elif reason == "ok":
        e.pop("limited_until", None)
        e.pop("captcha_until", None)
        e.pop("unreachable_until", None)
    e["last_reason"] = reason
    e["last_seen"] = int(now)


# 出口国家被 tokenharbor 拒的地区（signup 和 /v1 API 都拒）
BLOCKED_REGION_FLAGS = ("🇭🇰", "🇲🇴", "🇨🇳")


def is_blocked_region(node):
    return any(f in node for f in BLOCKED_REGION_FLAGS)


def boundary():
    return "----WebKitFormBoundary" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=16))


def tz_for(node):
    for flag, tz in (("🇭🇰", "Asia/Hong_Kong"), ("🇸🇬", "Asia/Singapore"),
                     ("🇯🇵", "Asia/Tokyo"), ("🇺🇸", "America/Los_Angeles"),
                     ("🇬🇧", "Europe/London"), ("🇨🇳", "Asia/Taipei")):
        if flag in node:
            return tz
    return "Asia/Singapore"


def signup(s, email, pwd, tz="Asia/Singapore"):
    fp = str(uuid.uuid4())
    bd = boundary()
    fields = [
        ("_1_$ACTION_REF_1", ""),
        ("_1_$ACTION_1:0", json.dumps({"id": SIGNUP_ID, "bound": "$@1"}, separators=(",", ":"))),
        ("_1_$ACTION_1:1", '["$undefined"]'),
        ("_1_$ACTION_KEY", ACTION_KEY),
        ("_1_device_fingerprint", fp), ("_1_timezone", tz), ("_1_next", ""),
        ("_1_email", email), ("_1_password", pwd), ("_1_invite_code", ""),
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
    # 必须先登记 fp，否则服务端 signUp 直接 500
    need_captcha = tar.signup_precheck(s, fp)
    if need_captcha:
        return False, None, ["captcha required"], True
    r = s.post(f"{BASE}/login", data=body, headers=headers, timeout=50)
    ok = "signedIn" in r.text
    return ok, r, tar.flight_errors(r.text), False


def save_account(acct):
    """落盘：完整记录 jsonl + 便于取用的纯文本 key 清单（加锁，多线程安全）。"""
    with SAVE_LOCK:
        with open("th_direct_results.jsonl", "a") as f:
            f.write(json.dumps(acct) + "\n")
        if acct.get("api_key"):
            with open("th_keys.txt", "a") as f:
                f.write(f"{acct['email']}\t{acct['password']}\t{acct['api_key']}"
                        f"\tverified={acct.get('verified')}\ttest={acct.get('test')}\n")
    print("    saved -> th_direct_results.jsonl / th_keys.txt")


def finalize(s, acct, etok):
    """建 key + 开 free models + 验证邮箱 + 测 key + 落盘。
    不碰代理节点，可以多线程并行。返回 acct。"""
    tag = acct["email"].split("@")[0]
    try:
        r2 = s.get(f"{BASE}/api/keys", headers={"Accept": "application/json"}, timeout=30)
        for k in r2.json().get("keys", []):
            try: s.delete(f"{BASE}/api/keys/{k['id']}", timeout=15)
            except Exception: pass
    except Exception:
        pass
    r3 = s.post(f"{BASE}/api/keys", json={"label": f"th-{random.randint(100,999)}"},
                headers={"Accept": "application/json", "Content-Type": "application/json"}, timeout=40)
    if r3.status_code != 201:
        print(f"    [{tag}] KEY CREATE FAIL: {r3.status_code}")
        save_account(acct)
        return acct
    key = r3.json().get("plaintext")
    acct["api_key"] = key
    print(f"    [{tag}] Key: {key[:30]}...")
    try:
        rc = s.post(f"{BASE}/api/me/privacy", json={"free_models_enabled": True},
                    headers={"Accept": "application/json", "Content-Type": "application/json"}, timeout=30)
        acct["consent"] = rc.status_code == 200 and '"ok":true' in rc.text
    except Exception:
        acct["consent"] = False
    print(f"    [{tag}] Free models: {'Y' if acct['consent'] else 'N'}")
    # 验证必须在模型测试之前——未验证时 /v1 一律 403 email_verification_required
    print(f"    [{tag}] Verify email (max 300s)...")
    acct["verified"] = tar.verify_email(etok)
    print(f"    [{tag}] Verified: {'Y' if acct['verified'] else 'N'}")
    okc, info = tar.test_model(key)
    # 注册与验证是并行的，测 key 时出口可能刚好落在被封锁的地区（region_blocked）。
    # 那不是 key 的问题 —— 重试几次，等主线程把节点换到别处。
    for attempt in range(4):
        if okc or "region_blocked" not in info:
            break
        print(f"    [{tag}] 出口地区被封锁（region_blocked），15s 后重试 {attempt+1}/4")
        time.sleep(15)
        okc, info = tar.test_model(key)
    acct["test"] = info
    print(f"    [{tag}] Model test: {'OK' if okc else 'FAIL'} ({info})")
    save_account(acct)
    return acct


def register_only(node, ip):
    """切节点后的注册动作（不含验证）。
    返回 (job, reason)：成功 job=(session, acct, etok) 且 reason="ok"；
    失败 job=None，reason ∈ {limited, captcha, blocked, network}。"""
    email, etok = make_email()
    if not email:
        print("    → tempmail 创建失败，跳过")
        return None, "network"
    print(f"    email: {email}")
    pwd = ''.join(random.choices(string.ascii_letters + string.digits, k=12)) + '!Aa1'
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    try:
        s.get(f"{BASE}/login", timeout=40)
        ok, r, errs, captcha = signup(s, email, pwd, tz_for(node))
    except Exception as e:
        print(f"    EXC: {type(e).__name__} {str(e)[:70]}")
        return None, "unreachable"
    print(f"    signedIn={ok} errors={errs[:2]}")
    if captcha:
        print("    → 该出口需要 Cloudflare Turnstile，换下一节点")
        return None, "captcha"
    if not ok:
        blob = " | ".join(errs).lower()
        if "too many sign-ups" in blob:
            print("    → 该出口注册额度用尽，换下一节点")
            return None, "limited"
        if "not supported" in blob:
            print("    → 该出口国家/邮箱域被拒")
            return None, "blocked"
        if "couldn't create" in blob:
            print("    → 风控标记")
            return None, "limited"
        if "invite code" in blob:
            print("    → 站点已改为邀请制")
            return None, "blocked"
        if "a bit fast" in blob:
            print("    → 触发频率限制")
            return None, "limited"
        return None, "network"
    uid = re.findall(r'"userId":\s*"([^"]+)"', r.text)
    acct = {"email": email, "password": pwd, "userId": uid[0] if uid else "",
            "node": node, "ip": ip}
    print(f"    ✓ SIGNUP OK userId={acct['userId']}")
    return (s, acct, etok), "ok"


def main(target=1, n_workers=2):
    done = []
    done_lock = threading.Lock()
    jobs = queue.Queue()

    def worker(wid):
        while True:
            job = jobs.get()
            try:
                if job is None:
                    return
                s, acct, etok = job
                finalize(s, acct, etok)
                if acct.get("verified"):
                    with done_lock:
                        done.append(acct)
                    print(f"  ★ 有效账号 {len(done)}/{target}: {acct['email']} "
                          f"(test={acct.get('test')})")
                else:
                    print(f"  ✗ {acct['email']} 邮箱未验证，不计入目标（key 会 403）")
            except Exception as e:
                print(f"  [w{wid}] EXC {type(e).__name__}: {str(e)[:70]}")
            finally:
                jobs.task_done()

    threads = [threading.Thread(target=worker, args=(i + 1,), daemon=True)
               for i in range(n_workers)]
    for t in threads:
        t.start()
    print(f"=== 目标 {target} 个账号 | 工作线程 {n_workers} | 节点 {len(NODES)} 个 ===")

    MAX_PASSES = 4
    for p in range(1, MAX_PASSES + 1):
        with done_lock:
            if len(done) >= target:
                break
        # 在途任务已够目标 → 直接等它们跑完，不必再开新的一轮
        if len(done) + jobs.unfinished_tasks >= target:
            break
        if p > 1:
            print(f"\n{'#'*68}\n### PASS {p} — 已得 {len(done)}/{target}，冷却 60s 后重扫\n{'#'*68}")
            time.sleep(60)
        seen_ips = set()
        state = load_state()
        skipped_cd = 0
        skipped_region = 0
        for i, node in enumerate(NODES, 1):
            with done_lock:
                if len(done) >= target:
                    break
            # 已完成的 + 在途的 >= 目标就不再注册（在途的交给工作线程）
            if len(done) + jobs.unfinished_tasks >= target:
                break
            # 封锁地区直接跳过（signup 和 /v1 都拒，留着白费请求）
            if is_blocked_region(node):
                skipped_region += 1
                print(f"\n=== pass{p} node[{i}/{len(NODES)}] {node}")
                print("    → 出口地区被 tokenharbor 封锁（HK/MO/CN），跳过")
                continue
            left = cooldown_left(state, node)
            if left > 0:
                skipped_cd += 1
                print(f"\n=== pass{p} node[{i}/{len(NODES)}] {node}")
                print(f"    → 缓存命中：{left//60}m{left%60}s 前记录为"
                      f"「{state[node].get('last_reason')}」，跳过")
                continue
            print(f"\n=== pass{p} node[{i}/{len(NODES)}] {node} "
                  f"(完成 {len(done)}/{target}, 在途 {jobs.unfinished_tasks})")
            switch_node(node)
            ip = current_ip()
            print(f"    egress: {ip}")
            if ip == "?":
                print("    → 节点不通/超时，记入缓存跳过")
                mark(state, node, "unreachable")
                save_state(state)
                continue
            if ip in seen_ips:
                print("    → 重复出口 IP，跳过")
                continue
            seen_ips.add(ip)
            job, reason = register_only(node, ip)
            if job:
                jobs.put(job)
                mark(state, node, "ok")
                save_state(state)
            else:
                if reason in ("limited", "captcha", "blocked", "unreachable"):
                    mark(state, node, reason)
                    save_state(state)
                time.sleep(random.randint(3, 8))
        if skipped_cd or skipped_region:
            print(f"\n  （本轮跳过 {skipped_cd} 个冷却中 + {skipped_region} 个封锁地区节点，未浪费请求）")

    print("\n=== 等待在途任务完成验证... ===")
    jobs.join()
    for _ in threads:
        jobs.put(None)
    for t in threads:
        t.join(timeout=10)

    print(f"\n{'='*68}")
    print(f"=== DONE: {len(done)}/{target} 个有效账号（已验证 + key 可用）===")
    for a in done:
        print(f"  {a['email']}  test={a.get('test')}  node={a['node']}")
    if len(done) < target:
        print(f"  ⚠ 差 {target - len(done)} 个 — 出口额度耗尽，等 1h 或换代理再跑")
    print("  结果已追加到 th_direct_results.jsonl / th_keys.txt")
    return done


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a.isdigit()]
    n_target = int(args[0]) if len(args) > 0 else 1
    n_workers = int(args[1]) if len(args) > 1 else 2
    main(n_target, n_workers)
