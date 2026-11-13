#!/usr/bin/env python3
"""river/capture_signup.py — 用真实 Chrome 抓注册表单的 Server Action 提交协议。

为什么需要它：AuthKit 托管页的表单是 React Server Component + useActionState，
提交协议（Next-Action 头、body 字段编码、是否需要额外 header）不写在 HTML 里，
纯 HTTP 复现会撞 "Server action not found."。这里用 CDP 驱动真实浏览器跑一遍，
把浏览器实际发出的请求原样抓下来，作为 client.submit_signup 的事实依据。

用法：
    .venv/bin/python river/capture_signup.py                # 只抓，不真注册
    .venv/bin/python river/capture_signup.py --email x@y.z # 用真邮箱跑完整注册
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PORT = 9333
PROFILE = "/tmp/river-cdp-profile"

CID = "client_01M0TG5W3GBNMFS87K40PYHM98"
REDIRECT = "https://console.river.ai/auth/callback"

URL = (f"https://login.console.river.ai/sign-up?client_id={CID}"
       f"&redirect_uri=REDIRECT_ENC&screen_hint=sign-up").replace(
           "REDIRECT_ENC", urllib.parse.quote(REDIRECT, safe=""))


def start_chrome():
    subprocess.Popen([
        CHROME, "--headless=new", "--no-sandbox", "--disable-gpu",
        f"--remote-debugging-port={PORT}", f"--user-data-dir={PROFILE}",
        # CDP 3.x 起拒绝非空 Origin 的 ws 连接，loopback 调试需显式放行
        "--remote-allow-origins=*",
        "--no-first-run", "--no-default-browser-check",
        "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=2) as r:
                json.load(r)
                return True
        except Exception:
            time.sleep(0.5)
    return False


def cdp_targets():
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=5) as r:
        return json.load(r)


def wait_for(target_pred, timeout=20):
    """轮询 /json/list 直到出现满足条件的 target（headless 下 about:blank 可能延迟出现）。"""
    end = time.time() + timeout
    while time.time() < end:
        try:
            for t in cdp_targets():
                if target_pred(t):
                    return t
        except Exception:
            pass
        time.sleep(0.4)
    return None


class CDP:
    def __init__(self, ws_url):
        import websocket
        self.ws = websocket.create_connection(ws_url, timeout=60)
        self.i = 0
        self.events = []

    def send(self, method, **params):
        self.i += 1
        self.ws.send(json.dumps({"id": self.i, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.i:
                return msg.get("result", msg.get("error"))
            self.events.append(msg)

    def pump(self, seconds):
        end = time.time() + seconds
        self.ws.settimeout(1.0)
        while time.time() < end:
            try:
                self.events.append(json.loads(self.ws.recv()))
            except Exception:
                pass

    def evaluate(self, expr):
        r = self.send("Runtime.evaluate", expression=expr, awaitPromise=True,
                      returnByValue=True)
        return (r or {}).get("result", {}).get("value")


def main():
    import urllib.parse  # noqa: F401  (URL 在模块级已用)
    if not start_chrome():
        print("Chrome 起不来")
        return 1
    try:
        tgt = wait_for(lambda t: t.get("type") == "page")
        if not tgt:
            print("拿不到 page target")
            return 1
        c = CDP(tgt["webSocketDebuggerUrl"])
        c.send("Page.enable")
        c.send("Runtime.enable")
        c.send("Network.enable")

        # 用 CDP 的 Network 域抓 POST —— fetch 拦截器拿不到 FormData 的完整序列化，
        # 而 Network.requestWillBeSent 能看到真实的 postData 与全部请求头
        posts = []

        # 拦截 fetch，把请求头和 body 记到 window.__cap
        c.send("Page.addScriptToEvaluateOnNewDocument", source=r"""
        window.__cap = [];
        const of = window.fetch;
        window.fetch = async function(input, init) {
          try {
            const url = typeof input === 'string' ? input : (input && input.url);
            const method = (init && init.method) || (input && input.method) || 'GET';
            const headers = {};
            if (init && init.headers) {
              (init.headers.forEach ? init.headers.forEach((v,k)=>headers[k]=v)
                                    : Object.entries(init.headers).forEach(([k,v])=>headers[k]=v));
            }
            // FormData 要展开成键值对，否则拿到的是 '[object FormData]'
            let form = null;
            const b = init && init.body;
            if (b instanceof FormData) {
              form = {};
              for (const [k, v] of b.entries()) {
                if (typeof v === 'string') form[k] = v.length > 300 ? v.slice(0,300)+'…' : v;
                else form[k] = '[File ' + (v && v.name) + ']';
              }
            } else if (typeof b === 'string') {
              form = {__raw__: b.slice(0, 800)};
            }
            window.__cap.push({url, method, headers, form});
          } catch (e) { window.__cap.push({err: String(e)}); }
          return of.apply(this, arguments);
        };
        """)

        c.send("Page.navigate", url=URL)

        # 等 load 事件，而不是固定 sleep —— 否则可能在 DOM 就绪前就往下走
        loaded = False
        end = time.time() + 25
        while time.time() < end:
            c.pump(0.5)
            for ev in c.events:
                if ev.get("method") == "Page.loadEventFired":
                    loaded = True
            if loaded:
                break
        c.pump(3)          # load 之后 React 还要 hydration
        c.events.clear()

        # 等 React hydration 把 input 渲染出来，而不是只看 readyState
        for _ in range(30):
            n = c.evaluate("document.querySelectorAll('input').length")
            if n:
                break
            c.pump(0.5)

        title = c.evaluate("document.title")
        print(f"[1] 页面标题: {title!r} | input数={c.evaluate("document.querySelectorAll('input').length")}")
        print("[2] input 字段:",
              c.evaluate("JSON.stringify([...document.querySelectorAll('input')]"
                         ".map(i=>i.name+':'+(i.type||'')))"))

        # ---- 第一步：填姓/名/邮箱，点 Continue ----
        c.evaluate("""(() => {
          const set = (sel, v) => {
            const el = document.querySelector(sel); if (!el) return false;
            const proto = Object.getPrototypeOf(el);
            Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, v);
            el.dispatchEvent(new Event('input', {bubbles:true}));
            el.dispatchEvent(new Event('change', {bubbles:true}));
            return true;
          };
          set('input[name=first_name]', 'probe');
          set('input[name=last_name]',  'test');
          set('input[name=email]',      'probe-test@example.invalid');
          const btn = [...document.querySelectorAll('button')]
            .find(b => /continue/i.test(b.innerText||''));
          if (btn) { btn.click(); return 'clicked: ' + btn.innerText; }
          return 'no continue button';
        })()""")
        c.pump(6)
        print("[3] 点 Continue 后页面:", (c.evaluate("document.body.innerText") or "")[:120].replace("\n"," | "))
        print("    现在的 input:",
              c.evaluate("JSON.stringify([...document.querySelectorAll('input')]"
                         ".map(i=>i.name+':'+(i.type||'')))"))

        # ---- 第二步：点完 Continue 后会跳到 /sign-up/password，填密码并提交 ----
        # AuthKit 是两步表单：先姓名+邮箱（POST /sign-up），再密码（POST /sign-up/password）
        # 两个页面用不同的 server action id，必须分别提取
        for _ in range(20):
            if c.evaluate("!!document.querySelector('input[type=password]')"):
                break
            c.pump(0.5)

        has_pw = c.evaluate("!!document.querySelector('input[type=password]')")
        print("[4] 密码页 input:",
              c.evaluate("JSON.stringify([...document.querySelectorAll('input')]"
                         ".map(i=>i.name+':'+(i.type||'')))"))
        print("    URL:", c.evaluate("location.pathname"))
        if has_pw:
            c.evaluate("""(() => {
              const el = document.querySelector('input[type=password]');
              const proto = Object.getPrototypeOf(el);
              Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, 'Aa1234567890xyz');
              el.dispatchEvent(new Event('input', {bubbles:true}));
              el.dispatchEvent(new Event('change', {bubbles:true}));
              const btn = [...document.querySelectorAll('button[type=submit]')]
                .find(b => !/google/i.test(b.innerText||''));
              if (btn) { btn.click(); return 'submitted'; }
              return 'no submit';
            })()""")
            c.pump(8)

        # 从 Network 域事件里捞 POST（fetch 拦截器对 FormData 不可靠）
        for ev in c.events:
            if ev.get("method") == "Network.requestWillBeSent":
                req = ev["params"]["request"]
                if req.get("method") == "POST" and "sign-up" in req.get("url", ""):
                    posts.append({
                        "url": req["url"],
                        "headers": req.get("headers", {}),
                        "postData": (req.get("postData") or "")[:2000],
                    })
        c.events.clear()

        print(f"\n[4] Network 抓到的 sign-up POST: {len(posts)} 条")
        for i, p in enumerate(posts):
            print(f"\n  --- POST #{i+1} ---")
            print("  URL:", p["url"][:170])
            for k in ("accept", "next-action", "next-router-state-tree",
                      "x-deployment-id", "content-type", "origin", "referer"):
                v = p["headers"].get(k) or p["headers"].get(k.title()) or ""
                if v:
                    print(f"    {k}: {str(v)[:150]}")
            print("  postData:", repr(p["postData"][:700]))

        # 提取字段名清单，方便直接对齐 Python 侧的 multipart
        print("\n[7] postData 里的表单字段名:")
        for p in posts:
            names = re.findall(r'name="([^"]+)"', p["postData"])
            if names:
                print(f"   {sorted(set(names))}")

        print("\n[5] 页面状态:", c.evaluate("document.body.innerText.slice(0,300)"))
        return 0
    finally:
        subprocess.run(["pkill", "-f", f"remote-debugging-port={PORT}"])


if __name__ == "__main__":
    sys.exit(main())