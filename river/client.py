#!/usr/bin/env python3
"""river/client.py — river.ai 认证层（WorkOS AuthKit 托管注册 + PKCE）

从 console.river.ai 的 bundle 与 login.console.river.ai 的 RSC payload 反推实现：
  - 自建密码注册 /api/auth/signup 已关闭（password_signup=false）→ 走 WorkOS 托管页
  - 授权发起   GET  auth.<domain>/user_management/authorize
  - 注册表单   POST login.<domain>/sign-up  （Next.js Server Action，action id 随 build 轮换）
  - 换token    POST auth.<domain>/user_management/authenticate (authorization_code + PKCE)

手机验证码：River 未启用 AuthKit 的 SMS 通道（authMethods 里无 sms/phone key，
页面零个 type="tel" 输入框）。若某天启用了，见 PhoneVerificationRequired。
"""
import base64
import hashlib
import json
import os
import re
import secrets
import string
import time
import urllib.parse

import requests

DIR = os.path.dirname(os.path.abspath(__file__))

CONSOLE = "https://console.river.ai"
AUTH = "https://auth.console.river.ai"
LOGIN = "https://login.console.river.ai"
API = "https://api.river.ai"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

REDIRECT_URI = f"{CONSOLE}/auth/callback"


class PhoneVerificationRequired(Exception):
    """注册流程要求手机号/短信验证码。

    River 当前不会走到这里（AuthKit SMS 通道未启用）；保留分支是为了
    目标站策略变化时不至于静默失败 —— 调用方应该显式跳过而不是卡住。
    """


class AuthError(Exception):
    pass


def log(msg, lv="INFO"):
    print(f"  [{time.strftime('%H:%M:%S')}] [{lv}] {msg}", flush=True)


# ---------------------------------------------------------------- PKCE


def new_pkce():
    """RFC 7636 —— AuthKit authorize 收 code_challenge，回调拿 code_verifier 换 token。"""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def new_state():
    return base64.urlsafe_b64encode(secrets.token_bytes(24)).rstrip(b"=").decode()


# ---------------------------------------------------------------- 配置探测


def fetch_config(s):
    """GET /api/auth/config —— 无鉴权，直接吐出 WorkOS client_id 与各登录开关。

    用途：拿 client_id、判断 password_signup 是否开（开了就能走短路径，
    省掉整个 AuthKit 表单往返）。
    """
    r = s.get(f"{CONSOLE}/api/auth/config", timeout=30)
    r.raise_for_status()
    return r.json()


def signup_precheck(s):
    """注册前置探测：问服务端这个出口要不要过人机校验。

    参考 tokenharbor-farmer/th_auto_register.signup_precheck 的做法 ——
    那个站点有 /api/auth/signup-precheck?fp=… 返回 needCaptcha。
    River 没有同名端点，但 /api/auth/config 会泄露风控开关，
    所以这里只做两件事：确认注册通道开着 + 记录当前出口IP 供换IP 决策用。

    返回 dict: {ok, reason, ip}
    """
    ip = None
    try:
        r = s.get("https://api.ipify.org?format=json", timeout=12)
        ip = r.json().get("ip")
    except Exception:
        pass

    try:
        cfg = fetch_config(s)
    except Exception as e:
        return {"ok": False, "reason": f"config 拉取失败: {e}", "ip": ip}

    if not cfg.get("workos_client_id"):
        return {"ok": False, "reason": "config 里没有 workos_client_id", "ip": ip}

    return {
        "ok": True,
        "reason": "",
        "ip": ip,
        "password_signup": bool(cfg.get("password_signup")),
    }


# ---------------------------------------------------------------- 授权发起


def start_authorization(s, client_id, screen_hint="sign-up", login_hint=None,
                        invitation_token=None, code_challenge=None,
                        state=None):
    """GET /user_management/authorize → 302 到 login.<domain>/bootstrap

    client_id 必须显式传入 —— 缺了它 WorkOS 会静默 302 到 error.workos.com/sso，
    而不是给出可读的报错，容易误判成「接口挂了」。

    返回 (authorization_session_id, login_url)。login_url 需要带 Cookie 跟到注册页。
    """
    params = {
        "client_id": client_id,
        "provider": "authkit",
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "screen_hint": screen_hint,
    }
    if state:
        params["state"] = state
    if login_hint:
        params["login_hint"] = login_hint
    if invitation_token:
        params["invitation_token"] = invitation_token
    if code_challenge:
        params["code_challenge"] = code_challenge
        params["code_challenge_method"] = "S256"

    r = s.get(f"{AUTH}/user_management/authorize", params=params, allow_redirects=False)
    if r.status_code not in (301, 302, 307, 308):
        raise AuthError(f"authorize 期望 3xx，实得 {r.status_code}: {r.text[:200]}")

    loc = r.headers["location"]
    m = re.search(r"authorization_session_id=([\w]+)", loc)
    if not m:
        raise AuthError(f"authorize 未返回 authorization_session_id（location={loc[:160]}）—— "
                        f"检查 client_id / redirect_uri 是否已在 WorkOS 后台登记")
    return m.group(1), loc


