#!/usr/bin/env python3
"""river/browser.py — 用真实 Chrome 跑完注册（CDP 直连，无 playwright 依赖）。

为什么必须走浏览器：AuthKit 托管页的表单是 React Server Component +
useActionState，提交走 Next.js Server Action。除了 action id、state-tree 头，
还有 signals 遥测指纹、__Host-state cookie 绑定、以及服务端对客户端环境的
额外校验。纯 HTTP 复现会稳定拿到 text/plain 的 "Server action not found."。

实测记录（capture_signup.py 抓 postData）：
    第 1 步  POST /sign-up            action 408cf059...   字段 _1_email/_1_first_name/...
    第 2 步  POST /sign-up/password   action 408b9aeb...   字段 _1_password/...
    两次都要 __Host-state-<sid> cookie + next-router-state-tree + x-deployment-id

依赖：websocket-client（pip install websocket-client）
用法：
    from river.browser import register_via_browser
    r = register_via_browser(email, password)
"""
import json
import os
import random
import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.request

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
]

AUTH = "https://auth.console.river.ai"
LOGIN = "https://login.console.river.ai"
CONSOLE = "https://console.river.ai"
REDIRECT_URI = f"{CONSOLE}/auth/callback"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")


def find_chrome():
    for p in CHROME_CANDIDATES:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    p = shutil.which("chromium") or shutil.which("google-chrome")
    return p


def log(msg, lv="INFO"):
    print(f"  [{time.strftime('%H:%M:%S')}] [{lv}] {msg}", flush=True)


class BrowserClosed(RuntimeError):
    """CDP 连接断了 —— 浏览器被关掉、崩溃，或 profile 被占用。"""


