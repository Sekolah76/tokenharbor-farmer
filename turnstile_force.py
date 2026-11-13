#!/usr/bin/env python3
"""turnstile_force.py — 强制渲染 Turnstile 并取 token。

背景（2026-10-20 实测）：
  · /api/auth/signup-precheck 一直返回 {"needCaptcha": false}
  · 前端因此**不渲染** Turnstile 组件（window.turnstile 都不加载）
  · 但提交时服务端仍回 {"error":"Please complete the human check...","needCaptcha":true}
  → 服务端在提交环节独立二次判定，"前端不需要"不代表"服务端放行"。

对策：不管前端渲染不渲染，自己把 Turnstile 脚本注入页面，用同一 sitekey/action
      把验证跑完，拿到 cf-turnstile-response 再拼进注册请求。

用法:
  .venv/bin/python turnstile_force.py [--proxy URL] [--headless]
"""
import argparse
import sys
import time

SITEKEY = "0x4AAAAAADBuC8Knz1EJZx9-"
ACTION = "signup"
URL = "https://tokenharbor.ai/login?mode=signup"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")

STEALTH = r"""
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN','zh','en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
window.chrome = window.chrome || {runtime: {}};
"""


def force_solve(proxy=None, headless=False, timeout=120, verbose=True):
    from patchright.sync_api import sync_playwright

    launch = {"headless": headless,
              "args": ["--disable-blink-features=AutomationControlled",
                       "--no-first-run", "--disable-dev-shm-usage"]}
    if proxy:
        launch["proxy"] = {"server": proxy}

    token = None
    with sync_playwright() as p:
        b = p.chromium.launch(**launch)
        ctx = b.new_context(user_agent=UA, viewport={"width": 1440, "height": 900},
                            locale="zh-CN", timezone_id="Asia/Shanghai")
        ctx.add_init_script(STEALTH)
        page = ctx.new_page()
        if verbose:
            print("[1] 打开注册页", flush=True)
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)

        # 立刻记下页面自己的 device_fingerprint（服务端会拿它跟 turnstile 关联）
        fp = page.evaluate(
            "()=>{const e=document.querySelector('input[name=device_fingerprint]');"
            "return e?e.value:''}")
        if verbose:
            print(f"[2] device_fingerprint = {fp}", flush=True)

        # 注入 Turnstile 脚本（前端没加载，我们自己来）
        if verbose:
            print("[3] 注入 challenges.cloudflare.com/turnstile/v0/api.js", flush=True)
        page.evaluate("""() => {
            if (window.__tsInjecting) return;
            window.__tsInjecting = true;
            window.__tsToken = null;
            const s = document.createElement('script');
            s.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
            s.async = true; s.defer = true;
            s.onload = () => { window.__tsLoaded = true; };
            s.onerror = () => { window.__tsError = true; };
            document.head.appendChild(s);
            const d = document.createElement('div');
            d.id = '__ts_box';
            d.style.cssText = 'position:fixed;left:20px;top:20px;z-index:999999;';
            document.body.appendChild(d);
        }""")

        # 等脚本加载后 render
        deadline = time.time() + timeout
        rendered = False
        while time.time() < deadline:
            st = page.evaluate("()=>({loaded: !!window.__tsLoaded, err: !!window.__tsError, ts: !!window.turnstile, tok: window.__tsToken})")
            if st.get("tok"):
                token = st["tok"]
                break
            if st.get("ts") and not rendered:
                page.evaluate(f"""() => {{
                    try {{
                      window.turnstile.render('#__ts_box', {{
                        sitekey: '{SITEKEY}',
                        action: '{ACTION}',
                        callback: (t) => {{ window.__tsToken = t; }},
                        'error-callback': (e) => {{ window.__tsErrDetail = String(e); return true; }},
                        'timeout-callback': () => {{ window.__tsTimeout = true; }}
                      }});
                      window.__tsRendered = true;
                    }} catch(e) {{ window.__tsRenderEx = String(e); }}
                }}""")
                rendered = True
                if verbose:
                    print("[4] 已调用 turnstile.render，等待 token…", flush=True)
            page.wait_for_timeout(600)

        if verbose:
            detail = page.evaluate("()=>({loaded:!!window.__tsLoaded,err:!!window.__tsError,ts:!!window.turnstile,rendered:!!window.__tsRendered,ex:window.__tsRenderEx||null,errDetail:window.__tsErrDetail||null,timeout:!!window.__tsTimeout})")
            print(f"[5] 状态: {detail}", flush=True)
            print(f"    {'✓ token 拿到，长度 ' + str(len(token)) if token else '✗ 没拿到 token'}", flush=True)
        try:
            ctx.close(); b.close()
        except Exception:
            pass
    return token


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--proxy", default=None)
    ap.add_argument("--headless", action="store_true")
    A = ap.parse_args()
    t0 = time.time()
    tok = force_solve(proxy=A.proxy, headless=A.headless)
    print(f"耗时 {time.time()-t0:.1f}s")
    if tok:
        print("token 前 80:", tok[:80])
    else:
        sys.exit(1)
