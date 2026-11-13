#!/usr/bin/env python3
"""tor_screened_farm v2 — 出口烧伤记忆 + NEWNYM 轮换的 Tor 注册农场

核心逻辑（v2，替代假邮箱探针——实验证明域名校验先于限流，探针测不到限流）：
  1. 新流 → 查出口 IP（廉价，ipify 经流）
  2. 出口所在 /24 网段若在烧伤名单里（WINDOW 分钟内被限过）→ 直接 NEWNYM 换，不烧真注册
  3. 干净出口 → 真注册直接上
  4. 被限流 → 记 /24 进烧伤名单 + NEWNYM
  5. 成功 → 验证线程（CF 收信 → 建 key → 测 200）

用法:
  TOR_AUTOSTART=1 .venv/bin/python -u tor_screened_farm.py [目标] [线程] [尝试上限] [烧伤窗口分钟]
产出: th_keys_screened_<时间戳>.txt / .jsonl（实时落盘）
"""
import os, sys, json, time, random, string, threading, queue, re, itertools
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("TOR_AUTOSTART", "1")
import tor_parallel_farm as t
import th_auto_register as tar

TARGET  = int(sys.argv[1]) if len(sys.argv) > 1 else 100
THREADS = int(sys.argv[2]) if len(sys.argv) > 2 else 8
MAX_TRY = int(sys.argv[3]) if len(sys.argv) > 3 else TARGET * 60
WINDOW  = int(sys.argv[4]) if len(sys.argv) > 4 else 20   # 烧伤 /24 的记忆分钟数

STAMP    = time.strftime("%Y%m%d-%H%M%S")
TXT_OUT  = f"th_keys_screened_{STAMP}.txt"
JSON_OUT = f"th_keys_screened_{STAMP}.jsonl"

lock     = threading.Lock()
done     = []
attempts = itertools.count(1)
stop     = threading.Event()
jobs     = queue.Queue()
burned   = {}          # /24 网段 → 被限时刻
geo_cache = {}         # exit ip → country_code


def subnet_of(ip):
    m = re.match(r"^(\d+\.\d+\.\d+)\.\d+$", ip or "")
    return m.group(1) + ".0/24" if m else ip


def subnet_burned(ip):
    sn = subnet_of(ip)
    ts = burned.get(sn)
    return ts is not None and time.time() - ts < WINDOW * 60


def burn(ip):
    burned[subnet_of(ip)] = time.time()


def exit_country(ip):
    """直连查出口国家（排除 CN/HK/MO 地区封锁），带缓存"""
    if ip in geo_cache:
        return geo_cache[ip]
    cc = ""
    try:
        r = requests.get(f"https://ipwho.is/{ip}", timeout=8)
        cc = (r.json() or {}).get("country_code", "") or ""
    except Exception:
        pass
    geo_cache[ip] = cc
    return cc


def registrar(wid):
    while not stop.is_set():
        with lock:
            if len(done) + jobs.unfinished_tasks >= TARGET:
                stop.set(); return
            n = next(attempts)
            if n > MAX_TRY:
                stop.set(); return
        p = t.stream_proxies()
        try:
            ip = t.exit_ip(p, tries=1)
            if ip == "?":
                print(f"  [r{wid} #{n}] ✗ 死流 → NEWNYM", flush=True)
                t.newnym(); time.sleep(1)
                continue
            if subnet_burned(ip):
                print(f"  [r{wid} #{n}] ↷ 出口 {ip} 网段已烧伤 → NEWNYM", flush=True)
                t.newnym(); time.sleep(1)
                continue
            cc = exit_country(ip)
            if cc in ("CN", "HK", "MO"):
                print(f"  [r{wid} #{n}] ↷ 出口 {ip} 地区封锁({cc}) → NEWNYM", flush=True)
                burn(ip); t.newnym(); time.sleep(1)
                continue
            # 干净出口 → 真注册 + 榨干模式（成功后同出口连注直到被限）
            squeeze = 0
            while not stop.is_set():
                with lock:
                    if len(done) + jobs.unfinished_tasks >= TARGET:
                        stop.set(); return
                email, etok = tar.gen_email()
                pwd = ''.join(random.choices(string.ascii_letters + string.digits, k=12)) + '!Aa1'
                s, ok, errs, cap, txt = t.signup_tor(p, email, pwd)
                if not ok:
                    joined = " ".join(errs).lower()
                    tag = "captcha" if cap else (errs[0][:44] if errs else "?")
                    if cap or "bit fast" in joined or "too many" in joined or "couldn't create" in joined:
                        burn(ip)
                        if squeeze:
                            print(f"  [r{wid} #{n}] 出口 {ip} 榨干（连出 {squeeze} 个后）→ 烧记 + NEWNYM", flush=True)
                        else:
                            print(f"  [r{wid} #{n}] ✗ {ip} 被限（{tag}）→ 烧记 + NEWNYM", flush=True)
                    else:
                        print(f"  [r{wid} #{n}] ✗ {tag}", flush=True)
                    t.newnym(); time.sleep(1)
                    break
                uid = re.findall(r'"userId":\s*"([^"]+)"', txt)
                squeeze += 1
                print(f"  [r{wid} #{n}] ✓ SIGNUP OK (exit={ip} 第{squeeze}个) → 验证线程", flush=True)
                jobs.put((p, s, {"email": email, "password": pwd,
                                 "userId": uid[0] if uid else ""}, etok, wid, n))
                time.sleep(2)          # 同出口连注间隔，避免触发频控
            continue
        except Exception as e:
            print(f"  [r{wid} #{n}] EXC {type(e).__name__}: {str(e)[:50]}", flush=True)