def bootstrap(s, client_id, auth_session_id):
    """先过 /bootstrap 拿 __Host-state-<sid> cookie，再返回真实落地页URL。

    这一跳不能省：Server Action 表单提交时，服务端用这个 cookie 做
    state/CSRF 绑定。缺了它，POST 会返回 text/plain 的
    "Server action not found." —— 故意伪装成 404 而不是 403，防探测。
    """
    url = (f"{LOGIN}/bootstrap?client_id={urllib.parse.quote(client_id)}"
           f"&redirect_uri={urllib.parse.quote(REDIRECT_URI, safe='')}"
           f"&authorization_session_id={auth_session_id}")
    r = s.get(url, allow_redirects=False, timeout=40)
    loc = r.headers.get("location")
    if not loc:
        raise AuthError(f"bootstrap 没有返回 redirect location（status={r.status_code}）")
    return loc


def load_signup_page(s, client_id, auth_session_id):
    """走 bootstrap → sign-up 两跳，拿 __Host-state cookie + server action id。

    action id 是 React Flight 里形如 20:{"id":"<40hex>","bound":null} 的那行，
    随站点重新部署轮换 —— 每次现取，别写死。
    """
    # 关键：先 bootstrap 拿 state cookie，否则后续 Server Action 提交会被判 "not found"
    land_url = bootstrap(s, client_id, auth_session_id)
    r = s.get(land_url, timeout=40)
    r.raise_for_status()
    html = r.text

    if "/sign-up" not in urllib.parse.urlparse(r.url).path:
        # bootstrap 可能把我们送去 /（session 复用），显式去注册页
        url = (f"{LOGIN}/sign-up?client_id={urllib.parse.quote(client_id)}"
               f"&redirect_uri={urllib.parse.quote(REDIRECT_URI, safe='')}"
               f"&authorization_session_id={auth_session_id}")
        r = s.get(url, timeout=40)
        r.raise_for_status()
        html = r.text

    m = _find_action_id(html)
    if not m:
        raise AuthError(f"没从页面里提取到 server action id（站点结构变了？）len={len(html)}")
    return html, m


def signup_form_url(client_id, auth_session_id):
    """注册表单的目标 URL —— 必须是带 query 的 /sign-up，而不是当前页地址。"""
    return (f"{LOGIN}/sign-up?client_id={urllib.parse.quote(client_id)}"
            f"&redirect_uri={urllib.parse.quote(REDIRECT_URI, safe='')}"
            f"&authorization_session_id={auth_session_id}")


def extract_rsc(html):
    """把 Next.js 的 __next_f.push payload 还原成一份连续的 RSC 流文本。

    页面上这些 payload 是 JSON-in-JS 字符串，内部引号被转义成 \\"；
    直接对原始 HTML 做正则会被转义层干扰（同一个 action id 在页面上有
    转义与未转义两种形态），所以先 json.loads 还原再匹配，稳得多。
    """
    out = []
    for p in re.findall(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)', html):
        try:
            out.append(json.loads(f'"{p}"'))
        except Exception:
            continue
    return "".join(out)


def _find_action_id(html):
    """从注册页抽出 server action id。

    两层保险：
      1) 优先在还原后的 RSC 流里匹配形如  20:{"id":"<40hex>","bound":null}
      2) RSC 提取失败时退回裸HTML 的宽松匹配

    必须带 "bound" 锚点 —— 页面上还有构建 hash（同样 40 位十六进制）混在里面，
    只找 40 位 hex 会抓到错的。
    """
    rsc = extract_rsc(html)
    if rsc:
        m = re.search(r'[\w]+:\{"id":"([0-9a-f]{40})","bound":', rsc)
        if m:
            return m.group(1)
    for pat in (r'\{"id":"([0-9a-f]{40})","bound"', r'\{\\"id\\":\\"([0-9a-f]{40})'):
        m = re.search(pat, html)
        if m:
            return m.group(1)
    return None


# ---------------------------------------------------------------- 表单提交


def _boundary():
    return "----WebKitFormBoundary" + ''.join(
        random_choices(string.ascii_uppercase + string.digits, 16))


def random_choices(population, k):
    """避免为一个纯随机 helper 引整个 random —— 直接用 secrets。"""
    return [secrets.choice(population) for _ in range(k)]


def _multipart(fields, boundary):
    parts = []
    for name, value in fields:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}')
    return "\r\n".join(parts) + f"\r\n--{boundary}--\r\n"


