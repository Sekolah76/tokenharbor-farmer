#!/usr/bin/env python3
"""river/register.py — 注册 + 拿 API Key 的主流程

流程（对齐 river/ 逆向结论）：
    1. GET  /api/auth/config拿 client_id、判断密码注册是否开
       ├─ password_signup=true  → 短路径 POST /api/auth/signup（当前关闭）
       └─ false                 → 长路径 WorkOS AuthKit 托管注册
    2. GET  auth/user_management/authorize        → authorization_session_id
    3. GET  login/sign-up（跟 bootstrap 307）      → __Host-state cookie + server action id
    4. POST login/sign-up（Next.js Server Action） → authorization_code
    5. POST auth/user_management/authenticate     → access_token / refresh_token
    6. GET  console/api/me                        → 确认登录态
    7. POST console/api/keys                      → 创建 API Key
    8. GET  console/api/keys/propagation/.../wait → 等待下发到推理网关

关于手机验证码：River 当前 authMethods 里没有 sms/phone 通道，注册页零个
type="tel" 输入框，所以第4 步不会触发短信。若目标站策略变化导致触发，
本模块按 SKIP_PHONE_VERIFICATION 处理（默认跳过并记录，见 handle_phone_challenge）。
"""
import json
import os
import random
import re
import string
import time

from .client import (AUTH, CONSOLE, LOGIN, AuthError, PhoneVerificationRequired,
                     authenticate, extract_code, fetch_config, flight_errors,
                     is_phone_challenge, load_signup_page, log, new_pkce, new_session,
                     new_state, start_authorization, signup_succeeded,
                     submit_signup_http)
from .keys import KeyManager, KeysError, get_me

# 手机验证处理策略：
#   skip    跳过该分支，记一条结果，继续拿 key（默认）
#   abort   抛 PhoneVerificationRequired，由调用方决定
#   solve   把 phone challenge 交出去（预留，接短信平台时实现）
SKIP_PHONE_VERIFICATION = os.environ.get("RIVER_PHONE_VERIFY", "skip").lower()


def handle_phone_challenge(text, session, auth_session_id, client_id, action_id,
                           email, password, policy=SKIP_PHONE_VERIFICATION):
    """第 4 步响应里出现手机验证要求时的处理。

    River 当前不会走到这里 —— authMethods 无 sms 通道、页面无 tel 输入框。
    保留是为了目标站策略变化时不静默卡死。

    返回 (handled: bool, reason: str)。handled=True 表示流程可以继续。
    """
    reason = "检测到手机/短信验证码要求"
    if policy == "abort":
        raise PhoneVerificationRequired(reason)
    if policy == "skip":
        log(f"{reason} —— 按策略跳过（River 未启用 AuthKit SMS 通道，属预期外）", "WARN")
        return False, f"{reason}（skipped）"

    # policy == "solve"：预留位。WorkOS 的 SMS 走 Prelude 网关，
    # 需要 client_id + authorization_session_id + 一个能收码的号码，此处不实现。
    log(f"{reason} —— solve 策略未实现，回退为跳过", "WARN")
    return False, f"{reason}（solve 未实现，等同 skip）"


def gen_password(min_len=12):
    """≥10 位 + 强度 3。密码规则来自 AuthKit passwordRules：
       无强制大小写/数字/符号，无pwned 检查。"""
    core = ''.join(random.choices(
        string.ascii_letters + string.digits + "!@#$%^&*", k=max(min_len, 12)))
    # 确保至少有大写、小写、数字、符号各一，满足 strength=3
    core = (random.choice(string.ascii_uppercase) + random.choice(string.ascii_lowercase)
            + random.choice(string.digits) + random.choice("!@#$%^&*")
            + core[4:] + ''.join(random.choices(string.ascii_letters + string.digits, k=4)))
    return core[:max(min_len, 12)]


