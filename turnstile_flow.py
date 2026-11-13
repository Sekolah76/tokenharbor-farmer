#!/usr/bin/env python3
"""turnstile_flow.py — 按站点真实前端流程过 Turnstile 并完成注册。

为什么是这条流程（2026-10-20 逆向 + 实测）：
  React 源码里：
      er = "signup"===c && !B && (!0===U || !!ee?.needCaptcha)
      er ? <Turnstile onVerified={W} /> : <表单…含隐藏 input cf-turnstile-response=B>
  即：
    ① needCaptcha=false 时 → 直接显示表单（不加载 Turnstile）
    ② 提交后服务端返回 needCaptcha:true → 组件重渲染成 Turnstile
    ③ Turnstile 通过 → B 有值 → 表单重新出现，带隐藏 input cf-turnstile-response
    ④ 再提交 → 成功

  所以「硬注入 api.js」不成立（window.turnstile 起不来），
  必须**先提交一次把 needCaptcha 打出来**，让站点自己渲染。

用法：
  .venv/bin/python turnstile_flow.py [--headless] [--proxy URL]
成功输出：signedIn + 邮箱 + 密码 + 注册时的 device_fingerprint
"""
import argparse
import json
import sys
import time

SITEKEY = "0x4AAAAAADBuC8Knz1EJZx9-"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")

STEALTH = r"""
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN','zh','en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
window.chrome = window.chrome || {runtime: {}};
"""


def run(email, pwd, headless=False, proxy=None, verbose=True):
    from patchright.sync_api import sync_playwright

    launch = {"headless": headless,
              "args": ["--disable-blink-features=AutomationControlled",
                       "--no-first-run", "--disable-dev-shm-usage"]}
    if proxy:
        launch["proxy"] = {"server": proxy}

    out = {"email": email, "ok": False, "stage": "", "token": None, "fp": None}
    with sync_playwright() as p:
        b = p.chromium.launch(**launch)
        # 注意：device_scale_factor / timezone_id 会触发站点反爬（只渲染 7 个隐藏字段、
        # 不给 email/password，等于关掉注册入口）。只用最朴素的 context。
        ctx = b.new_context(viewport={"width": 1440, "height": 900})
        ctx.add_init_script(STEALTH)
        page = ctx.new_page()

        def log(m):
            if verbose:
                print(m, flush=True)

        def fill_and_submit():
            """填表并提交。返回 'submitted' / 'no-form' / 'turnstile-shown'"""
            state = page.evaluate("""(creds) => {
                const inputs=[...document.querySelectorAll('input')];
                const em=inputs.find(i=>i.name==='email');
                const pw=inputs.find(i=>i.name==='password');
                if (!em || !pw) {
                    const hasTs=!!document.querySelector('input[name="cf-turnstile-response"]')
                             || [...document.querySelectorAll('iframe')].some(f=>
                                 (f.src||'').includes('challenges.cloudflare.com'));
                    return hasTs ? 'turnstile-shown' : 'no-form';
                }
                const set=(el,v)=>{const s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
                    s.call(el,v); el.dispatchEvent(new Event('input',{bubbles:true}));
                    el.dispatchEvent(new Event('change',{bubbles:true}));};
                set(em, creds.email); set(pw, creds.pwd);
                const fp=document.querySelector('input[name=device_fingerprint]');
                const f=em.closest('form');
                if(!f) return 'no-form';
                setTimeout(()=>{ try{ f.requestSubmit? f.requestSubmit(): f.submit(); }catch(e){} }, 120);
                return 'submitted';
            }""", {"email": email, "pwd": pwd})
            return state

        log(f"[1] 打开注册页")
        page.goto("https://tokenharbor.ai/login?mode=signup",
                  wait_until="domcontentloaded", timeout=60000)
        # 轮询等 React 把表单渲染出来（固定 sleep 会撞上未渲染完）
        for _ in range(30):
            if page.evaluate("()=>!!document.querySelector('input[name=email]')"):
                break
            page.wait_for_timeout(500)
        page.wait_for_timeout(600)

        fp = page.evaluate("()=>{const e=document.querySelector('input[name=device_fingerprint]');"
                           "return e?e.value:''}")
        out["fp"] = fp
        log(f"[2] device_fingerprint = {fp}")

        log("[3] 首次提交（触发 needCaptcha）")
        log("    " + str(fill_and_submit()))
        page.wait_for_timeout(6000)

        # 现在看 Turnstile 有没有出来
        has_ts = page.evaluate("""()=>{
            if (window.turnstile) return true;
            const ifr=[...document.querySelectorAll('iframe')].some(f=>
                (f.src||'').includes('challenges.cloudflare.com'));
            return ifr || !!document.querySelector('#__ts_box');
        }""")
        log(f"[4] Turnstile 已渲染: {has_ts} | window.turnstile: {page.evaluate('()=>typeof window.turnstile')}")

        # 等 token（优先读隐藏 input，其次读 window.__tok）
        deadline = time.time() + 150
        tok = None
        while time.time() < deadline:
            tok = page.evaluate("""()=>{
                const e=document.querySelector('input[name="cf-turnstile-response"]');
                if (e && e.value && e.value.length>20) return e.value;
                return null;
            }""")
            if tok:
                break
            # 轻微人类动作提升评分
            try:
                page.mouse.move(720, 420); page.wait_for_timeout(200)
                page.mouse.move(760, 470)
            except Exception:
                pass
            page.wait_for_timeout(1200)

        out["token"] = tok
        if not tok:
            out["stage"] = "no-token"
            log("[5] ✗ 没拿到 token（Turnstile 未通过）")
            try:
                ctx.close(); b.close()
            except Exception:
                pass
            return out

        log(f"[5] ✓ token 长度 {len(tok)}")

        # 填表再提交（这次带 token）
        page.wait_for_timeout(800)
        log("[6] 带 token 二次提交")
        st = fill_and_submit()
        log("    " + str(st))
        page.wait_for_timeout(9000)

        # 判断成功：URL 变化 / 出现 dashboard 元素 / 响应含 signedIn
        body = page.evaluate("()=>document.body.innerText.slice(0,400)")
        url = page.url
        ok = ("signedIn" in body) or ("dashboard" in url) or ("Sign out" in body) \
             or ("Credits" in body and "Sign in" not in body[:200])
        out["ok"] = bool(ok)
        out["stage"] = "done"
        out["url"] = url
        log(f"[7] URL={url} | 判定 ok={ok}")
        log(f"    body: {body[:200]!r}")

        try:
            ctx.close(); b.close()
        except Exception:
            pass
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--proxy", default=None)
    ap.add_argument("--email", default=None)
    A = ap.parse_args()

    import random
    import string
    import os
    sys.path.insert(0, "/Users/zxc/tokenharbor-farmer")
    os.environ.setdefault("TOR_AUTOSTART", "0")
    import th_auto_register as tar

    email = A.email
    if not email:
        try:
            email, _ = tar._cfmail_create()
        except Exception:
            email = f"probe{random.randint(10000,99999)}@hellocat.ccwu.cc"
    pwd = ''.join(random.choices(string.ascii_letters + string.digits, k=12)) + '!Aa1'
    print("测试邮箱:", email)
    t0 = time.time()
    res = run(email, pwd, headless=A.headless, proxy=A.proxy)
    print(f"\n耗时 {time.time()-t0:.1f}s")
    print(json.dumps(res, ensure_ascii=False, indent=1)[:800])