# Next.js App Router 的路由状态树，AuthKit 每页固定。
# 浏览器实测（capture_signup.py 抓到的 next-router-state-tree 头）就是这个值。
ROUTER_SIGNUP = urllib.parse.quote(
    '["",{"children":["(main)",{"children":["(root)",{"children":["sign-up",'
    '{"children":["__PAGE__",{},null,null,4096]},null,null,4096]},null,null,4096]},'
    'null,null,4096]},null,null,4112]')

DEPLOYMENT_ID = ("hosted-authkit-ab5497b936c84574e3dd5006f81a7e095a33bf77"
                 "-38296192439")


def _action_headers(action_id, state_tree=ROUTER_SIGNUP, deployment_id=DEPLOYMENT_ID):
    """Server Action 请求头。

    Next-Action               当前页面的 action id（每页不同，随build 轮换）
    next-router-state-tree    路由状态树，缺了会被判成非法导航
    x-deployment-id           Vercel 部署 id，同 build 内固定
    Accept: text/x-component  RSC action 的响应类型
    """
    return {
        "Accept": "text/x-component",
        "Next-Action": action_id,
        "Next-Router-State-Tree": state_tree,
        "x-deployment-id": deployment_id,
        "Origin": LOGIN,
        "Referer": f"{LOGIN}/sign-up",
    }


def _signals_payload():
    """构造 signals 字段 —— 一段 base64 的客户端遥测 JSON。

    实测（capture_signup.py 抓到的 postData）解码后形如：
      {"createdAtMs":..., "timezone":"Asia/Shanghai", "language":"zh-CN",
       "hardwareConcurrency":8, "webdriver":false, "userAgent":"Mozilla/5.0 ..."}
    服务端只做风控参考，不校验完整性，所以按真实环境填即可。
    """
    tz = ("Asia/Shanghai" if time.timezone < 0 else "UTC")
    payload = {
        "createdAtMs": int(time.time() * 1000),
        "timezone": tz,
        "language": "en-US",
        "hardwareConcurrency": 8,
        "webdriver": False,
        "userAgent": UA,
    }
    raw = json.dumps(payload, separators=(",", ":"))
    return base64.b64encode(raw.encode()).decode()


def submit_signup_http(s, client_id, auth_session_id, action_id, *, email, password,
                       first_name="", last_name=""):
    """提交注册（纯 HTTP 版）—— AuthKit 是两步表单，这里合并两步。

    实测协议（river/capture_signup.py 用真实 Chrome 抓 postData 得到）：

      第 1 步  POST /sign-up            action 408cf059...
               multipart，字段名带 _1_ 前缀：
               _1_signals / _1_first_name / _1_last_name / _1_email
               / _1_intent / _1_redirect_uri / _1_authorization_session_id / 0
      第 2 步  POST /sign-up/password   action 408b9aeb...（另有 409a26bd... 变体）
               multipart，字段 _1_browser_supports_passkeys / _1_signals / ...

    三个必须踩准的点，踩错就返回 text/plain 的 "Server action not found."：
      a) 必须先过 bootstrap 拿 __Host-state-<sid> cookie
      b) 表单字段名带 _1_ 前缀（useActionState 的命名空间），不是裸email
      c) 必须带 next-router-state-tree 与 x-deployment-id 头
    """
    signals = _signals_payload()

    # ---- 第 1 步：姓名 + 邮箱 ----
    url1 = signup_form_url(client_id, auth_session_id)
    bd1 = _boundary()
    fields1 = [
        ("_1_signals", signals),
        ("_1_first_name", first_name),
        ("_1_last_name", last_name),
        ("_1_email", email),
        ("_1_intent", "sign-up"),
        ("_1_redirect_uri", REDIRECT_URI),
        ("_1_authorization_session_id", auth_session_id),
        ("0", '["$undefined","$K1"]'),
    ]
    r1 = s.post(url1, data=_multipart(fields1, bd1),
                headers={**_action_headers(action_id),
                         "Content-Type": f"multipart/form-data; boundary={bd1}",
                         "Referer": url1},
                timeout=60)

    # 第 1 步失败就别往下走了（如邮箱已存在会在这里返回）
    if r1.status_code >= 400:
        return r1

    # ---- 第 2 步：密码 ----
    url2 = (f"{LOGIN}/sign-up/password?redirect_uri={urllib.parse.quote(REDIRECT_URI, safe='')}"
            f"&authorization_session_id={auth_session_id}")
    _, action2 = load_signup_password_page(s, client_id, auth_session_id)
    if not action2:
        return r1                      # 密码页取不到 action，退回第 1 步结果让调用方判断

    bd2 = _boundary()
    fields2 = [
        ("_1_browser_supports_passkeys", "true"),
        ("_1_signals", signals),
        ("_1_password", password),
        ("_1_email", email),
        ("_1_redirect_uri", REDIRECT_URI),
        ("_1_authorization_session_id", auth_session_id),
        ("0", '["$undefined","$K1"]'),
    ]
    r2 = s.post(url2, data=_multipart(fields2, bd2),
                headers={**_action_headers(action2),
                         "Content-Type": f"multipart/form-data; boundary={bd2}",
                         "Referer": url2},
                timeout=60)
    return r2


