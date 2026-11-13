#!/usr/bin/env python3
"""river/google_login.py — 用 Google 账号登录 river.ai 并取回 access_token。

流程：
  console.river.ai → (未登录) → WorkOS AuthKit sign-in
  → 点Continue with Google → Google 账号登录
  → 回调 auth.console.river.ai → console.river.ai/auth/callback
  → 落地控制台，sessionStorage 里就有 accessToken

用法：
  .venv/bin/python river/google_login.py --email you@example.com --password 'xxx'
  .venv/bin/python river/google_login.py --email you@example.com --password 'xxx' --headed
"""
import argparse
import base64
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from river.browser import Browser, CONSOLE, log, _signup_url  # noqa: E402

AUTH = "https://auth.console.river.ai"
LOGIN = "https://login.console.river.ai"
REDIRECT_URI = f"{CONSOLE}/auth/callback"

RUNS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runs")
os.makedirs(RUNS, exist_ok=True)


def shot(b, name):
    """截图留证——每个阶段都存，方便回看卡在哪。"""
    try:
        d = b.call("Page.captureScreenshot", format="png")
        p = os.path.join(RUNS, f"{name}.png")
        with open(p, "wb") as f:
            f.write(base64.b64decode(d["data"]))
        return p
    except Exception as e:
        return f"(截图失败 {str(e)[:40]})"


def state(b):
    """当前页面状态快照。"""
    try:
        url = b.js("return location.href;") or ""
    except Exception:
        url = "(读不到)"
    try:
        title = b.js("return document.title;") or ""
    except Exception:
        title = "(读不到)"
    try:
        txt = (b.js("return (document.body?.innerText||'').slice(0,400);") or "")
    except Exception:
        txt = ""
    try:
        inputs = b.js("""return JSON.stringify([...document.querySelectorAll('input')]
            .map(i=>({n:i.name||i.id||i.type, t:i.type,
                      vis: !!(i.offsetWidth||i.offsetHeight||i.getClientRects().length),
                      ph:(i.placeholder||'').slice(0,20)})));""") or "[]"
    except Exception:
        inputs = "[]"
    return {"url": url, "title": title, "text": txt, "inputs": inputs}


def show(tag, st):
    log(f"[{tag}] {st['title']!r} | {st['url'][:95]}")
    try:
        print(f"      inputs: {st['inputs'][:220]}")
    except Exception:
        pass
    t = (st["text"] or "").replace("\n", " | ")[:180]
    if t:
        print(f"      text: {t}")


def wait_any(b, exprs, timeout=25, step=0.5):
    """等任一条件成立，返回命中的 key。

    exprs: {key: JS 布尔表达式}。表达式会被包成 (() => { return (EXPR) })() 执行。
    坑：条件里写函数时要立刻调用（如 `(() => {...})()`），
    只写函数不调用的话外层 !! 判的是「函数对象存在」，恒为真。
    """
    end = time.time() + timeout
    while time.time() < end:
        for k, e in exprs.items():
            try:
                if b.js(f"return !!({e});"):
                    return k
            except Exception:
                pass
        b.pump(step)
    return None


