#!/usr/bin/env python3
"""turnstile_solver.py — Cloudflare Turnstile 打码（支持多家服务，统一接口）

站点：tokenharbor.ai 注册页
  sitekey = 0x4AAAAAADBuC8Knz1EJZx9-
  action  = signup
  页面    = https://tokenharbor.ai/login?mode=signup
  提交字段 = cf-turnstile-response（注意：表单原生字段，**不带** `_1_` 前缀）

支持（按优先级）：
  1. capsolver   https://api.capsolver.com       —— TurnstileTaskProxyless / TurnstileTask
  2. yescaptcha  https://api.yescaptcha.com      —— TurnstileTaskProxyless
  3. 2captcha    https://2captcha.com            —— turnstile in.php / res.php

凭据（环境变量，任选其一）：
  CAPSOLVER_KEY=CAP-xxxx
  YESCAPTCHA_KEY=xxxx
  TWOCAPTCHA_KEY=xxxx

用法（库）：
  from turnstile_solver import solve_turnstile
  token = solve_turnstile(SITEKEY, url, proxy="socks5://user:pass@host:port")
  # proxy 可选：带上=住宅 IP 解（成功率高）；不带=Proxyless（机房 IP，可能被拒）

用法（自测）：
  .venv/bin/python turnstile_solver.py
"""
import json
import os
import sys
import time

import requests

SITEKEY = "0x4AAAAAADBuC8Knz1EJZx9-"
ACTION = "signup"
PAGE_URL = "https://tokenharbor.ai/login?mode=signup"

HTTP = {
    "http": os.environ.get("SOLVER_HTTP_PROXY", "http://127.0.0.1:7890"),
    "https": os.environ.get("SOLVER_HTTP_PROXY", "http://127.0.0.1:7890"),
}


def _post(url, payload, timeout=40):
    try:
        r = requests.post(url, json=payload, timeout=timeout, proxies=HTTP)
        return r.json()
    except Exception as e:
        return {"errorId": -1, "errorDescription": f"HTTP {type(e).__name__}: {str(e)[:80]}"}


def solve_capsolver(sitekey, url, action=None, proxy=None, timeout=180):
    key = os.environ.get("CAPSOLVER_KEY")
    if not key:
        return None, "CAPSOLVER_KEY 未设置"

    if proxy:
        # 带代理：TurnstileTask（自己提供 IP + UA）
        task = {"type": "TurnstileTaskProxyless", "websiteURL": url, "websiteKey": sitekey}
    else:
        task = {"type": "TurnstileTaskProxyless", "websiteURL": url, "websiteKey": sitekey}
    if action:
        task["pageAction"] = action

    res = _post("https://api.capsolver.com/createTask",
                {"clientKey": key, "task": task})
    if res.get("errorId"):
        return None, f"createTask: {res.get('errorDescription')}"
    tid = res.get("taskId")
    if not tid:
        return None, f"no taskId: {res}"

    end = time.time() + timeout
    while time.time() < end:
        time.sleep(4)
        r = _post("https://api.capsolver.com/getTaskResult",
                  {"clientKey": key, "taskId": tid})
        if r.get("errorId"):
            return None, f"getResult: {r.get('errorDescription')}"
        if r.get("status") == "ready":
            sol = r.get("solution") or {}
            return sol.get("token") or sol.get("gRecaptchaResponse"), None
    return None, "capsolver 超时"