def load_signup_password_page(s, client_id, auth_session_id):
    """取密码页，抽出它自己的 action id（与 /sign-up 的不同）。"""
    url = (f"{LOGIN}/sign-up/password?redirect_uri={urllib.parse.quote(REDIRECT_URI, safe='')}"
           f"&authorization_session_id={auth_session_id}")
    r = s.get(url, timeout=40)
    if r.status_code != 200:
        return "", None
    return r.text, _find_action_id(r.text)


# ---------------------------------------------------------------- 响应解析


def is_phone_challenge(text):
    """判断响应是否要求手机/短信验证。

    AuthKit 把这类文案放在 flight payload 里（服务端错误重渲染）。
    River 当前不会命中；一旦命中，调用方按需跳过或转人工。
    """
    low = text.lower()
    needles = (
        "verify your phone number", "enter your phone number",
        "enter the code sent to your phone", "enter the code sent to {phonenumber}",
        "error sending sms challenge", "only mobile phone numbers are supported",
        "too many challenges sent for this phone number",
    )
    return any(n in low for n in needles)


def flight_errors(text):
    """从 flight 响应里抽真实错误文案。

    坑一（与 th_auto_register.flight_errors 同源）：页面里会出现 "error":"$11"
    这种 React 行引用，不是消息本身 —— 必须解引用确认那一行真是 {"error": ...}，
    否则丢弃。真文案可能在任意行，所以遍历全部命中。

    坑二：flight 响应同样是 JSON-in-JS 字符串，引号被转义。先还原 RSC 流再解析，
    否则 '^([0-9a-f]+):(.+)$' 后面那行json.loads 会因为转义而失败。
    """
    rows = {}
    source = extract_rsc(text) or text
    for m in re.finditer(r'^([0-9a-f]+):(.+)$', source, re.M):
        try:
            rows[m.group(1)] = json.loads(m.group(2))
        except Exception:
            pass
    out = []
    for rid, row in rows.items():
        if not isinstance(row, dict):
            continue
        err = row.get("error")
        if not isinstance(err, str):
            continue
        m = re.fullmatch(r"\$(\d+)", err)
        if m:                                   # 行引用 → 解引用
            ref = rows.get(m.group(1))
            if isinstance(ref, dict) and isinstance(ref.get("error"), str):
                out.append(ref["error"])
            continue
        out.append(err)
    return [e for e in dict.fromkeys(out) if e]


def signup_succeeded(text):
    """注册成功的判据：拿到 authorization_code 或被重定向到回调。"""
    if re.search(r'authorization_code|"code"\s*:\s*"[A-Za-z0-9_\-]{8,}"', text):
        return True
    return "Sign-up successful" in text or "signedIn" in text


def extract_code(text, state=None):
    """从 flight/重定向里抽 authorization_code。"""
    for pat in (r'code=([A-Za-z0-9_\-]{8,})', r'"code"\s*:\s*"([A-Za-z0-9_\-]{8,})"'):
        m = re.search(pat, text)
        if m:
            return m.group(1)
    m = re.search(r'code=([A-Za-z0-9_\-]{8,})', (state or ""))
    return m.group(1) if m else None


# ---------------------------------------------------------------- 换 token


def authenticate(s, client_id, *, code=None, code_verifier=None, refresh_token=None,
                 organization_id=None, use_cookie=False):
    """POST /user_management/authenticate

    授权码换 token： grant_type=authorization_code + code + code_verifier
    刷新：          grant_type=refresh_token   + refresh_token (+ organization_id)
    响应：{ user, organization_id, access_token, refresh_token, authentication_method }
    """
    body = {"client_id": client_id}
    if code:
        body.update({"grant_type": "authorization_code", "code": code,
                     "code_verifier": code_verifier})
    else:
        body["grant_type"] = "refresh_token"
        if refresh_token:
            body["refresh_token"] = refresh_token
        if organization_id:
            body["organization_id"] = organization_id

    r = s.post(f"{AUTH}/user_management/authenticate", json=body,
               headers={"Accept": "application/json"}, timeout=40)
    if not r.ok:
        try:
            err = r.json()
        except Exception:
            err = {"error_description": r.text[:200]}
        raise AuthError(f"authenticate {r.status_code}: "
                        f"{err.get('error')} / {err.get('error_description')}")
    return r.json()


def new_session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA,
                      "Accept-Language": "en-US,en;q=0.9"})
    return s