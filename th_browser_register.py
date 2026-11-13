#!/usr/bin/env python3
"""th_browser_register.py — 用真浏览器（住宅 IP）过 Turnstile 并完成注册。

2026-10-20 实测结论（为什么必须这么干）：
  · 服务端在提交环节强制 Turnstile，返回 {"error":"Please complete the human check...","needCaptcha":true}
  · precheck 永远返回 needCaptcha:false —— 靠它判断会误判
  · 前端会真渲染 Turnstile，但 **token 只有住宅 IP + 有头浏览器才给**
    - 机房 IP（机场/免费代理）+ headless → token 恒为空字符串
    - 住宅 IP（duckip）+ 有头 → token 816 字符，一次通过
  · 流程：首提触发 Turnstile → 等隐藏 input cf-turnstile-response 有值 → 再提交

前置：
  1. duckip_relay.py 在跑（127.0.0.1:18080 → mihomo → proxy.duckip.net:1000）
  2. socks_nolocal.py 在跑（127.0.0.1:18082 无认证 → 18080，给 Chromium 用）

用法：
  .venv/bin/python th_browser_register.py --count 100 --workers 1
"""
import argparse
import json
import os
import random
import re
import string
import sys
import threading
import time

sys.path.insert(0, "/Users/zxc/tokenharbor-farmer")
os.environ.setdefault("TOR_AUTOSTART", "0")

SITEKEY_FIELD = "cf-turnstile-response"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
STEALTH = r"""
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.chrome = window.chrome || {runtime: {}};
"""

STAMP = time.strftime("%Y%m%d-%H%M%S")
TXT_OUT = f"th_keys_browser_{STAMP}.txt"
JSON_OUT = f"th_keys_browser_{STAMP}.jsonl"
LOCK = threading.Lock()


def log(msg):
    print(msg, flush=True)