def verifier(vid):
    while True:
        job = jobs.get()
        try:
            if job is None:
                return
            proxies, s, acct, etok, wid, n = job
            ip = acct.get("ip", "?")
            acct, err = t.finalize(s, proxies, acct, etok)
            t.save(acct)
            if acct.get("verified") and acct.get("test") == "200":
                with lock:
                    done.append(acct)
                print(f"  ★ 有效账号 {len(done)}/{TARGET}: {acct['email']} (exit={ip})", flush=True)
            else:
                print(f"  [v{vid} #{n}] 注册成功但未可用 (verified={acct.get('verified')} test={acct.get('test')})", flush=True)
        except Exception as e:
            print(f"  [v{vid}] EXC {type(e).__name__}: {str(e)[:50]}", flush=True)
        finally:
            jobs.task_done()


# 实时落盘（沿用 batch_run 的双写逻辑）
_orig_save = t.save
def save_dual(acct):
    rec = dict(acct)
    ok = rec.get("verified") and rec.get("test") == "200"
    with lock:
        with open(JSON_OUT, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if ok:
            with open(TXT_OUT, "a") as f:
                f.write(f"{rec['email']}\t{rec['password']}\t{rec.get('api_key')}"
                        f"\tverified={rec.get('verified')}\ttest={rec.get('test')}\n")
    _orig_save(acct)
t.save = save_dual


def main():
    print(f"=== 出口预筛农场 v2 | 目标 {TARGET} | 线程 {THREADS} | 上限 {MAX_TRY} | 烧伤窗口 {WINDOW}min ===", flush=True)
    print(f"    导出 → {TXT_OUT} / {JSON_OUT}", flush=True)
    if t.TOR_UPSTREAM_NODE:
        t.switch_upstream(t.TOR_UPSTREAM_NODE)
    t.start_tor()
    t0 = time.time()
    try:
        regs = [threading.Thread(target=registrar, args=(i + 1,), daemon=True) for i in range(THREADS)]
        vers = [threading.Thread(target=verifier, args=(i + 1,), daemon=True) for i in range(max(2, THREADS // 4))]
        for x in regs + vers: x.start()
        for x in regs: x.join()
        inflight = jobs.unfinished_tasks
        if inflight:
            print(f"\n=== 注册达标，等待 {inflight} 个在途验证 ===", flush=True)
        jobs.join()
        for _ in vers: jobs.put(None)
        for x in vers: x.join(timeout=30)
    finally:
        t.stop_tor()
    dt = time.time() - t0
    print(f"\n{'='*66}\n=== DONE: {len(done)}/{TARGET} 有效 | 耗时 {dt/60:.1f} 分钟 ===", flush=True)
    for a in done:
        print(f"  {a['email']}  exit={a.get('ip')}  test={a.get('test')}", flush=True)
    print(f"  有效 key → {TXT_OUT}", flush=True)


if __name__ == "__main__":
    main()