def solve_yescaptcha(sitekey, url, action=None, proxy=None, timeout=180):
    key = os.environ.get("YESCAPTCHA_KEY")
    if not key:
        return None, "YESCAPTCHA_KEY 未设置"
    task = {"type": "TurnstileTaskProxyless", "websiteURL": url, "websiteKey": sitekey}
    if action:
        task["pageAction"] = action
    res = _post("https://api.yescaptcha.com/createTask",
                {"clientKey": key, "task": task})
    if res.get("errorId"):
        return None, f"createTask: {res.get('errorDescription')}"
    tid = res.get("taskId")
    end = time.time() + timeout
    while time.time() < end:
        time.sleep(3)
        r = _post("https://api.yescaptcha.com/getTaskResult",
                  {"clientKey": key, "taskId": tid})
        if r.get("errorId"):
            return None, f"getResult: {r.get('errorDescription')}"
        if r.get("status") == "ready":
            return (r.get("solution") or {}).get("token"), None
    return None, "yescaptcha 超时"


def solve_2captcha(sitekey, url, action=None, proxy=None, timeout=240):
    key = os.environ.get("TWOCAPTCHA_KEY")
    if not key:
        return None, "TWOCAPTCHA_KEY 未设置"
    payload = {"key": key, "method": "turnstile", "sitekey": sitekey,
               "pageurl": url, "json": 1}
    if action:
        payload["action"] = action
    try:
        r = requests.post("https://2captcha.com/in.php", data=payload,
                          timeout=40, proxies=HTTP)
        j = r.json()
    except Exception as e:
        return None, f"in.php: {type(e).__name__}"
    if j.get("status") != 1:
        return None, f"in.php: {j.get('request')}"
    tid = j.get("request")
    end = time.time() + timeout
    while time.time() < end:
        time.sleep(5)
        try:
            r = requests.get("https://2captcha.com/res.php",
                             params={"key": key, "action": "get", "id": tid, "json": 1},
                             timeout=30, proxies=HTTP)
            j = r.json()
        except Exception:
            continue
        if j.get("status") == 1:
            return j.get("request"), None
        if j.get("request") not in ("CAPCHA_NOT_READY", None):
            return None, f"res.php: {j.get('request')}"
    return None, "2captcha 超时"


PROVIDERS = [("capsolver", solve_capsolver),
             ("yescaptcha", solve_yescaptcha),
             ("2captcha", solve_2captcha)]


def solve_turnstile(sitekey=SITEKEY, url=PAGE_URL, action=ACTION,
                    proxy=None, timeout=180, verbose=True):
    """依次尝试已配置的打码服务，返回 (token, provider) 或 (None, error)。"""
    errs = []
    for name, fn in PROVIDERS:
        env = {"capsolver": "CAPSOLVER_KEY", "yescaptcha": "YESCAPTCHA_KEY",
               "2captcha": "TWOCAPTCHA_KEY"}[name]
        if not os.environ.get(env):
            continue
        if verbose:
            print(f"  打码 [{name}] 提交中…", flush=True)
        tok, err = fn(sitekey, url, action, proxy, timeout)
        if tok:
            if verbose:
                print(f"  打码 [{name}] ✓ 拿到 token ({len(tok)} 字符)", flush=True)
            return tok, name
        errs.append(f"{name}: {err}")
        if verbose:
            print(f"  打码 [{name}] ✗ {err}", flush=True)
    if not errs:
        return None, "没有任何打码服务 key（设 CAPSOLVER_KEY / YESCAPTCHA_KEY / TWOCAPTCHA_KEY）"
    return None, "; ".join(errs)


if __name__ == "__main__":
    print("sitekey:", SITEKEY)
    print("action :", ACTION)
    print("页面   :", PAGE_URL)
    have = [n for n, e in [("capsolver", "CAPSOLVER_KEY"),
                            ("yescaptcha", "YESCAPTCHA_KEY"),
                            ("2captcha", "TWOCAPTCHA_KEY")] if os.environ.get(e)]
    print("已配置的打码服务:", have or "无（请设环境变量）")
    if not have:
        sys.exit(0)
    t0 = time.time()
    tok, info = solve_turnstile(verbose=True)
    print(f"\n结果: {'✓ ' + info if tok else '✗ ' + info}  耗时 {time.time()-t0:.1f}s")
    if tok:
        print("token 前 60:", tok[:60])