class Browser:
    """最小 CDP 客户端：起 Chrome、导航、填表、点按钮、读页面。"""

    def __init__(self, port=9444, profile="/tmp/river-browser-profile", headless=True,
                 proxy=None):
        """proxy: "host:port" 或 "socks5://host:port"。

        为什么要给浏览器挂代理：WorkOS Radar 的判定里 IP 信誉权重很高，
        而 requests 挂代理 ≠ Chrome 挂代理 —— 两者出口必须一致，否则
        「注册请求走代理、后续 token 交换走直连」本身就是异常特征。
        """
        self.port = port
        self.profile = profile
        self.headless = headless
        self.proxy = proxy
        self.proc = None
        self.ws = None
        self._id = 0
        self.events = []

    # ------------------------------------------------------------ 生命周期

    def start(self):
        exe = find_chrome()
        if not exe:
            raise RuntimeError("找不到 Chrome/Chromium，请安装或改 CHROME_CANDIDATES")
        args = [exe]
        if self.headless:
            args.append("--headless=new")
        args += [
            "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self.profile}",
            "--remote-allow-origins=*",       # CDP 3.x 起默认拒绝带 Origin 的 ws
            "--no-first-run", "--no-default-browser-check",
            "about:blank",
        ]
        if self.proxy:
            args.append(f"--proxy-server={self.proxy}")
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
        for _ in range(60):
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{self.port}/json/version", timeout=2):
                    import websocket
                    tgt = self._first_page()
                    self.ws = websocket.create_connection(
                        tgt["webSocketDebuggerUrl"], timeout=60)
                    break
            except Exception:
                time.sleep(0.4)
        else:
            raise RuntimeError("Chrome 启动超时")
        self.call("Page.enable")
        self.call("Runtime.enable")
        self.call("Network.enable")
        # 抹掉最明显的自动化指纹
        self.call("Page.addScriptToEvaluateOnNewDocument", source=r"""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
            window.chrome = {runtime: {}};
        """)
        return self

    def _first_page(self):
        for _ in range(50):
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{self.port}/json/list", timeout=3) as r:
                    for t in json.load(r):
                        if t.get("type") == "page":
                            return t
            except Exception:
                pass
            time.sleep(0.3)
        raise RuntimeError("拿不到 page target")

    def stop(self):
        try:
            if self.ws:
                self.ws.close()
        except Exception:
            pass
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except Exception:
                self.proc.kill()

    def __enter__(self):
        return self.start()

    def __exit__(self, *a):
        self.stop()

    # ------------------------------------------------------------ CDP

    @property
    def alive(self):
        """连接是否还活着 —— headed 模式下用户可能直接关窗口。"""
        if not self.ws:
            return False
        try:
            self.ws.ping()
            return True
        except Exception:
            return False

    def call(self, method, **params):
        self._id += 1
        # 关键：pump() 为了轮询事件把 timeout 调成了 0.5，这里必须恢复成正常值，
        # 否则下一次 call 会用 0.5s 超时等一个可能几秒才回的 CDP 响应 →假性断连。
        try:
            self.ws.settimeout(60)
        except Exception:
            pass
        try:
            self.ws.send(json.dumps({"id": self._id, "method": method, "params": params}))
        except Exception as e:
            raise BrowserClosed(f"{method} 发送失败：{e}") from e
        while True:
            try:
                msg = json.loads(self.ws.recv())
            except Exception as e:
                raise BrowserClosed(f"{method} 等待响应时连接断开：{e}") from e
            if msg.get("id") == self._id:
                if "error" in msg:
                    raise RuntimeError(f"{method} 失败: {msg['error']}")
                return msg.get("result", {})
            self.events.append(msg)

    def pump(self, seconds):
        end = time.time() + seconds
        try:
            self.ws.settimeout(0.5)
        except Exception:
            return                      # 连接已死，静默返回让调用方走 alive 检查
        while time.time() < end:
            try:
                self.events.append(json.loads(self.ws.recv()))
            except Exception:
                pass

    def js(self, expr):
        r = self.call("Runtime.evaluate", expression=f"(() => {{ {expr} }})()",
                      awaitPromise=True, returnByValue=True)
        res = r.get("result", {})
        if res.get("subtype") == "error":
            raise RuntimeError(res.get("description", "JS 错误"))
        return res.get("value")

    def goto(self, url, wait=6.0):
        self.call("Page.navigate", url=url)
        self.pump(wait)
        self.events.clear()

    def wait_for(self, expr, timeout=25, interval=0.4):
        end = time.time() + timeout
        while time.time() < end:
            try:
                if self.js(f"return !!{expr};"):
                    return True
            except Exception:
                pass
            self.pump(interval)
        return False

    def text(self, selector="body"):
        return self.js(f"return (document.querySelector({json.dumps(selector)})"
                       f"?.innerText || '').slice(0, 2000);") or ""

    # ------------------------------------------------------------ 交互

    def fill(self, selector, value):
        """填输入框。

        React 受控组件不认直接赋值 —— 必须走原型上的 value setter 再派发
        input/change 事件，否则 React 状态不更新，点提交会拿到空值。
        """
        return self.js(f"""
          const el = document.querySelector({json.dumps(selector)});
          if (!el) return false;
          const setter = Object.getOwnPropertyDescriptor(
              Object.getPrototypeOf(el), 'value').set;
          setter.call(el, {json.dumps(value)});
          el.dispatchEvent(new Event('input', {{bubbles:true}}));
          el.dispatchEvent(new Event('change', {{bubbles:true}}));
          return true;
        """)

    def get_value(self, selector):
        return self.js(f"return (document.querySelector({json.dumps(selector)})"
                       f"?.value || '');") or ""

    def click_text(self, pattern):
        return self.js(f"""
          const btn = [...document.querySelectorAll('button,[role=button]')]
            .find(b => new RegExp({json.dumps(pattern)}, 'i').test(b.innerText||''));
          if (!btn) return false;
          btn.click();
          return true;
        """)

    def click_submit(self, skip=None):
        return self.js(f"""
          const skip = {json.dumps(skip) if skip else 'null'};
          const btn = [...document.querySelectorAll('button[type=submit]')]
            .find(b => !/google/i.test(b.innerText||''));
          if (!btn) return false;
          if (skip && new RegExp(skip,'i').test(btn.innerText||'')) return false;
          btn.click();
          return true;
        """)


# ---------------------------------------------------------------- 配置


def fetch_config():
    import requests
    s = requests.Session()
    s.headers["User-Agent"] = UA
    r = s.get(f"{CONSOLE}/api/auth/config", timeout=30)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------- 注册


PHONE_MARKERS = (
    "verify your phone number", "enter your phone number",
    "enter the code sent to your phone", "error sending sms challenge",
    "only mobile phone numbers are supported", "phone number is not available",
)


def page_has_phone_challenge(text):
    low = (text or "").lower()
    return any(m in low for m in PHONE_MARKERS)


def _signup_url(client_id, mode="root"):
    """注册入口 URL。

    实测（不同出口 IP 下多轮对比）三件事：

      login.console.river.ai/?client_id=…&redirect_uri=…   ✅ 正常渲染
      login.console.river.ai/sign-up?…&screen_hint=sign-up  ❌ Cloudflare「请稍候…」
      console.river.ai/login                                ❌ chrome-error（DNS 被节点拦）

    带 screen_hint=sign-up 的 sign-up 直达链会触发 CF 的额外规则，
    所以默认从根路径进（页面自带 "Don't have an account? Sign up" 链接），
    由页面自己跳到注册态。
    """
    q = (f"client_id={urllib.parse.quote(client_id)}"
         f"&redirect_uri={urllib.parse.quote(REDIRECT_URI, safe='')}")
    if mode == "root":
        return f"{LOGIN}/?{q}"
    if mode == "signup-no-hint":
        return f"{LOGIN}/sign-up?{q}"
    return f"{LOGIN}/sign-up?{q}&screen_hint=sign-up"


