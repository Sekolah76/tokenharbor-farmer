#!/usr/bin/env python3
"""sub2api_push.py — 把 TokenHarbor 账号池推进 sub2api：导入 + 建组 + 绑定 tokenhour 分组

做四件事（可分别跳过）：
  1. 登录拿 admin token（或直接 --token 粘贴浏览器里的 access_token）
  2. 确保分组 tokenhour 存在（不存在则创建，platform=openai）
  3. 导入 sub2api-tokenhour.json（POST /api/v1/admin/accounts/data）
  4. 按账号名前缀搜出这批账号 → POST /api/v1/admin/accounts/bulk-update 绑定到 tokenhour 分组

典型用法:
  # 一条龙
  python3 sub2api_push.py --base-url https://sub2.example.com --email you@example.com --password 'xxx'

  # 只用浏览器 token（后台 F12 → localStorage 拿 access_token）
  python3 sub2api_push.py --base-url https://sub2.example.com --token eyJhbGci...

  # 已经手工导入过了，只做分组绑定
  python3 sub2api_push.py --base-url ... --token ... --skip-import

  # 干跑：只打印将要发生的请求，不发写操作
  python3 sub2api_push.py --base-url ... --token ... --dry-run
"""
import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict

DEFAULT_FILE = "sub2api-tokenhour.json"


class Api:
    def __init__(self, base_url, token=None, insecure=False, dry_run=False, verbose=True):
        self.base = base_url.rstrip("/")
        self.token = token
        self.insecure = insecure
        self.dry_run = dry_run
        self.verbose = verbose

    def call(self, method, path, body=None, write=False):
        url = self.base + path
        if write and self.dry_run:
            print(f"   [dry-run] {method} {path} body={json.dumps(body, ensure_ascii=False)[:400] if body else '-'}")
            return {"code": 0, "message": "dry-run", "data": {}}
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        ctx = ssl._create_unverified_context() if self.insecure else None
        if self.verbose:
            print(f"   → {method} {path}" + (f"  ({len(data)} bytes)" if data else ""))
        try:
            with urllib.request.urlopen(req, timeout=180, context=ctx) as resp:
                raw = resp.read().decode("utf-8", "replace")
                status = resp.status
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            status = exc.code
        except urllib.error.URLError as exc:
            raise SystemExit(f"❌ 连不上 {url}: {exc.reason}")
        try:
            payload = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            raise SystemExit(f"❌ {method} {path} 返回非 JSON（HTTP {status}）: {raw[:300]}")
        if status >= 400 or payload.get("code") not in (0, None):
            raise SystemExit(f"❌ {method} {path} 失败（HTTP {status} code={payload.get('code')}）: "
                             f"{payload.get('message') or raw[:300]}")
        return payload

    def get(self, path):
        return self.call("GET", path)

    def post(self, path, body):
        return self.call("POST", path, body, write=True)


def login(api_no_token, email, password):
    res = api_no_token.call("POST", "/api/v1/auth/login", {"email": email, "password": password}, write=True)
    token = (res.get("data") or {}).get("access_token")
    if not token:
        raise SystemExit("❌ 登录响应没有 access_token，请改用 --token 直接粘贴浏览器 token"
                         "（F12 → Application → localStorage → access_token）")
    return token


def find_group(api, name):
    page = 1
    while True:
        q = urllib.parse.urlencode({"search": name, "page": page, "page_size": 100})
        res = api.get(f"/api/v1/admin/groups?{q}")
        data = res.get("data") or {}
        items = data.get("items") or []
        for g in items:
            if str(g.get("name", "")).strip() == name:
                return g
        total = data.get("total") or 0
        if not items or page * 100 >= total:
            return None
        page += 1


