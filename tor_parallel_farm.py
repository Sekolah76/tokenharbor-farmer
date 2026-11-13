#!/usr/bin/env python3
"""tor_parallel_farm.py — 并行 Tor 注册农场

为什么能并行（和 mihomo 方案的本质区别）：
  mihomo 只有一个全局 selector，切节点是串行独占的，多线程拿不到不同出口。
  Tor 支持流隔离（stream isolation）：每个 SOCKS 流用不同的用户名/密码，
  就走不同的电路 → 不同出口 IP。所以 N 个线程可以同时持有 N 个出口。

出口成本极低（实测 8 个并发流 2.2s 拿到 7 个不同 IP），
所以用"广撒网"策略：并发狂试，撞到能用的就收下。

Tor 生命周期：
  自动启动**默认关闭** —— 脚本不会主动拉起 Tor。
    · 默认：要求 Tor 已在运行（手动启动），脚本只用不管；没跑就报错提示。
    · TOR_AUTOSTART=1：脚本自己拉起 Tor，注册结束再关掉（独占端口、
      会清掉残留的旧 tor 进程，保证跑完端口一定释放）。

用法:
  python3 tor_parallel_farm.py [数量] [线程数]
  默认 1 个账号 / 8 线程

环境变量:
  TOR_AUTOSTART=1       开启 Tor 自动启停（默认关闭 = 用已在跑的 Tor）
  TOR_BIN               tor 可执行文件（默认 "tor"）
  TOR_UPSTREAM_PROXY    Tor 出网走的代理（默认 127.0.0.1:7890，即 mihomo）
                        设成 "" 则直连（本机直连 Tor 网络通常不通）
  TOR_KEEP_ALIVE=1      自动启动模式下，跑完不关 Tor（调试用）
"""
import requests, re, json, random, string, uuid, time, socket, sys, os
import threading, queue, itertools, subprocess, shutil, tempfile, atexit, signal
import urllib.parse
import th_auto_register as tar

BASE, UA, ROUTER = tar.BASE, tar.UA, tar.ROUTER
SIGNUP_ID, ACTION_KEY = tar.ACTION_ID, tar.ACTION_KEY
TOR_HOST = os.environ.get("TOR_SOCKS_HOST", "127.0.0.1")
TOR_PORT = int(os.environ.get("TOR_SOCKS_PORT", "9050"))
CONTROL = ("127.0.0.1", int(os.environ.get("TOR_CONTROL_PORT", "9051")))
TOR_BIN = os.environ.get("TOR_BIN", "tor")
TOR_UPSTREAM = os.environ.get("TOR_UPSTREAM_PROXY", "127.0.0.1:7890")
TOR_KEEP_ALIVE = os.environ.get("TOR_KEEP_ALIVE", "") == "1"
# 自动启动默认关闭 —— 脚本不会主动拉起 Tor，除非显式 TOR_AUTOSTART=1
TOR_AUTOSTART = os.environ.get("TOR_AUTOSTART", "") == "1"
# 按端口隔离工作目录 —— 这样能同时跑多个 Tor 实例（不同端口），互相不打架
TOR_DIR = os.path.join(tempfile.gettempdir(), f"th-tor-run-{TOR_PORT}")
# 跳过 GET /login（每次尝试省 1/3 的 Tor 请求）。上游代理是共享瓶颈，
# 少一个请求 = 多三成尝试。默认开启 —— 已实测注册仍能成功。
TOR_SKIP_LOGIN = os.environ.get("TOR_SKIP_LOGIN", "1") != "0"
# 可选：开跑前把 mihomo 切到指定节点。
# Tor 的所有中继连接都经上游代理出去，节点选择对 Tor 延迟影响可达 38%
# （实测 1.79s ~ 2.47s），而且和节点自身的直连延迟无关 —— 用 bench_upstream.py 测。
TOR_UPSTREAM_NODE = os.environ.get("TOR_UPSTREAM_NODE", "")
MIHOMO_SOCK = os.environ.get(
    "MIHOMO_SOCK",
    "/var/folders/13/mjjw8v4s63qdq4xy9051f9700000gp/T/verge-mihomo.sock")