def do_google_login(email, password, headless=False, port=9455):
    with Browser(headless=headless, port=port,
                 profile=f"/tmp/river-google-{port}") as b:
        # ---- 1. 打开控制台，触发登录 ----
        b.goto(CONSOLE, wait=6)
        show("console", state(b))
        log(f"截图: {shot(b, '01_console')}")

        # 找登录入口（未登录时通常会被重定向到 AuthKit，也可能页面上有 Sign in 按钮）
        if "auth.console.river.ai" in (b.js("return location.href;") or "") or \
           "login.console.river.ai" in (b.js("return location.href;") or ""):
            log("已被重定向到 AuthKit")
        else:
            clicked = b.js("""return (() => {
                const el = [...document.querySelectorAll('a,button')]
                  .find(x => /sign in|log in|登录/i.test(x.innerText||''));
                if (el) { el.click(); return true; }
                return false;
            })();""")
            log(f"点登录入口: {clicked}")
            b.pump(5)

        show("after-login-click", state(b))

        # ---- 2. AuthKit 页面上点 Google ----
        if "Continue with Google" not in (b.text() or ""):
            # 可能已经过了这页，或者文案不同，直接看有没有 Google 链接
            log("当前页未见 'Continue with Google'，尝试直接找 Google 登录链接")
        g = b.js("""return (() => {
            const el = [...document.querySelectorAll('a,button')]
              .find(x => /continue with google|sign in with google/i.test(x.innerText||''));
            if (el) { el.click(); return true; }
            return false;
        })();""")
        log(f"点 Continue with Google: {g}")
        b.pump(7)

        st = state(b)
        show("google-page", st)
        log(f"截图: {shot(b, '02_google')}")

        # ---- 3. Google 账号页：填邮箱 ----
        if "accounts.google.com" in st["url"] or "accounts.google" in st["url"]:
            log("在 Google 登录域")
            # 两个坑：
            # 1) Google 邮箱框是 input[name=identifier]，type 是 text 不是 email
            # 2) 页面上常驻一个 input[name=hiddenPassword]（隐藏），
            #    所以「等 input[type=password]」会立刻误命中，跳过邮箱步骤。
            #    所有等待都必须限定可见性。
            ID_SEL = "input[name='identifier']"
            VIS = "(e.offsetWidth>0 || e.offsetHeight>0 || e.getClientRects().length>0)"
            got_id = wait_any(b, {
                "id": "(() => { const e = document.querySelector(" + json.dumps(ID_SEL) + ");"
                      " return !!(e && (" + VIS + ")); })()"
            }, timeout=25)
            log(f"等到可见邮箱框: {bool(got_id)}")

            if got_id:
                filled = b.js(f"""return (() => {{
                    const el = document.querySelector({json.dumps(ID_SEL)});
                    if (!el) return null;
                    const setter = Object.getOwnPropertyDescriptor(
                        Object.getPrototypeOf(el), 'value').set;
                    setter.call(el, {json.dumps(email)});
                    el.dispatchEvent(new Event('input', {{bubbles:true}}));
                    el.dispatchEvent(new Event('change', {{bubbles:true}}));
                    return el.value;
                }})();""")
                log(f"填邮箱结果: {str(filled)[:60]}")
                shot(b, "03_google_email")
                b.pump(1.0)
                # 「下一步」是 div[role=button]，不是 <button>
                nxt = b.js("""return (() => {
                    const el = [...document.querySelectorAll('[role=button],button,div')]
                      .find(x => /^(下一步|next)$/i.test((x.innerText||'').trim()));
                    if (el) { el.click(); return true; }
                    return false;
                })();""")
                log(f"点下一步: {nxt}")
                b.pump(8)

            # ---- 4. 密码（只等可见的）----
            k = wait_any(b, {
                "pw": "(()=>{const e=[...document.querySelectorAll('input[type=password]')]"
                      f".find(x=>x.name!=='hiddenPassword' && ({VIS})); return !!e; }})()",
                "2fa": "(()=>{const e=document.querySelector('input[name=otpPin]');" + f"return !!(e && ({VIS}));}})()",
                "captcha": "!!document.querySelector('#captcha-form, input[name=arcResponse]')",
                "challenge": "document.body.innerText.match(/unusual|suspicious|verify it|确认是您|验证身份|不是您本人|异常活动/i) ? 1 : 0",
                "recover": "document.body.innerText.match(/forgot|try another way|忘记密码|其他方式|重设密码/i) ? 1 : 0",
            }, timeout=30)
            log(f"密码页等到: {k}")

            if k == "pw":
                ok_pw = b.js(f"""return (() => {{
                    const el = [...document.querySelectorAll('input[type=password]')]
                      .find(e => e.name !== 'hiddenPassword' &&
                                 (e.offsetWidth>0 || e.offsetHeight>0));
                    if (!el) return 'no-visible-pw';
                    const setter = Object.getOwnPropertyDescriptor(
                        Object.getPrototypeOf(el), 'value').set;
                    setter.call(el, {json.dumps(password)});
                    el.dispatchEvent(new Event('input', {{bubbles:true}}));
                    el.dispatchEvent(new Event('change', {{bubbles:true}}));
                    return el.value.length;
                }})();""")
                log(f"填密码结果: {ok_pw}")
                shot(b, "04_google_pw")
                b.pump(0.8)
                b.js("""return (() => {
                    const el = [...document.querySelectorAll('[role=button],button,div')]
                      .find(x => /^(下一步|next|登录|sign in)$/i.test((x.innerText||'').trim()));
                    if (el) { el.click(); return true; }
                    return false;
                })();""")
                b.pump(10)
            elif k == "2fa":
                log("⚠ 命中两步验证，需要短信/Authenticator 代码", "WARN")
                shot(b, "04_google_2fa")
                return {"ok": False, "error": "需要 2FA 验证码"}
            elif k == "captcha":
                log("⚠ 命中 Google 验证码", "WARN")
                shot(b, "04_google_captcha")
                return {"ok": False, "error": "命中 Google 验证码"}
            elif k == "recover":
                log("⚠ Google 要求额外验证（密码过期或异地登录）", "WARN")
                shot(b, "04_google_recover")
                return {"ok": False, "error": "Google 要求额外验证"}
            elif k == "challenge":
                log("⚠ Google 判定异常活动", "WARN")
                shot(b, "04_google_challenge")
                return {"ok": False, "error": "Google 判定异常活动"}

            st = state(b)
            show("after-pw", st)
            log(f"截图: {shot(b, '05_after_pw')}")

            # ---- 5. 可能还有 OAuth 授权同意页 ----
            if "accounts.google.com" in st["url"] and "river" in (st["text"] or "").lower():
                log("疑似 Google 授权同意页")
                acc = b.js("""return (() => {
                    const el = [...document.querySelectorAll('[role=button],button,div')]
                      .find(x => /^(继续|continue|允许|allow)$/i.test((x.innerText||'').trim()));
                    if (el) { el.click(); return true; }
                    return false;
                })();""")
                log(f"点同意: {acc}")
                b.pump(9)
                st = state(b)

            # ---- 6. 等回 river ----
            got = wait_any(b, {
                "river": "location.hostname.includes('river.ai')",
                "still-google": "location.hostname.includes('google.com')",
            }, timeout=45)
            log(f"回跳结果: {got}")
            st = state(b)
            show("final", st)
            log(f"截图: {shot(b, '06_final')}")

            if got != "river":
                return {"ok": False, "error": f"未回到 river，当前 {st['url'][:90]}",
                        "text": st["text"][:400]}

        # ---- 6. 取 token ----
        log("等控制台加载完，取会话里的 token…")
        end = time.time() + 30
        tok = {}
        while time.time() < end:
            try:
                raw = b.js("""return (() => {
                    const out = {};
                    try {
                      for (let i=0;i<sessionStorage.length;i++){
                        const k = sessionStorage.key(i); out['ss:'+k] = sessionStorage.getItem(k);
                      }
                    } catch(e){}
                    try {
                      for (let i=0;i<localStorage.length;i++){
                        const k = localStorage.key(i); out['ls:'+k] = localStorage.getItem(k);
                      }
                    } catch(e){}
                    return JSON.stringify(out);
                })();""") or "{}"
                tok = json.loads(raw)
            except Exception:
                pass
            if any("accesstoken" in k.lower() for k in tok):
                break
            b.pump(1.5)

        log(f"会话键: {sorted(tok)}")
        result = {"ok": False, "session": {}, "url": b.js("return location.href;") or ""}
        for k, v in tok.items():
            result["session"][k] = (v or "")[:200]
        low = {k.lower(): v for k, v in tok.items()}
        for k, v in tok.items():
            if "accesstoken" in k.lower():
                result["access_token"] = v
            elif "refreshtoken" in k.lower():
                result["refresh_token"] = v
            elif k.endswith("user"):
                try:
                    result["user"] = json.loads(v)
                except Exception:
                    result["user"] = {"raw": v[:120]}

        # 顺便确认登录态
        try:
            me = b.js(f"""return fetch('{CONSOLE}/api/me', {{
                headers: {{'Accept':'application/json',
                           'Authorization': 'Bearer ' + (sessionStorage.getItem('accessToken')||'')}}
            }}).then(r=>r.text());""")
            result["me"] = me[:300]
        except Exception:
            pass

        result["ok"] = bool(result.get("access_token"))
        return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--out", default="river/results.jsonl")
    a = ap.parse_args()

    r = do_google_login(a.email, a.password, headless=not a.headed)

    print("\n=== RESULT ===")
    for k, v in r.items():
        if k in ("session",):
            print(f"  {k}: {sorted(v)}")
        elif isinstance(v, str) and len(v) > 200:
            print(f"  {k}: {v[:200]}…")
        else:
            print(f"  {k}: {v}")

    if r.get("ok"):
        print("\n" + "=" * 60)
        print("token 已取到，接下来跑越权探测：")
        print(f"  export RIVER_TOKEN='{r['access_token']}'")
        print(f"  export RIVER_ME='{a.email}'")
        print(f"  python3 river/probe_authz.py")
        print("=" * 60)
        with open(a.out, "a") as f:
            f.write(json.dumps({
                "email": a.email,
                "access_token": r.get("access_token", ""),
                "refresh_token": r.get("refresh_token", ""),
                "user": r.get("user", {}),
                "ok": True,
            }, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()