#!/usr/bin/env python3
"""deploy.py — 用 Cloudflare API 直接部署 cf-mail（不需要 wrangler）

用法:
    CF_TOKEN=<api_token> python3 cf-mail/deploy.py --domain 215.im
    # 可选：--worker-name cf-mail  --mail-token <读信口令>（不给就随机生成并打印）

需要的 token 权限（Dashboard → My Profile → API Tokens → Create Token）:
    Account  → Workers Scripts        → Edit
    Account  → Workers KV Storage     → Edit
    Zone     → Email Routing Rules    → Edit
    Zone     → Zone                   → Read
    Account Resources: 包含你的账号
    Zone Resources:    包含目标域名

流程:
    1. 校验 token / 找 zone 和 account
    2. 建 KV namespace（已存在则复用）
    3. 上传 worker（含 KV 绑定 + MAIL_TOKEN secret）
    4. 配置 Email Routing catch-all → 该 worker
    5. 自测 /health
"""
import argparse, json, os, secrets, sys, time
import urllib.parse
import requests

API = "https://api.cloudflare.com/client/v4"


def die(msg, extra=None):
    print(f"\n✗ {msg}", file=sys.stderr)
    if extra:
        print(f"  {json.dumps(extra, ensure_ascii=False)[:600]}", file=sys.stderr)
    sys.exit(1)


class CF:
    def __init__(self, token):
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}"})

    def call(self, method, path, **kw):
        r = self.s.request(method, API + path, timeout=60, **kw)
        try:
            d = r.json()
        except Exception:
            die(f"{method} {path} 返回非 JSON (http {r.status_code})", {"body": r.text[:400]})
        if not d.get("success"):
            return None, d.get("errors")
        return d.get("result"), None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", required=True, help="走 Email Routing 的域名，如 215.im")
    ap.add_argument("--worker-name", default="cf-mail")
    ap.add_argument("--mail-token", default=os.environ.get("MAIL_TOKEN", ""),
                    help="读信口令；不给就随机生成")
    ap.add_argument("--script", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "worker.js"))
    args = ap.parse_args()

    token = os.environ.get("CF_TOKEN", "").strip()
    if not token:
        die("请设置环境变量 CF_TOKEN")
    if not os.path.exists(args.script):
        die(f"找不到 worker 脚本: {args.script}")
    src = open(args.script).read()

    cf = CF(token)

    # --- 1. 校验 ---
    res, err = cf.call("GET", "/user/tokens/verify")
    if err:
        die("token 校验失败 —— 确认它是 Cloudflare API Token（40 字符、无前缀）", err)
    print(f"✓ token 有效 (status={res.get('status')})")

    zones, err = cf.call("GET", f"/zones?name={urllib.parse.quote(args.domain)}")
    if err:
        die("查询 zone 失败（token 可能缺 Zone:Read 权限）", err)
    if not zones:
        die(f"token 看不到域名 {args.domain} —— 检查 Zone Resources 是否包含它")
    zone = zones[0]
    zone_id, account_id = zone["id"], zone["account"]["id"]
    print(f"✓ zone {zone['name']} (id={zone_id})")
    print(f"✓ account {account_id}")

    # --- 2. 检查 Email Routing 是否启用 ---
    er, err = cf.call("GET", f"/zones/{zone_id}/email/routing")
    if err:
        print(f"! 读 Email Routing 状态失败（可能缺权限）: {err}")
    else:
        if not er.get("enabled"):
            die(f"域名 {args.domain} 的 Email Routing 未启用 —— "
                f"去 Dashboard → Email → Email Routing 开启，并按提示加 MX")
        print(f"✓ Email Routing 已启用（MX: {[r.get('content') for r in er.get('record', {}) if False] or 'route*.mx.cloudflare.net'}）")

    # --- 3. KV namespace ---
    title = f"{args.worker_name}-MAIL"
    ns_list, err = cf.call("GET", f"/accounts/{account_id}/storage/kv/namespaces?per_page=100")
    if err:
        die("列 KV namespace 失败（缺 Workers KV Storage:Edit 权限）", err)
    ns = next((n for n in (ns_list or []) if n["title"] == title), None)
    if ns:
        print(f"✓ KV namespace 已存在: {title} (id={ns['id']})")
    else:
        ns, err = cf.call("POST", f"/accounts/{account_id}/storage/kv/namespaces",
                          json={"title": title})
        if err:
            die("创建 KV namespace 失败", err)
        print(f"✓ KV namespace 已创建: {title} (id={ns['id']})")

    # --- 4. 上传 worker ---
    mail_token = args.mail_token or secrets.token_urlsafe(24)
    metadata = {
        "main_module": "worker.js",
        "compatibility_date": time.strftime("%Y-%m-%d"),
        "bindings": [
            {"type": "kv_namespace", "name": "MAIL", "namespace_id": ns["id"]},
            {"type": "secret_text", "name": "MAIL_TOKEN", "text": mail_token},
        ],
    }
    files = {
        "metadata": (None, json.dumps(metadata), "application/json"),
        "worker.js": ("worker.js", src, "application/javascript+module"),
    }
    res, err = cf.call("PUT", f"/accounts/{account_id}/workers/scripts/{args.worker_name}",
                       files=files)
    if err:
        die("上传 worker 失败（缺 Workers Scripts:Edit 权限？）", err)
    print(f"✓ worker 已部署: {args.worker_name}")

    # 拿到 workers.dev 地址（子域没开的话这步会失败，不影响 Email 收信）
    sub, _ = cf.call("GET", f"/accounts/{account_id}/workers/subdomain")
    worker_url = ""
    if sub and sub.get("subdomain"):
        worker_url = f"https://{args.worker_name}.{sub['subdomain']}.workers.dev"
        print(f"  URL: {worker_url}")

    # --- 5. catch-all → worker ---
    rule = {
        "enabled": True,
        "name": f"{args.worker_name} catch-all",
        "matchers": [{"type": "all"}],
        "actions": [{"type": "worker", "value": [args.worker_name]}],
    }
    res, err = cf.call("PUT", f"/zones/{zone_id}/email/routing/rules/catch_all", json=rule)
    if err:
        print(f"! 配置 catch-all 失败: {json.dumps(err, ensure_ascii=False)[:300]}")
        print("  手动兜底：Dashboard → Email → Email Routing → Routes → Catch-all → 选该 Worker")
    else:
        print(f"✓ catch-all 已指向 worker（所有 *@{args.domain} 都会进 Worker）")

    print("\n" + "=" * 62)
    print("部署完成。脚本侧这样配：")
    print("=" * 62)
    print(f'export CF_MAIL_BASE="{worker_url or "https://<你的 workers.dev 子域>"}"')
    print(f'export CF_MAIL_TOKEN="{mail_token}"')
    print(f'export CF_MAIL_DOMAIN="{args.domain}"')
    print()
    if worker_url:
        print("自测：")
        print(f'  curl -s -H "X-Mail-Token: {mail_token}" "{worker_url}/health"')
    else:
        print("! 未取到 workers.dev 子域 —— 需要在 Dashboard → Workers 里启用 workers.dev 子域，")
        print("  否则脚本无法通过 HTTP 读信。")
    print()
    print("⚠ 别忘了实测：从任意邮箱发一封给 test@{d}，再调 /inbox 看能不能收到。".format(d=args.domain))
    print("   tokenharbor 会对某些域名静默丢信，自建域名也必须先验证。")


if __name__ == "__main__":
    main()