MIHOMO_SECRET = os.environ.get("MIHOMO_SECRET", "set-your-secret")
MIHOMO_GROUP = os.environ.get("MIHOMO_GROUP", "宝可梦")
# 可选：obfs4 网桥 —— 让 Tor 直连、不走上游代理（甩掉共享瓶颈）。
# 网桥行从 https://bridges.torproject.org/options 拿（要过验证码），
# 每行一条，用真实换行或 \n 分隔：
#   TOR_BRIDGES="obfs4 1.2.3.4:443 ABCDEF... cert=xxxxx iat-mode=0
#                obfs4 5.6.7.8:80  FEDCBA... cert=yyyyy iat-mode=0"
# 注意：设了网桥就走直连，不再写 Socks5Proxy。
TOR_BRIDGES = [b.strip() for b in
               os.environ.get("TOR_BRIDGES", "").replace("\\n", "\n").split("\n")
               if b.strip() and not b.strip().startswith("#")]
def _resolve_obfs4proxy():
    """Tor 用 exec 启动 PT 时不走 PATH —— 必须给绝对路径。

    实测：写 "obfs4proxy" 会让它反复以 status 1 退出、Tor 卡在
    Bootstrapped 2%；换成绝对路径后能正常起来。用 shutil.which 解析。
    """
    p = os.environ.get("OBFS4PROXY", "")
    if p:
        return p
    return shutil.which("obfs4proxy") or "obfs4proxy"


OBFS4PROXY = _resolve_obfs4proxy()

SAVE_LOCK = threading.Lock()
_stream_counter = itertools.count(1)
_tor_proc = None          # 脚本自己拉起的 Tor 进程（None = 用的是外部实例）


# ---------- Tor 生命周期 ----------

def tor_listening():
    """SOCKS 端口是否已被占用"""
    try:
        with socket.create_connection((TOR_HOST, TOR_PORT), timeout=2):
            return True
    except Exception:
        return False


def _bootstrap_done():
    try:
        with open(os.path.join(TOR_DIR, "notice.log"), errors="ignore") as f:
            return "Bootstrapped 100%" in f.read()
    except Exception:
        return False


def _kill_stale_tor():
    """清掉占用 SOCKS 端口的旧 tor 进程。

    Tor 只应在注册期间存在 —— 端口被占说明是上次残留（或手动起的），
    脚本要独占端口，所以先收掉它。只杀进程名就是 tor 的，别的程序不动。
    返回被清掉的 pid 列表。
    """
    if not tor_listening():
        return []
    pids = []
    try:
        out = subprocess.run(["lsof", "-nP", f"-iTCP:{TOR_PORT}", "-sTCP:LISTEN", "-t"],
                             capture_output=True, text=True, timeout=10).stdout.split()
    except Exception:
        out = []
    for pid in out:
        try:
            cmd = subprocess.run(["ps", "-p", pid, "-o", "comm="],
                                 capture_output=True, text=True, timeout=5).stdout.strip()
        except Exception:
            continue
        # comm 通常是完整路径，取 basename 判断，避免误杀 tor-something
        if os.path.basename(cmd) != "tor":
            continue
        try:
            os.kill(int(pid), signal.SIGTERM)
            pids.append(int(pid))
        except Exception:
            pass
    for _ in range(20):                      # 等端口释放（最多 10s）
        if not tor_listening():
            break
        time.sleep(0.5)
    for pid in pids:                         # 还不走就强杀
        if tor_listening():
            try:
                os.kill(pid, signal.SIGKILL)
            except Exception:
                pass
    return pids