def register_via_browser(email, password, client_id=None, first_name="Test",
                         last_name="User", timeout=90, skip_phone=True,
                         headless=True, proxy=None, human_pace=False,
                         port=9444, profile=None):
    """用真实 Chrome 跑完注册，返回结果 dict。

    流程：/sign-up 填姓名+邮箱 → Continue → /sign-up/password 填密码 → 提交
          → WorkOS 回调 console.river.ai/auth/callback → 落地控制台

    参数（对抗 WorkOS Radar 的三个杠杆）：
      headless=False   有界面模式。headless 在 TLS/JA3 指纹与 Client Hints 上
                       与真 Chrome 差距最大，是 Radar 判定「不像人」的主要依据。
      proxy="host:port"或 "socks5://host:port"
                       IP 信誉权重很高。必须给浏览器挂，不能只给 requests 挂 ——
                       两者出口不一致本身就是异常特征。
      human_pace=True  每个字段之间插入随机延时。默认是秒填完三个框立刻点
                       Continue，行为时序特征很重。

    skip_phone=True 时，若页面出现手机验证要求则标记 phone_verification=True
    （River 当前不会触发：无 sms 通道、无 tel 输入框）。
    """
    cfg = fetch_config()
    client_id = client_id or cfg.get("workos_client_id")
    if not client_id:
        return {"ok": False, "error": "no_client_id", "email": email}

    out = {
        "email": email, "password": password, "ok": False, "error": "",
        "phone_verification": False, "access_token": "", "refresh_token": "",
        "user_id": "", "console_url": "", "screenshotted": False,
        "headless": headless, "proxy": bool(proxy), "human_pace": human_pace,
    }

    def pace():
        """拟人节奏：human_pace 开启时给一点随机延时。"""
        if human_pace:
            b.pump(0.4 + random.random() * 0.9)

    with Browser(headless=headless, proxy=proxy, port=port,
                 profile=profile or f"/tmp/river-browser-profile-{port}") as b:
        b.goto(_signup_url(client_id), wait=9)

        title = b.js("return document.title;") or ""
        log(f"入口页: {title!r} | headless={headless} proxy={'有' if proxy else '无'}")

        # 根路径进来是 Sign in 页 —— 判断依据用「有没有 first_name 框」（注册页独有），
        # 别用 title：登录页 title 是 'Sign in'，永远不含 "sign up"，靠 title 判断不触发
        if not b.js("return !!document.querySelector('input[name=first_name]');"):
            log("当前是登录页（无 first_name 框），点 Sign up 切到注册")
            clicked = b.js("""return (() => {
                const el = [...document.querySelectorAll('a,button,[role=link]')]
                  .find(x => /^sign ?up$/i.test((x.innerText||'').trim()));
                if (el) { el.click(); return true; }
                return false;
            })();""")
            log(f"点 Sign up 链接: {clicked}")
            # 客户端路由切换，等注册表单（first_name 框）出现
            if not b.wait_for("document.querySelector('input[name=first_name]')",
                              timeout=20):
                body = b.text()
                out["error"] = f"点了 Sign up 但注册表单没出来: {body[:120]}"
                log(out["error"], "WARN")
                return out
            log(f"已切到注册页: {(b.js('return location.pathname;') or '')!r}")

        # CF 挑战页判定（「请稍候…」）—— 有时会自己过，给它一段时间
        if "请稍候" in title or "just a moment" in title.lower():
            log("撞上 Cloudflare 挑战页，等待自动通过…", "WARN")
            deadline = time.time() + 45
            while time.time() < deadline:
                b.pump(3)
                t2 = b.js("return document.title;") or ""
                if "请稍候" not in t2 and "just a moment" not in t2.lower():
                    log(f"挑战已通过: {t2!r}")
                    title = t2
                    break
            else:
                out["error"] = "Cloudflare 挑战页未通过（换节点再试）"
                out["error_kind"] = "cf_challenge"
                log(out["error"], "ERR")
                return out

        if not b.js("return !!document.querySelector('input[name=email]');"):
            out["error"] = f"页面上没有 email 输入框: {title!r}"
            log(out["error"], "WARN")
            return out

        # ---- 第 1 步：姓名 + 邮箱 ----
        for sel, val in (("input[name=first_name]", first_name),
                         ("input[name=last_name]", last_name),
                         ("input[name=email]", email)):
            b.fill(sel, val)
            pace()
        b.click_text(r"^\s*continue\s*$")

        if not b.wait_for("document.querySelector('input[type=password]')", timeout=25):
            body = b.text()
            log(f"没进到密码页，当前页面：{body[:200]}", "WARN")
            if page_has_phone_challenge(body):
                out["phone_verification"] = True
                out["error"] = "页面要求手机验证"
                return out
            out["error"] = "未进入密码页"
            return out

        # ---- 手机验证检查 ----
        pw_page_text = b.text()
        if page_has_phone_challenge(pw_page_text):
            out["phone_verification"] = True
            out["error"] = "密码页要求手机验证"
            log("检测到手机验证要求", "WARN")
            if not skip_phone:
                return out
            log("按 skip_phone 策略继续尝试提交", "WARN")

        # ---- 第 2 步：密码 ----
        b.fill("input[type=password]", password)
        pace()
        b.pump(0.6)
        log(f"提交前密码框实际值长度 = {len(b.get_value('input[type=password]'))}")
        b.click_submit()

        # ---- 等落地 ----
        # 注意：不能用 "页面文本里没有报错" 当成功判据 —— AuthKit 提交后
        # 会停在原页并静默重渲染。必须以「URL 离开 /sign-up」为准。
        landed = False
        end = time.time() + timeout
        last_url = ""
        while time.time() < end:
            if not b.alive:
                out["error"] = "浏览器连接断开（headed 模式下可能被手动关闭）"
                out["error_kind"] = "browser_closed"
                log(out["error"], "ERR")
                return out
            b.pump(1.0)
            try:
                url = b.js("return location.href;") or ""
            except BrowserClosed:
                out["error"] = "浏览器连接断开"
                out["error_kind"] = "browser_closed"
                return out
            except Exception:
                continue
            if url != last_url:
                log(f"  URL → {url[:100]}")
                last_url = url
            if "console.river.ai" in url and "/auth/callback" in url:
                landed = True
                out["console_url"] = url
                break
            if "console.river.ai" in url and "login" not in url and "sign" not in url:
                landed = True
                out["console_url"] = url
                break
            body = b.text()
            if page_has_phone_challenge(body):
                out["phone_verification"] = True
                out["error"] = "提交后要求手机验证"
                return out
            if re.search(r"sign-?up successful|check your email|verify your email", body, re.I):
                landed = True
                break

        try:
            page = b.text()
        except BrowserClosed:
            page = ""
        if not landed:
            out["error"] = f"未离开密码页（URL={last_url[:90]}），页面: {page[:160]}"
            log(out["error"], "WARN")
            # 页面上的错误文案是真正的原因
            errs = re.findall(r"(?:already|invalid|error|unable|failed|expired|blocked)[^\n]{0,120}",
                              page, re.I)
            if errs:
                out["error"] += " | " + "; ".join(dict.fromkeys(errs))[:200]
            return out

        # ---- 从落地页 / 控制台会话里取 token ----
        b.pump(6)
        tok = b.js("""return (() => {
            const pick = (k) => { try { return sessionStorage.getItem(k); } catch(e){ return null; } };
            const keys = ['accessToken','user','river:accessToken','__river_token'];
            const hit = {};
            for (const k of keys) { const v = pick(k); if (v) hit[k] = v; }
            try {
              for (let i=0;i<localStorage.length;i++){
                const k = localStorage.key(i);
                if (/token|user|auth/i.test(k)) hit['ls:'+k] = localStorage.getItem(k).slice(0,300);
              }
            } catch(e){}
            try {
              for (let i=0;i<sessionStorage.length;i++){
                const k = sessionStorage.key(i);
                hit['ss:'+k] = sessionStorage.getItem(k).slice(0,300);
              }
            } catch(e){}
            return JSON.stringify(hit);
        })()""")
        try:
            st = json.loads(tok or "{}")
        except Exception:
            st = {}
        if st:
            for k, v in st.items():
                if "accessToken" in k and isinstance(v, str):
                    out["access_token"] = v
                if k.endswith("accessToken") or k.endswith("refreshToken"):
                    if k.endswith("refreshToken"):
                        out["refresh_token"] = v
                if k.endswith(":user") or k == "ss:user":
                    try:
                        out["user_id"] = json.loads(v).get("id", "")
                    except Exception:
                        pass
            log(f"从会话存储取到 {len(st)} 个键")

        out["ok"] = True
        return out