def ensure_group(api, name, platform, desc):
    grp = find_group(api, name)
    if grp:
        print(f"✅ 分组已存在: {grp.get('name')} (id={grp.get('id')}, platform={grp.get('platform')})")
        return grp
    print(f"➕ 分组不存在，创建: {name} (platform={platform})")
    res = api.post("/api/v1/admin/groups", {
        "name": name,
        "description": desc,
        "platform": platform,
        "subscription_type": "standard",
    })
    data = res.get("data") or {}
    gid = data.get("id")
    if not gid:
        grp = find_group(api, name)
        if not grp:
            raise SystemExit("❌ 分组创建后仍查不到，请在后台确认")
        return grp
    return {"id": gid, "name": name, "platform": platform}


def import_payload(api, payload):
    res = api.post("/api/v1/admin/accounts/data", {"data": payload, "skip_default_group_bind": True})
    data = res.get("data") or {}
    print(f"📥 导入结果: 新建账号 {data.get('account_created', 0)}，失败 {data.get('account_failed', 0)}，"
          f"代理新建 {data.get('proxy_created', 0)}/复用 {data.get('proxy_reused', 0)}")
    for err in (data.get("errors") or [])[:10]:
        print(f"   ⚠️ {err.get('kind')} {err.get('name') or err.get('proxy_key') or '-'}: {err.get('message')}")
    if data.get("account_failed"):
        print("   （失败通常是同 api_key 已存在 / 渠道冲突，按上面明细处理）")
    return data


def collect_accounts(api, prefix, search_first=True):
    """按 name 前缀收集账号 id；先带 search 查，命中 0 条自动全量翻页兜底"""
    def scan(search):
        found, page = [], 1
        while True:
            q = {"page": page, "page_size": 100}
            if search:
                q["search"] = search
            res = api.get("/api/v1/admin/accounts?" + urllib.parse.urlencode(q))
            data = res.get("data") or {}
            items = data.get("items") or []
            for a in items:
                if str(a.get("name", "")).startswith(prefix):
                    found.append(a)
            total = data.get("total") or 0
            if not items or page * 100 >= total:
                return found
            page += 1

    found = scan(prefix) if search_first else []
    if not found:
        print("   （search 未命中，改为全量翻页扫描）")
        found = scan(None)
    return found


def normalize_group_ids(account, target_gid, keep_existing):
    if not keep_existing:
        return [target_gid]
    ids = set()
    for g in (account.get("groups") or []):
        if isinstance(g, dict) and g.get("id"):
            ids.add(int(g["id"]))
    for ag in (account.get("account_groups") or []):
        if isinstance(ag, dict) and ag.get("group_id"):
            ids.add(int(ag["group_id"]))
    ids.add(target_gid)
    return sorted(ids)


def bind_group(api, prefix, group, keep_existing):
    print(f"🔗 绑定分组: 账号名前缀 '{prefix}' → 分组 {group.get('name')} (id={group.get('id')})")
    accounts = collect_accounts(api, prefix)
    if not accounts:
        raise SystemExit(f"❌ 没找到 name 以 '{prefix}' 开头的账号，确认导入是否成功 / 前缀是否改过")
    print(f"   命中账号 {len(accounts)} 个")
    verb = "待更新" if api.dry_run else "已更新"
    ids = [int(a["id"]) for a in accounts if a.get("id")]

    if keep_existing:
        # 保留账号原有分组：按并集分组后再批量更新，避免不同账号被写成同一组
        buckets = defaultdict(list)
        for a in accounts:
            if not a.get("id"):
                continue
            buckets[tuple(normalize_group_ids(a, int(group["id"]), True))].append(int(a["id"]))
        for gids, bucket in buckets.items():
            res = api.post("/api/v1/admin/accounts/bulk-update",
                           {"account_ids": bucket, "group_ids": list(gids), "confirm_mixed_channel_risk": True})
            print(f"   {verb} {len(bucket)} 个账号 → group_ids={list(gids)}: {res.get('data')}")
    else:
        res = api.post("/api/v1/admin/accounts/bulk-update",
                       {"account_ids": ids, "group_ids": [int(group["id"])], "confirm_mixed_channel_risk": True})
        print(f"   {verb} {len(ids)} 个账号 → group_ids=[{group.get('id')}]: {res.get('data')}")
    return accounts