def short_path_signup(session, email, password):
    """短路径：POST console/api/auth/signup

    当前 password_signup=false，服务端返回 404 "Password signup is disabled"。
    保留是因为路由还注册着、feature flag 一翻就能用。
    """
    r = session.post(f"{CONSOLE}/api/auth/signup",
                     json={"email": email, "password": password},
                     headers={"Content-Type": "application/json",
                              "Origin": CONSOLE, "Accept": "application/json"},
                     timeout=40)
    if r.status_code == 404:
        return None
    if r.status_code in (200, 201):
        return r.json()
    return None


def _new_result(email, password):
    return {
        "email": email, "password": password, "user_id": "",
        "access_token": "", "refresh_token": "", "organization_id": "",
        "api_key": "", "key_hash": "", "propagation": None,
        "phone_verification": False, "ok": False, "error": "", "mode": "",
    }


def register_via_browser_flow(email, password, team_id=None, first_name="",
                              last_name="", wait_propagation=True, timeout=90,
                              headless=True, proxy=None, human_pace=False):
    """浏览器驱动的注册 + 拿 key（默认路径）。

    实测：纯 HTTP 复现 AuthKit 的 Server Action 会稳定拿到
    "Server action not found."，即便字段名/头都对齐 —— 服务端对客户端环境
    有额外校验。所以走真实 Chrome。

    headless / proxy / human_pace 三个参数是对抗 WorkOS Radar 的杠杆，
    默认值是「最容易被拦」的组合，见 browser.py 的说明。
    """
    from .browser import register_via_browser

    log("用真实 Chrome 跑注册（AuthKit Server Action 不接受纯 HTTP 客户端）")
    r = register_via_browser(email, password, first_name=first_name,
                            last_name=last_name, timeout=timeout,
                            headless=headless, proxy=proxy,
                            human_pace=human_pace)
    r["mode"] = "browser"

    if not r.get("ok"):
        # Access blocked 是 WorkOS Radar 风控，不是流程 bug —— 明确标注便于重试策略
        if "blocked" in (r.get("error") or "").lower():
            r["error_kind"] = "workos_radar_blocked"
            log("被 WorkOS Radar 拦截（Access blocked）", "ERR")
        return r

    # 拿到 token 后创建 API Key
    token = r.get("access_token")
    if not token:
        r["ok"] = False
        r["error"] = "注册成功但没取到 access_token"
        return r
    return _finalize(new_session(), r, team_id, wait_propagation)


