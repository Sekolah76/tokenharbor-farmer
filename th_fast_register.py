#!/usr/bin/env python3
"""th_fast_register.py — 单浏览器多标签页并行注册（每个 tab 一个账号）。

为什么这么设计（2026-10-20 实测）：
  · Turnstile token **绑定出口 IP**：浏览器解的 token 拿去做纯 HTTP 请求会被拒
    （duckip 每条连接换住宅 IP，token 校验时 IP 对不上 → "human check" 再来一次）
  · 所以注册必须由**解 token 的那个浏览器自己发**
  · 开多个浏览器太重（每个 ~300MB + 20s 启动）
  → 一个 BrowserContext 开 N 个 page 并行，共享 cookie 池/连接池，最省资源

实测：单账号约 45-70s，5 个 page 并行 ≈ 5 个/分钟。

用法:
  .venv/bin/python th_fast_register.py --count 100 --tabs 5
"""
import argparse
import json
import os
import random
import string
import sys
import threading
import time

sys.path.insert(0, "/Users/zxc/tokenharbor-farmer")
os.environ.setdefault("TOR_AUTOSTART", "0")

FIELD = "cf-turnstile-response"
STAMP = time.strftime("%Y%m%d-%H%M%S")
TXT = f"th_keys_fast_{STAMP}.txt"
JSONL = f"th_keys_fast_{STAMP}.jsonl"
LOCK = threading.Lock()


def log(m):
    print(m, flush=True)