def verify(api, group):
    gid = int(group.get("id"))
    page, bound = 1, 0
    while True:
        q = urllib.parse.urlencode({"group": gid, "page": page, "page_size": 100})
        res = api.get(f"/api/v1/admin/accounts?{q}")
        data = res.get("data") or {}
        items = data.get("items") or []
        bound += len(items)
        total = data.get("total") or 0
        if not items or page * 100 >= total:
            break
        page += 1
    print(f"🔍 复核: 分组 {group.get('name')} 下现有账号 {bound} 个")
    return bound


def main():
    p = argparse.ArgumentParser(description="sub2api 导入 TokenHarbor 账号池并绑定 tokenhour 分组")
    p.add_argument("--base-url", required=True, help="sub2api 地址，例如 https://sub2.example.com")
    p.add_argument("--token", help="admin access_token（与 --email/--password 二选一）")
    p.add_argument("--email", help="管理员邮箱")
    p.add_argument("--password", help="管理员密码")
    p.add_argument("--file", default=DEFAULT_FILE, help=f"导入文件（默认 {DEFAULT_FILE}）")
    p.add_argument("--group", default="tokenhour", help="池模式分组名（默认 tokenhour）")
    p.add_argument("--group-platform", default="openai", help="新建分组时的平台（默认 openai）")
    p.add_argument("--name-prefix", default=None, help="账号名前缀，用于筛选本批账号（默认 <group>-）")
    p.add_argument("--skip-import", action="store_true", help="跳过导入，只做分组绑定")
    p.add_argument("--skip-bind", action="store_true", help="跳过分组绑定，只做导入")
    p.add_argument("--keep-existing-groups", action="store_true", help="绑定分组时保留账号原有分组（默认覆盖为该分组）")
    p.add_argument("--insecure", action="store_true", help="忽略 HTTPS 证书校验（自签证书自用实例）")
    p.add_argument("--dry-run", action="store_true", help="只打印将要执行的请求，不发出写操作")
    args = p.parse_args()
    if args.name_prefix is None:
        args.name_prefix = f"{args.group}-"

    here = os.path.dirname(os.path.abspath(__file__))
    file_path = args.file if os.path.isabs(args.file) else os.path.join(here, args.file)

    api = Api(args.base_url, token=args.token, insecure=args.insecure, dry_run=args.dry_run)

    print(f"🚀 目标: {args.base_url}  分组: {args.group}  文件: {file_path}")

    if not args.token:
        if not (args.email and args.password):
            raise SystemExit("❌ 需要 --token，或者 --email + --password")
        if args.dry_run:
            print("   [dry-run] POST /api/v1/auth/login")
        else:
            args.token = login(api, args.email, args.password)
            api.token = args.token
            print("🔑 登录成功，已拿到 access_token")

    # 1) 确保分组存在
    group = ensure_group(api, args.group, args.group_platform,
                         f"TokenHarbor 账号池（{os.path.basename(file_path)}）")

    # 2) 导入账号
    if not args.skip_import:
        if not os.path.exists(file_path):
            raise SystemExit(f"❌ 导入文件不存在: {file_path}（先跑 th_to_sub2api.py）")
        payload = json.load(open(file_path, "r", encoding="utf-8"))
        print(f"📦 载入 {len(payload.get('accounts') or [])} 个账号，准备导入")
        import_payload(api, payload)
    else:
        print("⏭  跳过导入（--skip-import）")

    # 3) 绑定分组
    if not args.skip_bind:
        bind_group(api, args.name_prefix, group, args.keep_existing_groups)
    else:
        print("⏭  跳过分组绑定（--skip-bind）")

    if not args.dry_run:
        verify(api, group)
    print("✅ 完成。回后台「账号管理」筛分组 {} 即可看到这批账号。".format(args.group))
    return 0


if __name__ == "__main__":
    sys.exit(main())