def register(email=None, password=None, team_id=None, name_prefix="river",
             wait_propagation=True, phone_policy=SKIP_PHONE_VERIFICATION,
             first_name="Test", last_name="User", mode="browser", timeout=90,
             headless=True, proxy=None, human_pace=False):
    """完整注册 + 拿key。返回结果 dict。

    mode="browser"（默认）走真实 Chrome —— AuthKit 的 Server Action 对纯 HTTP
    客户端做环境校验，稳定路径是浏览器。
    mode="http"    走纯 HTTP（submit_signup_http），适合协议研究，实践中常被
                   Radar 拦（Access blocked）。

    headless/proxy/human_pace 仅在 browser 模式生效，见 browser.py。
    """
    if not password:
        password = gen_password()

    if mode == "browser":
        return register_via_browser_flow(email, password, team_id,
                                         first_name, last_name,
                                         wait_propagation, timeout,
                                         headless, proxy, human_pace)

    result = _new_result(email, password)
    result["mode"] = "http"
    result["password"] = password

    s = new_session()

    # ---- 1. 认证配置 ----
    cfg = fetch_config(s)
    client_id = cfg.get("workos_client_id")
    if not client_id:
        log("config 里没有 workos_client_id，无法继续", "ERR")
        result["error"] = "no_client_id"
        return result
    log(f"client_id={client_id} password_signup={cfg.get('password_signup')}")

    # ---- 2. 短路径（若开放） ----
    if cfg.get("password_signup"):
        log("password_signup 开启，尝试短路径 POST /api/auth/signup")
        tok = short_path_signup(s, email, password)
        if tok and tok.get("access_token"):
            result["access_token"] = tok["access_token"]
            result["refresh_token"] = tok.get("refresh_token", "")
            result["organization_id"] = tok.get("organization_id", "")
            result["ok"] = True
            return _finalize(s, result, team_id, wait_propagation)

    # ---- 3. 长路径：WorkOS AuthKit 托管注册 ----
    verifier, challenge = new_pkce()
    state = new_state()

    auth_session_id, _ = start_authorization(s, client_id, screen_hint="sign-up",
                                            code_challenge=challenge, state=state)
    log(f"authorization_session_id={auth_session_id}")

    html, action_id = load_signup_page(s, client_id, auth_session_id)
    log(f"server action id={action_id}")

    r = submit_signup_http(s, client_id, auth_session_id, action_id,
                      email=email, password=password,
                      first_name=first_name, last_name=last_name)

    text = r.text

    # ---- 4. 手机验证分支 ----
    if is_phone_challenge(text):
        result["phone_verification"] = True
        handled, reason = handle_phone_challenge(
            text, s, auth_session_id, client_id, action_id,
            email, password, policy=phone_policy)
        result["phone_verification_reason"] = reason
        if not handled:
            # 跳过后再试一次不带任何手机相关字段的纯密码注册
            log("跳过手机验证后重试一次纯密码提交", "WARN")
            r = submit_signup_http(s, client_id, auth_session_id, action_id,
                              email=email, password=password,
                              first_name=first_name, last_name=last_name)
            text = r.text

    # ---- 5. 判断注册结果 ----
    if not signup_succeeded(text):
        errs = flight_errors(text)
        log(f"注册未成功 status={r.status_code} errors={errs[:3]}", "ERR")
        result["error"] = "; ".join(errs[:3]) or f"http_{r.status_code}"
        return result

    code = extract_code(text, state)
    if not code:
        log("注册成功但没抓到 authorization_code", "WARN")

    # ---- 6. 换 token ----
    if code:
        tok = authenticate(s, client_id, code=code, code_verifier=verifier)
    else:
        log("无 code，改用 cookie 直读 token", "WARN")
        return result

    user = tok.get("user", {})
    result["user_id"] = user.get("id", "")
    result["access_token"] = tok.get("access_token", "")
    result["refresh_token"] = tok.get("refresh_token", "")
    result["organization_id"] = tok.get("organization_id", "")
    result["ok"] = True
    log(f"token 成功 user={result['user_id']}")

    return _finalize(s, result, team_id, wait_propagation)


def _finalize(session, result, team_id, wait_propagation):
    """确认登录态 + 创建 API Key。"""
    me = get_me(session, result["access_token"], team_id)
    log(f"/api/me authenticated={me.get('authenticated')} email={me.get('email')}")

    try:
        km = KeyManager(session, result["access_token"], team_id)
        if wait_propagation:
            created = km.create_and_wait(
                name=f"river-{random.randint(1000, 9999)}",
                description="created by tokenharbor-farmer",
                expires_in_days=30, timeout_secs=60)
            if isinstance(created, tuple):
                created, prop = created
                result["propagation"] = prop
        else:
            created = km.create(name=f"river-{random.randint(1000, 9999)}",
                                description="created by tokenharbor-farmer",
                                expires_in_days=30)

        result["api_key"] = created.get("key", "")
        result["key_hash"] = created.get("key_hash", "")
        log(f"API Key 创建成功 key_hash={result['key_hash']}")
    except KeysError as e:
        log(f"创建 API Key 失败：{e}", "ERR")
        result["error"] = str(e)
        result["error_kind"] = "key_create_failed"
    return result


# ---------------------------------------------------------------- 批量


