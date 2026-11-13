#!/usr/bin/env python3
"""turnstile_browser.py — 用反检测浏览器（patchright）真跑 Turnstile，抠 cf-turnstile-response。

为什么不用视觉模型：
  Turnstile 不是图片验证码，判定依据是浏览器指纹 + 行为轨迹 + 网络信誉，
  输出是一串 JWT token。没有图像可识别，识别类模型无从下手。
  真正能通的路是「用真浏览器把验证跑完，再把 token 抠出来」。

用法（库）：
  from turnstile_browser import solve
  tok = solve(proxy="http://user:pass@host:port")     # proxy 可选

用法（自测）：
  .venv/bin/python turnstile_browser.py [--proxy URL] [--headless]
"""
import argparse
import json
import os
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
const origQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (p) => (
  p.name === 'notifications'
    ? Promise.resolve({state: Notification.permission})
    : origQuery(p)
);
"""


def solve(proxy=None, headless=False, timeout=90, verbose=True):
    """返回 token 字符串；失败返回 None。

    proxy 支持 http:// 或 socks5://；socks5 需要 browser 支持
    （chromium 原生支持 socks5，认证型需 http 代理中转）。
    """
    from patchright.sync_api import sync_playwright

    launch = {
        "headless": headless,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-dev-shm-usage",
        ],
    }
    if proxy:
        launch["proxy"] = {"server": proxy}

    token = None
    with sync_playwright() as p:
        browser = p.chromium.launch(**launch)
        ctx = browser.new_context(
            user_agent=UA,
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            device_scale_factor=2,
        )
        ctx.add_init_script(STEALTH)
        page = ctx.new_page()

        # 抓 token：Turnstile 成功后会把 token 塞进隐藏 input
        def grab():
            nonlocal token
            try:
                v = page.evaluate(
                    "() => { const e = document.querySelector('input[name=\"cf-turnstile-response\"]');"
                    " return e ? e.value : null; }")
                if v and len(v) > 20:
                    token = v
                    return True
            except Exception:
                pass
            return False

        if verbose:
            print(f"  打开 {URL}", flush=True)
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)

        # 等 Turnstile 组件渲染（页面会注入 challenges.cloudflare.com 的脚本）
        deadline = time.time() + timeout
        last_log = 0
        while time.time() < deadline:
            if grab():
                break
            # 模拟一点人类动作，提升评分
            try:
                page.mouse.move(700, 400)
                page.wait_for_timeout(300)
                page.mouse.move(760, 460)
            except Exception:
                pass
            edge = time.time() + 3
            while time.time() < edge and not grab():
                page.wait_for_timeout(250)
            if verbose and time.time() - last_log > 12:
                last_log = time.time()
                left = int(deadline - time.time())
                print(f"  …等待 Turnstile（剩 {left}s）", flush=True)

        if verbose:
            print(f"  {'✓ 拿到 token' if token else '✗ 没拿到 token'}", flush=True)
        try:
            ctx.close(); browser.close()
        except Exception:
            pass
    return token


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--proxy", default=None)
    ap.add_argument("--headless", action="store_true")
    A = ap.parse_args()
    print("sitekey:", SITEKEY, "| action:", ACTION)
    t0 = time.time()
    tok = solve(proxy=A.proxy, headless=A.headless)
    print(f"耗时 {time.time()-t0:.1f}s")
    if tok:
        print("token 长度:", len(tok))
        print("token 前 80:", tok[:80])
    else:
        print("失败")
        sys.exit(1)