def start_tor(timeout=120):
    """准备 Tor。

    自动启动**默认关闭** —— 脚本不会在你不知情的时候拉起 Tor：
      · 默认（TOR_AUTOSTART 未设）→ 要求 Tor 已在运行；没跑就直接报错，
        并给出手动启动命令。此时脚本既不启动也不关闭 Tor。
      · TOR_AUTOSTART=1 → 脚本自己拉起 Tor（独占端口，跑完由 stop_tor 关掉）。

    自动启动模式下若端口被旧的 tor 进程占着，会先收掉它 —— 不复用外部实例，
    保证跑完端口一定释放，不会攒下多个 Tor 进程。
    """
    global _tor_proc

    if not TOR_AUTOSTART:
        if tor_listening():
            print(f"  Tor 已在运行（{TOR_HOST}:{TOR_PORT}）→ 直接使用"
                  f"（脚本不会启动/关闭它）")
            return None
        raise RuntimeError(
            "Tor 未运行，且自动启动已关闭（默认）。\n"
            f"  手动启动： tor -f <torrc>     需要 SocksPort {TOR_PORT}"
            f" + ControlPort {CONTROL[1]}\n"
            "  或开启自动启动： TOR_AUTOSTART=1 python3 tor_parallel_farm.py ...")

    if not shutil.which(TOR_BIN):
        raise RuntimeError(f"找不到 {TOR_BIN}，请先安装 Tor（brew install tor）")

    killed = _kill_stale_tor()
    if killed:
        print(f"  清掉残留 Tor 进程 {killed}（脚本要独占端口）")
    if tor_listening():
        raise RuntimeError(
            f"端口 {TOR_PORT} 仍被非 tor 进程占用 —— 换个端口：TOR_SOCKS_PORT=9150")

    os.makedirs(TOR_DIR, exist_ok=True)
    torrc = os.path.join(TOR_DIR, "torrc")
    log = os.path.join(TOR_DIR, "notice.log")
    if os.path.exists(log):
        os.remove(log)
    with open(torrc, "w") as f:
        f.write(f"SocksPort {TOR_PORT}\n"
                f"ControlPort {CONTROL[1]}\n"
                f"CookieAuthentication 0\n"
                f"DataDirectory {os.path.join(TOR_DIR, 'data')}\n"
                f"Log notice file {log}\n")
        if TOR_BRIDGES:
            # 有网桥 → 直连网桥，不走上游代理（甩掉共享瓶颈）
            f.write("UseBridges 1\n")
            f.write(f"ClientTransportPlugin obfs4 exec {OBFS4PROXY}\n")
            for b in TOR_BRIDGES:
                f.write(f"Bridge {b}\n")
        elif TOR_UPSTREAM:
            # 本机直连 Tor 网络不通，借 mihomo 出网
            f.write(f"Socks5Proxy {TOR_UPSTREAM}\n")

    via = (f"经 {len(TOR_BRIDGES)} 个 obfs4 网桥（直连）" if TOR_BRIDGES
           else (f"经上游 {TOR_UPSTREAM}" if TOR_UPSTREAM else "直连"))
    print(f"  启动 Tor（{via}）...")
    errlog = os.path.join(TOR_DIR, "tor-stderr.log")
    errf = open(errlog, "w")
    _tor_proc = subprocess.Popen([TOR_BIN, "-f", torrc],
                                 stdout=errf, stderr=subprocess.STDOUT)
    t0 = time.time()
    while time.time() - t0 < timeout:
        if _tor_proc.poll() is not None:
            errf.close()
            tail = ""
            try:
                tail = open(errlog, errors="ignore").read().strip()[-400:]
            except Exception:
                pass
            raise RuntimeError(f"Tor 启动失败（退出码 {_tor_proc.returncode}）：{tail or '无输出'}")
        if _bootstrap_done():
            print(f"  ✓ Tor 就绪（bootstrap 用时 {int(time.time()-t0)}s）")
            return _tor_proc
        time.sleep(2)
    errf.close()
    stop_tor()
    raise RuntimeError(f"Tor {timeout}s 内未完成 bootstrap —— 上游代理 {TOR_UPSTREAM} 可能不通")


def stop_tor():
    """关掉本脚本启动的 Tor（注册结束时调用，保证端口释放、不留常驻进程）。"""
    global _tor_proc
    if _tor_proc is None:
        return
    if TOR_KEEP_ALIVE:
        print("  TOR_KEEP_ALIVE=1 → 保留 Tor 进程（调试）")
        _tor_proc = None
        return
    print("  关闭 Tor（释放内存）...")
    try:
        _tor_proc.terminate()
        _tor_proc.wait(timeout=10)
    except Exception:
        try:
            _tor_proc.kill()
            _tor_proc.wait(timeout=5)
        except Exception:
            pass
    _tor_proc = None
    # 确认端口释放（最多等 10s，还没走就强杀一次）
    for _ in range(20):
        if not tor_listening():
            break
        time.sleep(0.5)
    if tor_listening():
        _kill_stale_tor()
    print("  ✓ Tor 已关闭，端口已释放" if not tor_listening() else "  ⚠ 端口仍未释放")


def _cleanup(*_a):
    stop_tor()