def register_loop(n=1, delay=5, team_id=None, out="river/results.jsonl",
                  max_retry=3, proxies_list=None, **kw):
    """批量注册，逐个跑，结果落 jsonl。

    结构参考 tokenharbor-farmer/th_auto_register.cmd_loop：
      - 每个账号用独立临时邮箱
      - 失败时区分错误类型：风控拦截 → 换出口立刻重试；其他 → 等一下
      - max_retry 次机会，用完才放弃

    proxies_list: 可选代理列表（如 ["socks5://127.0.0.1:9050"]），
        每轮换一个，配合 max_retry 做「换 IP 重试」。
    **kw 透传给 register()（headless / proxy / human_pace / mode 等）。
    """
    try:
        import th_auto_register as tar
        tar.SOCKS = {}
    except Exception:
        tar = None

    # 邮箱源池：默认强制 YYDS 多域名轮换。
    # 背景：cf-mail 的 hellocat.ccwu.cc 是 catch-all 域，Radar 实测整域拉黑
    # （新加坡干净出口也拦）。YYDS 的 4 个域是独立域，信誉各不相同，
    # 逐个试哪个能过。可用 RIVER_MAIL_DOMAINS 覆盖。
    mail_domains = ([d.strip() for d in
                     os.environ.get("RIVER_MAIL_DOMAINS",
                                     "edu.jstyo.com,204901.xyz,100811.xyz,20220108.xyz")
                     .split(",") if d.strip()])

    results = []
    for i in range(n):
        log(f"=== 第 {i+1}/{n} 个 ===")
        attempt, r = 0, None
        while attempt < max_retry:
            attempt += 1
            # 每轮换邮箱；给了代理列表就每轮换代理
            if tar is not None:
                try:
                    if mail_domains:
                        # 强制 YYDS + 指定域名轮换（绕开被拉黑的 catch-all 域）
                        tar.CF_MAIL_BASE, tar.CF_MAIL_TOKEN, tar.CF_MAIL_DOMAIN = "", "", ""
                        dom = mail_domains[(i * max_retry + attempt - 1) % len(mail_domains)]
                        local = "rv" + ''.join(random.choices(
                            string.ascii_lowercase + string.digits, k=9))
                        kw["email"] = tar._yyds_create(domain=dom, local=local)[0]
                    else:
                        kw["email"] = tar.gen_email()[0]
                    log(f"临时邮箱（{dom if mail_domains else 'cf/yyds'}）：{kw['email']}")
                except Exception as e:
                    log(f"临时邮箱不可用：{str(e)[:50]}", "WARN")
            if proxies_list:
                kw["proxy"] = proxies_list[(attempt - 1) % len(proxies_list)]
                log(f"本轮代理：{kw['proxy']}")

            try:
                r = register(team_id=team_id, **kw)
            except PhoneVerificationRequired as e:
                log(f"手机验证未处理：{e}", "ERR")
                r = {"ok": False, "error": f"phone_verification: {e}",
                     "error_kind": "phone_verification"}
            except Exception as e:
                log(f"异常：{type(e).__name__}: {str(e)[:110]}", "ERR")
                r = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:110]}",
                     "error_kind": "exception"}

            r["attempt"] = attempt
            if r.get("ok") and r.get("api_key"):
                break

            err_low = (r.get("error") or "").lower()
            kind = (r.get("error_kind") or "")
            # 风控拦截 / CF 挑战 —— 都是「出口问题」，立刻换IP 重试
            blocked = ("blocked" in err_low or "blocked" in kind
                       or "radar" in kind or "cf_challenge" in kind
                       or "challenge" in kind)
            if blocked:
                log(f"出口被拦（第 {attempt} 次）：{kind or err_low[:40]}，换 IP 重试", "WARN")
                time.sleep(3)
            else:
                log(f"失败（第 {attempt} 次）：{str(r.get('error'))[:70]}", "WARN")
                time.sleep(delay + attempt * 2)

        results.append(r)
        with open(out, "a") as f:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
        if i < n - 1:
            time.sleep(delay)
    return results