def register_in_page(page, tar, proxy_ok=True, tok_timeout=75):
    """在给定 page 里完成一次注册，返回记录 dict。"""
    t0 = time.time()
    email, etok = tar._cfmail_create()
    pwd = ''.join(random.choices(string.ascii_letters + string.digits, k=12)) + '!Aa1'
    rec = {"email": email, "password": pwd, "email_token": etok}

    # 最多 3 轮「打开/重载 + 长等待」，React 首屏有时要 20s+
    ok = False
    for attempt in range(3):
        try:
            page.goto("https://tokenharbor.ai/login?mode=signup",
                      wait_until="commit", timeout=90000)
        except Exception:
            page.wait_for_timeout(2000)
            continue
        for _ in range(60):                       # 60 × 500ms = 30s
            if page.evaluate("()=>!!document.querySelector('input[name=email]')"):
                ok = True
                break
            page.wait_for_timeout(300)
        if ok:
            break
        # 没表单：可能还在 loading，reload 再来一轮
        try:
            page.reload(wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1500)
        except Exception:
            pass
    if not ok:
        rec["stage"] = "no-form"
        return rec
    page.wait_for_timeout(500)

    page.fill('input[name=email]', email)
    page.fill('input[name=password]', pwd)
    page.wait_for_timeout(250)
    # 首提：逼出 Turnstile
    page.evaluate("()=>{document.querySelector('input[name=email]')"
                  ".closest('form').requestSubmit();}")
    # 轮询等 Turnstile 的隐藏字段出现（比固定 3.5s 快且稳）
    for _ in range(20):
        if page.evaluate("()=>!!document.querySelector('input[name=\"%s\"]')" % FIELD):
            break
        page.wait_for_timeout(200)

    tok = None
    end = time.time() + tok_timeout
    i = 0
    while time.time() < end:
        tok = page.evaluate(
            "()=>{const e=document.querySelector('input[name=\"%s\"]');"
            "return e&&e.value.length>10?e.value:null}" % FIELD)
        if tok:
            break
        if i % 3 == 0:                      # 鼠标动作不必每轮都做
            try:
                page.mouse.move(650 + (i * 11) % 90, 390 + (i * 7) % 60)
            except Exception:
                pass
        i += 1
        page.wait_for_timeout(400)
    if not tok:
        rec["stage"] = "no-token"
        return rec
    rec["tok_len"] = len(tok)

    # 关键：拿到 token 后「立刻」用 fetch 直接把整个 FormData 发出去。
    # 不能等 React 重渲染 —— 重渲染会把 cf-turnstile-response 的 value 冲成空。
    if page.evaluate("()=>!!document.querySelector('input[name=email]')"):
        page.fill('input[name=email]', email)
        page.fill('input[name=password]', pwd)
        page.wait_for_timeout(200)
    # 提交前把 token / 凭据强行写回 hidden input（React 重渲染可能清空过），
    # 再 requestSubmit —— 保持浏览器的正常导航流程，便于判定登录态。
    page.evaluate("""(args) => {
        const [tok, email, pwd] = args;
        const set = (name, val) => {
            let e = document.querySelector('input[name="'+name+'"]');
            if (!e) { e = document.createElement('input'); e.type='hidden'; e.name=name;
                      document.querySelector('form').appendChild(e); }
            const s = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
            s.call(e, val);
        };
        set('cf-turnstile-response', tok);
        set('email', email);
        set('password', pwd);
    }""", [tok, email, pwd])
    page.wait_for_timeout(120)
    page.evaluate("()=>{const f=document.querySelector('form'); if(f) (f.requestSubmit? f.requestSubmit(): f.submit());}")
    # 轮询登录态：最多 12s，一旦出现就立刻走
    signed = False
    for _ in range(40):
        page.wait_for_timeout(300)
        body = page.evaluate("()=>document.body.innerText.slice(0,500)")
        url = page.url
        if ("Sign out" in body) or ("dashboard" in url) or \
           ("Credits" in body and "Create your free account" not in body):
            signed = True
            break
    rec["stage"] = "signedIn" if signed else "not-signed-in"
    if signed:
        r = page.evaluate("""async () => {
            try {
              const r1 = await fetch('/api/keys', {
                method:'POST',
                headers:{'Content-Type':'application/json','Accept':'application/json'},
                body: JSON.stringify({label:'th-f-'+Math.floor(Math.random()*900+100)})});
              const j = await r1.json();
              return {status:r1.status, key:j.plaintext||null};
            } catch(e){ return {err:String(e)}; }
        }""")
        if r.get("key"):
            rec["api_key"] = r["key"]
            try:
                page.evaluate("""async () => { try { await fetch('/api/me/privacy',{
                    method:'POST', headers:{'Content-Type':'application/json'},
                    body: JSON.stringify({free_models_enabled:true})}); } catch(e){} }""")
                rec["consent"] = True
            except Exception:
                pass
        else:
            rec["key_err"] = str(r)[:100]
    rec["secs"] = round(time.time() - t0, 1)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=100)
    ap.add_argument("--tabs", type=int, default=5)
    ap.add_argument("--proxy", default="socks5://127.0.0.1:18083")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--max-tries", type=int, default=None)
    A = ap.parse_args()
    max_tries = A.max_tries or int(A.count * 2.2)

    import th_auto_register as tar
    from patchright.sync_api import sync_playwright

    log(f"=== 快速注册 | 目标 {A.count} | 标签页 {A.tabs} | 代理 {A.proxy} | "
        f"headless={A.headless} | 尝试上限 {max_tries} ===")
    log(f"    导出 → {TXT} / {JSONL}")

    state = {"got": 0, "tries": 0, "done": 0}
    stop = threading.Event()

    def worker(wid):
        try:
            with sync_playwright() as p:
                launch = {"headless": A.headless,
                          "args": ["--disable-blink-features=AutomationControlled",
                                   "--no-first-run", "--disable-dev-shm-usage"]}
                if A.proxy:
                    launch["proxy"] = {"server": A.proxy}
                b = p.chromium.launch(**launch)
                while not stop.is_set():
                    with LOCK:
                        if state["got"] >= A.count or state["tries"] >= max_tries:
                            stop.set()
                            break
                        state["tries"] += 1
                        n = state["tries"]
                    # 每个账号独立 context：新 cookie 池 + 新连接池 → 换住宅 IP
                    # （实测同一 IP 注册过 1 个号后，注册页就不再渲染表单）
                    try:
                        ctx = b.new_context(viewport={"width": 1366, "height": 850})
                    except Exception as e:
                        log(f"  [t{wid}] ctx EXC {str(e)[:60]}")
                        time.sleep(2)
                        continue
                    page = ctx.new_page()
                    try:
                        rec = register_in_page(page, tar)
                    except Exception as e:
                        log(f"  [t{wid}] EXC {type(e).__name__}: {str(e)[:70]}")
                        time.sleep(2)
                        continue
                    finally:
                        try:
                            ctx.close()
                        except Exception:
                            pass
                    st = rec.get("stage")
                    with LOCK:
                        state["done"] += 1
                        with open(JSONL, "a") as f:
                            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    if rec.get("api_key"):
                        with LOCK:
                            with open(TXT, "a") as f:
                                f.write(f"{rec['email']}\t{rec['password']}\t"
                                        f"{rec['api_key']}\tverified=pending\ttest=untested\n")
                    if st == "signedIn" and rec.get("api_key"):
                        with LOCK:
                            state["got"] += 1
                            g = state["got"]
                        log(f"  ★ [t{wid}] {g}/{A.count} {rec['email']} "
                            f"(tok {rec.get('tok_len')}, {rec.get('secs')}s)")
                    else:
                        log(f"  [t{wid}] ✗ {st} key={'有' if rec.get('api_key') else '无'} "
                            f"{rec.get('secs')}s")
                    if st in ("no-form", "no-token"):
                        time.sleep(random.uniform(2, 5))
                try:
                    b.close()
                except Exception:
                    pass
        except Exception as e:
            log(f"  [t{wid}] FATAL {type(e).__name__}: {str(e)[:90]}")

    t0 = time.time()
    ths = [threading.Thread(target=worker, args=(i + 1,), daemon=True)
           for i in range(A.tabs)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()

    log(f"\n=== DONE: 注册成功 {state['got']}/{A.count} | 尝试 {state['done']} 次 | "
        f"{(time.time()-t0)/60:.1f} 分钟 ===")
    log(f"  key 文件 → {TXT}（verified=pending，稍后统一跑邮箱验证）")


if __name__ == "__main__":
    main()