def switch_upstream(node):
    """把 mihomo 切到指定节点（Tor 的上游）。切不动就只警告，不中断。"""
    quoted = urllib.parse.quote(MIHOMO_GROUP)
    try:
        subprocess.run(
            ["curl", "-s", "-X", "PUT", "--unix-socket", MIHOMO_SOCK,
             "-H", f"Authorization: Bearer {MIHOMO_SECRET}",
             "-H", "Content-Type: application/json",
             "-d", json.dumps({"name": node}),
             f"http://localhost/proxies/{quoted}"],
            capture_output=True, timeout=15)
        time.sleep(2)
        # 核对是否切成功
        r = subprocess.run(
            ["curl", "-s", "--unix-socket", MIHOMO_SOCK,
             "-H", f"Authorization: Bearer {MIHOMO_SECRET}",
             f"http://localhost/proxies/{quoted}"],
            capture_output=True, text=True, timeout=15)
        now = (json.loads(r.stdout) or {}).get("now")
        if now == node:
            print(f"  上游已切到 {node}")
        else:
            print(f"  ⚠ 上游切换未生效（现在是 {now}）—— 用当前节点继续")
    except Exception as e:
        print(f"  ⚠ 上游切换失败: {str(e)[:60]} —— 用当前节点继续")


def stream_proxies():
    """拿一个独占的 SOCKS 流（隔离 → 独立出口）。"""
    n = next(_stream_counter)
    cred = f"th{n}-{random.randint(10**6, 10**7)}"
    url = f"socks5h://{cred}:{cred}@{TOR_HOST}:{TOR_PORT}"
    return {"http": url, "https": url}


def newnym():
    try:
        s = socket.create_connection(CONTROL, timeout=5)
        s.sendall(b"AUTHENTICATE\r\n"); s.recv(100)
        s.sendall(b"SIGNAL NEWNYM\r\n"); r = s.recv(100).decode()
        s.close(); return "OK" in r
    except Exception:
        return False


def exit_ip(proxies, tries=2):
    for _ in range(tries):
        try:
            return requests.get("https://api.ipify.org", proxies=proxies, timeout=45).text.strip()
        except Exception:
            time.sleep(1)
    return "?"


_TS_NOTED = [False]


def _ts_note(msg):
    """打码不可用时只提示一次，避免刷屏。"""
    if _TS_NOTED[0]:
        return
    _TS_NOTED[0] = True
    print(f"  ⚠ Turnstile 打码不可用（{msg}）—— 注册会被 human check 拦下", flush=True)


def signup_tor(proxies, email, pwd, tz="Asia/Singapore"):
    """注册（经给�� proxies 出口）。返回 (session, ok, errs, captcha, text)

    2026-10-20 起站点在提交环节强制 Turnstile：
      · 失败响应是 1:{"error":"Please complete the human check to continue.","needCaptcha":true}
      · 前端把 token 放在表单原生字段 `cf-turnstile-response`（**不带 _1_ 前缀**）
      · 所以这里必须先打码拿到 token，再拼进 multipart
    没有打码 key 时退回旧行为（大概率失败，但至少能跑通链路）。
    """
    import turnstile_solver

    fp = str(uuid.uuid4())
    bd = "----WebKitFormBoundary" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=16))

    # 先打码（没有 key 会直接返回 None，不阻塞）
    ts_token = None
    try:
        ts_token, info = turnstile_solver.solve_turnstile(verbose=False)
        if not ts_token:
            _ts_note(info)
    except Exception as e:
        _ts_note(f"{type(e).__name__}: {str(e)[:60]}")

    fields = [("_1_$ACTION_REF_1", ""),
              ("_1_$ACTION_1:0", json.dumps({"id": SIGNUP_ID, "bound": "$@1"}, separators=(",", ":"))),
              ("_1_$ACTION_1:1", '["$undefined"]'),
              ("_1_$ACTION_KEY", ACTION_KEY),
              ("_1_device_fingerprint", fp), ("_1_timezone", tz), ("_1_next", ""),
              ("_1_email", email), ("_1_password", pwd), ("_1_invite_code", "")]
    if ts_token:
        # 原生 body 字段（前端 input name 就是这个，不带前缀）
        fields.append(("cf-turnstile-response", ts_token))
    fields.append(("0", '["$undefined","$K1"]'))

    parts = [f'--{bd}\r\nContent-Disposition: form-data; name="{n}"\r\n\r\n{v}' for n, v in fields]
    body = "\r\n".join(parts) + f"\r\n--{bd}--\r\n"
    hdrs = {"Content-Type": f"multipart/form-data; boundary={bd}", "Accept": "text/x-component",
            "Next-Action": SIGNUP_ID, "Next-Router-State-Tree": ROUTER,
            "Origin": BASE, "Referer": f"{BASE}/login"}
    s = requests.Session(); s.headers.update({"User-Agent": UA})
    if not TOR_SKIP_LOGIN:
        s.get(f"{BASE}/login", proxies=proxies, timeout=70)
    try:
        cap = s.get(f"{BASE}/api/auth/signup-precheck?fp={fp}",
                    headers={"Accept": "application/json"}, proxies=proxies, timeout=50)
        # 注意：precheck 可能返回 needCaptcha=false，但提交仍要求 human check。
        # 这个返回只用来提示，不据此跳过打码。
        cap.json().get("needCaptcha")
    except Exception:
        pass
    r = s.post(f"{BASE}/login", data=body, headers=hdrs, proxies=proxies, timeout=90)
    ok = "signedIn" in r.text
    return s, ok, tar.flight_errors(r.text), False, r.text