def one_account(idx, proxy, headless, timeout_tok=90):
    """单账号：开浏览器 → 过 Turnstile → 注册 → 建 key → 验证邮箱。"""
    from patchright.sync_api import sync_playwright
    import th_auto_register as tar

    t0 = time.time()
    email, etok = tar._cfmail_create()
    pwd = ''.join(random.choices(string.ascii_letters + string.digits, k=12)) + '!Aa1'
    rec = {"email": email, "password": pwd, "idx": idx}

    with sync_playwright() as p:
        launch = {"headless": headless,
                  "args": ["--disable-blink-features=AutomationControlled",
                           "--no-first-run", "--disable-dev-shm-usage"]}
        if proxy:
            launch["proxy"] = {"server": proxy}
        b = p.chromium.launch(**launch)
        ctx = b.new_context(viewport={"width": 1440, "height": 900})
        ctx.add_init_script(STEALTH)
        page = ctx.new_page()
        try:
            page.goto("https://tokenharbor.ai/login?mode=signup",
                      wait_until="domcontentloaded", timeout=90000)
            ok_form = False
            for _ in range(40):
                if page.evaluate("()=>!!document.querySelector('input[name=email]')"):
                    ok_form = True
                    break
                page.wait_for_timeout(500)
            if not ok_form:
                rec["stage"] = "no-form"
                return rec, None
            page.wait_for_timeout(700)

            page.fill('input[name=email]', email)
            page.fill('input[name=password]', pwd)
            page.wait_for_timeout(400)
            # 首提：触发 needCaptcha → 渲染 Turnstile
            page.evaluate("()=>{document.querySelector('input[name=email]')"
                          ".closest('form').requestSubmit();}")
            page.wait_for_timeout(5000)

            # 等 token
            tok = None
            end = time.time() + timeout_tok
            i = 0
            while time.time() < end:
                tok = page.evaluate(
                    "()=>{const e=document.querySelector('input[name=\"%s\"]');"
                    "return e&&e.value.length>10?e.value:null}" % SITEKEY_FIELD)
                if tok:
                    break
                try:
                    page.mouse.move(680 + (i * 9) % 80, 400 + (i * 7) % 50)
                except Exception:
                    pass
                i += 1
                page.wait_for_timeout(1500)

            if not tok:
                rec["stage"] = "no-token"
                return rec, None
            rec["ts_token_len"] = len(tok)

            # 表单这时被 Turnstile 替换，需要重新填 email/password
            has_inputs = page.evaluate(
                "()=>!!document.querySelector('input[name=email]')")
            if not has_inputs:
                page.wait_for_timeout(2500)
            if page.evaluate("()=>!!document.querySelector('input[name=email]')"):
                page.fill('input[name=email]', email)
                page.fill('input[name=password]', pwd)
                page.wait_for_timeout(300)

            # 二次提交（带 token）
            page.evaluate("()=>{const e=document.querySelector('input[name=\"email\"]');"
                          "if(e){e.closest('form').requestSubmit();}}")
            page.wait_for_timeout(9000)

            body = page.evaluate("()=>document.body.innerText.slice(0,600)")
            url = page.url
            signed = ("Sign out" in body) or ("dashboard" in url) or \
                     ("Credits" in body and "Create your free account" not in body)
            rec["stage"] = "signedIn" if signed else "not-signed-in"
            rec["url"] = url

            # 用同一浏览器上下文的后台会话建 key（cookie 已在）
            if signed:
                r = page.evaluate("""async () => {
                    try {
                        const r1 = await fetch('/api/keys', {
                            method:'POST',
                            headers:{'Content-Type':'application/json','Accept':'application/json'},
                            body: JSON.stringify({label:'th-br-'+Math.floor(Math.random()*900+100)})
                        });
                        const j = await r1.json();
                        return {status: r1.status, key: j.plaintext || null};
                    } catch(e) { return {err: String(e)}; }
                }""")
                if r.get("key"):
                    rec["api_key"] = r["key"]
                else:
                    rec["key_err"] = str(r)[:120]

                page.evaluate("""async () => {
                    try {
                        await fetch('/api/me/privacy', {
                            method:'POST',
                            headers:{'Content-Type':'application/json'},
                            body: JSON.stringify({free_models_enabled:true})
                        });
                    } catch(e) {}
                }""")
                rec["consent"] = True
        finally:
            try:
                ctx.close(); b.close()
            except Exception:
                pass

    # 邮箱验证（直连，快）
    if rec.get("api_key"):
        try:
            rec["verified"] = tar.verify_email(etok, max_wait=180)
        except Exception:
            rec["verified"] = False
        rec["test"] = "untested"
    rec["secs"] = round(time.time() - t0, 1)

    if rec.get("api_key") and rec.get("verified"):
        with LOCK:
            with open(TXT_OUT, "a") as f:
                f.write(f"{rec['email']}\t{rec['password']}\t{rec['api_key']}"
                        f"\tverified=True\ttest=untested\n")
    with LOCK:
        with open(JSON_OUT, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec, rec.get("api_key")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--proxy", default="socks5://127.0.0.1:18082")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--max-tries", type=int, default=None)
    ap.add_argument("--workers", type=int, default=2)
    A = ap.parse_args()
    max_tries = A.max_tries or max(A.count * 3, A.count + 5)

    log(f"=== 浏览器注册 | 目标 {A.count} | 代理 {A.proxy} | "
        f"headless={A.headless} | 尝试上限 {max_tries} ===")
    log(f"    导出 → {TXT_OUT} / {JSON_OUT}")

    got = [0]
    tries = [0]
    gotlock = threading.Lock()
    stop = threading.Event()
    t0 = time.time()

    def worker(wid):
        while not stop.is_set():
            with gotlock:
                if got[0] >= A.count or tries[0] >= max_tries:
                    stop.set(); return
                tries[0] += 1
                n = tries[0]
            log(f"\n--- [w{wid}] 第 {n} 次尝试（已有 {got[0]}/{A.count}）---")
            try:
                rec, key = one_account(n, A.proxy, A.headless)
            except Exception as e:
                log(f"  [w{wid}] EXC {type(e).__name__}: {str(e)[:100]}")
                continue
            st = rec.get("stage")
            if key and rec.get("verified"):
                with gotlock:
                    got[0] += 1
                    g = got[0]
                log(f"  ★ 有效账号 {g}/{A.count}: {rec['email']} "
                    f"(token {rec.get('ts_token_len')} 字符, {rec.get('secs')}s)")
            else:
                log(f"  [w{wid}] ✗ stage={st} key={'有' if key else '无'} "
                    f"verified={rec.get('verified')} {rec.get('secs')}s")
            if st in ("no-form", "no-token"):
                time.sleep(random.uniform(3, 7))

    ws = [threading.Thread(target=worker, args=(i + 1,), daemon=True)
          for i in range(max(1, A.workers))]
    for w in ws:
        w.start()
    for w in ws:
        w.join()

    log(f"\n=== DONE: {got[0]}/{A.count} 有效 | {tries} 次尝试 | "
        f"{(time.time()-t0)/60:.1f} 分钟 ===")
    log(f"  有效 key → {TXT_OUT}")


if __name__ == "__main__":
    main()