def finalize(s, proxies, acct, etok):
    """建 key + consent（经 Tor）；验证/测 key 走直连（快，且 Tor 出口可能被地区风控）。"""
    try:
        r2 = s.get(f"{BASE}/api/keys", headers={"Accept": "application/json"}, proxies=proxies, timeout=70)
        for k in r2.json().get("keys", []):
            try: s.delete(f"{BASE}/api/keys/{k['id']}", proxies=proxies, timeout=50)
            except Exception: pass
    except Exception:
        pass
    r3 = s.post(f"{BASE}/api/keys", json={"label": f"th-tor-{random.randint(100,999)}"},
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                proxies=proxies, timeout=70)
    if r3.status_code != 201:
        return acct, f"key {r3.status_code}"
    key = r3.json().get("plaintext")
    acct["api_key"] = key
    try:
        rc = s.post(f"{BASE}/api/me/privacy", json={"free_models_enabled": True},
                    headers={"Accept": "application/json", "Content-Type": "application/json"},
                    proxies=proxies, timeout=70)
        acct["consent"] = rc.status_code == 200 and '"ok":true' in rc.text
    except Exception:
        acct["consent"] = False
    acct["verified"] = tar.verify_email(etok)          # 直连（YYDS API）
    okc, info = tar.test_model(key)                    # 直连（mihomo）
    for _ in range(3):
        if okc or "region_blocked" not in info:
            break
        time.sleep(10)
        okc, info = tar.test_model(key)
    acct["test"] = info
    return acct, None


def save(acct):
    with SAVE_LOCK:
        with open("th_direct_results.jsonl", "a") as f:
            f.write(json.dumps(acct) + "\n")
        if acct.get("api_key"):
            with open("th_keys.txt", "a") as f:
                f.write(f"{acct['email']}\t{acct['password']}\t{acct['api_key']}"
                        f"\tverified={acct.get('verified')}\ttest={acct.get('test')}\n")


def main(target=1, n_threads=8, n_verifiers=None, max_attempts=None):
    """两段流水线。

    注册线程只做注册 —— 成功就丢进队列立刻开下一轮；
    验证线程单独消费队列做「建key + consent + 验证邮箱 + 测key」。
    这样注册线程不会被最长 300s 的验证邮件轮询堵住。

    另外每次尝试不再单独查出口 IP（那是 1/4 的 Tor 流量、纯粹为了日志），
    改成只在注册成功后才查一次。
    """
    done = []
    lock = threading.Lock()
    attempts = itertools.count(1)
    # 尝试预算：默认 目标×40（按成功率 2.5% 估）。成功率低的时段明显不够，
    # 用 TOR_MAX_ATTEMPTS 直接给绝对值覆盖。
    max_attempts = (max_attempts
                    or int(os.environ.get("TOR_MAX_ATTEMPTS", "0"))
                    or target * 40)
    stop = threading.Event()
    jobs = queue.Queue()
    n_verifiers = n_verifiers or max(2, n_threads // 8)

    def registrar(wid):
        while not stop.is_set():
            with lock:
                # 已完成 + 在途（已注册待验证）够目标就停，避免超发太多
                if len(done) + jobs.unfinished_tasks >= target:
                    stop.set(); return
                n = next(attempts)
                if n > max_attempts:
                    stop.set(); return
            proxies = stream_proxies()
            try:
                # 不再单独探测出口 IP —— 死流会在下面第一个请求就抛异常
                email, etok = tar.gen_email()
                pwd = ''.join(random.choices(string.ascii_letters + string.digits, k=12)) + '!Aa1'
                s, ok, errs, captcha, txt = signup_tor(proxies, email, pwd)
                if not ok:
                    tag = "captcha" if captcha else (errs[0][:46] if errs else "?")
                    print(f"  [r{wid} #{n}] ✗ {tag}")
                    continue
                uid = re.findall(r'"userId":\s*"([^"]+)"', txt)
                print(f"  [r{wid} #{n}] ✓ SIGNUP OK — 交给验证线程")
                jobs.put((proxies, s,
                          {"email": email, "password": pwd,
                           "userId": uid[0] if uid else ""}, etok, wid, n))
            except Exception as e:
                print(f"  [r{wid} #{n}] EXC {type(e).__name__}: {str(e)[:55]}")

    def verifier(vid):
        while True:
            job = jobs.get()
            try:
                if job is None:
                    return
                proxies, s, acct, etok, wid, n = job
                ip = exit_ip(proxies)                  # 只在成功时查，用于记录
                acct["ip"] = ip
                acct["node"] = f"tor:{ip}"
                acct, err = finalize(s, proxies, acct, etok)
                save(acct)
                if acct.get("verified") and acct.get("test") == "200":
                    with lock:
                        done.append(acct)
                    print(f"  ★ 有效账号 {len(done)}/{target}: {acct['email']} (exit={ip})")
                else:
                    print(f"  [v{vid} #{n}] {ip:16} 注册成功但未可用 "
                          f"(verified={acct.get('verified')} test={acct.get('test')})")
            except Exception as e:
                print(f"  [v{vid}] EXC {type(e).__name__}: {str(e)[:55]}")
            finally:
                jobs.task_done()

    print(f"=== Tor 并行农场 | 目标 {target} | 注册线程 {n_threads} "
          f"| 验证线程 {n_verifiers} | 最多 {max_attempts} 次尝试 ===")
    if TOR_UPSTREAM_NODE:
        switch_upstream(TOR_UPSTREAM_NODE)   # 先把上游切到优选节点
    start_tor()                    # 默认不自动启动；TOR_AUTOSTART=1 才会拉起
    t0 = time.time()
    try:
        regs = [threading.Thread(target=registrar, args=(i + 1,), daemon=True)
                for i in range(n_threads)]
        vers = [threading.Thread(target=verifier, args=(i + 1,), daemon=True)
                for i in range(n_verifiers)]
        for t in regs + vers: t.start()
        for t in regs: t.join()          # 注册线程跑完 → 不再产生新任务
        inflight = jobs.unfinished_tasks
        if inflight:
            print(f"\n=== 注册已达标，等待 {inflight} 个在途账号完成验证"
                  f"（邮件最长等 300s）===")
        jobs.join()                      # 等验证线程把队列消化完
        for _ in vers: jobs.put(None)
        for t in vers: t.join(timeout=30)
    finally:
        stop_tor()                 # 跑完就关，不常驻（除非复用了外部实例）

    dt = time.time() - t0
    print(f"\n{'='*68}")
    extra = f"（并发超发 {len(done)-target} 个，线程同时成功）" if len(done) > target else ""
    print(f"=== DONE: {len(done)}/{target} 个有效账号{extra} | 耗时 {dt/60:.1f} 分钟 ===")
    for a in done:
        print(f"  {a['email']}  exit={a['ip']}  test={a.get('test')}")
    if done:
        print(f"  平均 {dt/len(done):.0f}s / 账号")
    print("  结果已追加到 th_direct_results.jsonl / th_keys.txt")
    return done


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a.isdigit()]
    n_target = int(args[0]) if len(args) > 0 else 1
    n_threads = int(args[1]) if len(args) > 1 else 8
    # 异常/被杀也要关掉 Tor
    atexit.register(_cleanup)
    for _sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(_sig, lambda s, f: (_cleanup(), sys.exit(130)))
        except Exception:
            pass
    try:
        main(n_target, n_threads)
    except KeyboardInterrupt:
        _cleanup()
        sys.exit(130)
    except RuntimeError as e:
        # 环境问题（Tor 没跑、装不上等）→ 给干净提示，不打 traceback
        _cleanup()
        print(f"\n✗ {e}\n", file=sys.stderr)
        sys.exit(2)
