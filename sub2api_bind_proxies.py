#!/usr/bin/env python3
"""sub2api_bind_proxies.py — 把一批代理「一对一」配给账号：生成映射 JSON，可一键写回 sub2api

两种用法：
  1) 只生成映射（离线，产出一个 json）
     python3 sub2api_bind_proxies.py --proxies-json runs/proxies-public-122.json -i sub2api-tokenhour.json

  2) 生成映射并直接绑定（走 sub2api API；按账号名里的序号配对，不会错位）
     python3 sub2api_bind_proxies.py --proxies-json ... --apply \
         --base-url https://your-sub2api.example.com --token eyJhbGci...

代理清单支持这些形状：
  {items:[...]}（sub2api 代理列表分页响应）/ {proxies:[...]} / {data:{proxies:[...]}} / [...]

分配策略（默认）：
  排除地区(HK/CN/MO，tokenharbor 对这三地返回 403 region_blocked)
  → 按 质量等级(A>B>C>无>D>F) → 延迟 → 已占用账号数 排序
  → 取前 N 个一对一（不够时加 --allow-reuse 循环复用）

绑定原理：sub2api 里账号靠 accounts.proxy_id 关联代理，所以
  `PUT /api/v1/admin/accounts/{id} {"proxy_id": N}` 就是"给这个账号配这个代理"。
  （导入文件里的 proxy_key 需要 protocol|host|port|username|password 拼串，
   而代理列表接口不返回连接信息——protocol/host/port 是空的——所以走 id 绑定这条路）
"""
import argparse
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

GRADE_RANK = {"A": 0, "B": 1, "C": 2, "": 3, "?": 3, "D": 4, "F": 5}


# ------------------------------------------------------------------ 数据载入
def load_proxies(path):
    doc = json.load(open(path, encoding="utf-8"))
    if isinstance(doc, list):
        return doc
    if isinstance(doc, dict):
        for key in ("items", "proxies"):
            if isinstance(doc.get(key), list):
                return doc[key]
        data = doc.get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("proxies"), list):
            return data["proxies"]
    raise SystemExit(f"❌ 认不出代理清单结构: {path}（期望 items/proxies/data.proxies/数组）")


def load_account_names(path, prefix):
    doc = json.load(open(path, encoding="utf-8"))
    accts = doc["accounts"] if isinstance(doc, dict) else doc
    return [a.get("name", "") for a in accts if str(a.get("name", "")).startswith(prefix)]


def account_seq(name, prefix):
    m = re.match(rf"^{re.escape(prefix)}(\d+)", str(name))
    return int(m.group(1)) if m else None


# ------------------------------------------------------------------ 分配
def build_plan(proxies, names, args):
    # 配对方式：seq=按账号名里的序号（tokenhour-001）；order=按名字排序后的顺序
    pair_mode = args.pair_by
    if pair_mode == "auto":
        pair_mode = "seq" if names and all(account_seq(n, args.name_prefix) for n in names) else "order"
    if pair_mode == "order":
        names = sorted(names)

    excl = {c.strip().upper() for c in args.exclude_countries.split(",") if c.strip()}
    cands, skipped_country, skipped_status = [], 0, 0
    for p in proxies:
        if (p.get("status") or "active") != "active":
            skipped_status += 1
            continue
        if (p.get("country_code") or "").upper() in excl:
            skipped_country += 1
            continue
        cands.append(p)

    cands.sort(key=lambda p: (
        GRADE_RANK.get((p.get("quality_grade") or "").upper(), 3),
        p.get("latency_ms") if p.get("latency_ms") is not None else 9999,
        p.get("account_count") or 0,
        p.get("id") or 0,
    ))

    need = len(names)
    if args.allow_reuse and cands:
        picks = [cands[i % len(cands)] for i in range(need)]
    else:
        picks = cands[:need]

    plan = []
    for i, name in enumerate(names):
        if i >= len(picks):
            break
        p = picks[i]
        plan.append({
            "account_index": (account_seq(name, args.name_prefix) or (i + 1)) if pair_mode == "seq" else (i + 1),
            "account_name": name,
            "proxy_id": p.get("id"),
            "proxy_name": p.get("name"),
            "country_code": p.get("country_code"),
            "city": p.get("city"),
            "latency_ms": p.get("latency_ms"),
            "quality_grade": p.get("quality_grade"),
            "account_count_before": p.get("account_count"),
            "ip_address": p.get("ip_address"),
            "account_id": None,
            "applied": None,
        })
    stats = {
        "proxies_total": len(proxies),
        "excluded_country": skipped_country,
        "excluded_status": skipped_status,
        "candidates": len(cands),
        "accounts": len(names),
        "assigned": len(plan),
    }
    return plan, stats


# ------------------------------------------------------------------ API
class Api:
    def __init__(self, base_url, token=None, insecure=False, dry_run=False):
        self.base = base_url.rstrip("/")
        self.token = token
        self.insecure = insecure
        self.dry_run = dry_run

    def call(self, method, path, body=None, write=False):
        url = self.base + path
        if write and self.dry_run:
            print(f"   [dry-run] {method} {path} {json.dumps(body, ensure_ascii=False)}")
            return {"code": 0, "data": {}}
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        ctx = ssl._create_unverified_context() if self.insecure else None
        try:
            with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
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
            raise SystemExit(f"❌ {method} {path} 返回非 JSON（HTTP {status}）: {raw[:200]}")
        if status >= 400 or payload.get("code") not in (0, None):
            raise SystemExit(f"❌ {method} {path} 失败（HTTP {status}）: {payload.get('message') or raw[:200]}")
        return payload

    def login(self, email, password):
        res = self.call("POST", "/api/v1/auth/login", {"email": email, "password": password}, write=True)
        token = (res.get("data") or {}).get("access_token")
        if not token:
            raise SystemExit("❌ 登录没拿到 access_token，改用 --token")
        self.token = token
        return token

    def list_accounts(self, prefix, flavor="admin"):
        base_path = "/api/v1/accounts" if flavor == "user" else "/api/v1/admin/accounts"
        found, page = [], 1
        while True:
            q = urllib.parse.urlencode({"page": page, "page_size": 100, "search": prefix})
            data = self.call("GET", f"{base_path}?{q}").get("data") or {}
            items = data.get("items") or []
            found.extend(a for a in items if not prefix or str(a.get("name", "")).startswith(prefix))
            if not items or page * 100 >= (data.get("total") or 0):
                return found
            page += 1


# ------------------------------------------------------------------ 导出「账号+proxy_id」导入文件
def export_import_file(args, plan):
    """生成 sub2api 导入文件（sub2api-data v1）：账号照抄，额外带 proxy_id 引用。

    为什么不写 proxies[] / proxy_key：
      - 这批是"公共代理"，列表/详情接口里 host=""、port=0（坐标由平台托管、不对外暴露）
      - 而导入校验（validateDataProxy）要求 host 非空、port 在 1-65535，
        写空坐标进去只会让代理和账号双双导入失败
      - 所以只在账号条目上带 proxy_id（扩展字段）：你们实例认，就一步到位；
        不认也只是被忽略，账号照常导入，之后再跑本脚本 --apply 补绑定
    """
    acc_doc = json.load(open(args.input, encoding="utf-8"))
    by_seq = {x["account_index"]: x for x in plan}
    out_accounts = []
    for a in acc_doc["accounts"]:
        item = dict(a)
        seq = account_seq(item.get("name"), args.name_prefix)
        p = by_seq.get(seq)
        if p:
            item["proxy_id"] = p["proxy_id"]
            item["notes"] = (item.get("notes") or "") + f" | 代理#{p['proxy_id']} {p['country_code']}"
        out_accounts.append(item)

    out = {
        "type": "sub2api-data",
        "version": 1,
        "exported_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "proxies": [],
        "accounts": out_accounts,
    }
    here = os.path.dirname(os.path.abspath(__file__))
    path = args.export_file if os.path.isabs(args.export_file) else os.path.join(here, args.export_file)
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    with_proxy = sum(1 for x in out_accounts if "proxy_id" in x)
    print(f"📤 导入文件已导出: {path}")
    print(f"   {len(out_accounts)} 个账号（{with_proxy} 个带 proxy_id；proxies 留空——公共代理没坐标，写 key 会被导入校验拒）")
    return path


# ------------------------------------------------------------------ main
def main():
    p = argparse.ArgumentParser(description="把代理一对一配给账号（映射 JSON + 可选直接绑定）")
    p.add_argument("--proxies-json", default="runs/proxies-public-122.json",
                   help="代理清单（items / proxies / data.proxies / 数组）")
    p.add_argument("-i", "--input", default="sub2api-tokenhour.json", help="账号清单（取名字与序号）")
    p.add_argument("-o", "--out", default="sub2api-tokenhour-proxy-map.json", help="映射 JSON 输出路径")
    p.add_argument("--name-prefix", default="tokenhour-", help="账号名前缀（默认 tokenhour-；顺序配对时可留空）")
    p.add_argument("--api-flavor", choices=["admin", "user"], default="admin",
                   help="接口口径：admin=/api/v1/admin/accounts；user=/api/v1/accounts（普通用户面板）")
    p.add_argument("--pair-by", choices=["auto", "seq", "order"], default="auto",
                   help="账号↔映射的配对方式：seq 按名字序号 / order 按名字排序逐位对齐（默认 auto）")
    p.add_argument("--exclude-countries", default="HK,CN,MO",
                   help="排除的出口地区（默认 HK,CN,MO：tokenharbor 会 403 region_blocked）")
    p.add_argument("--allow-reuse", action="store_true", help="代理不够时允许一个代理扛多个账号")
    p.add_argument("--apply", action="store_true", help="生成映射后直接调 API 绑定到 sub2api")
    p.add_argument("--limit", type=int, default=0, help="只处理前 N 个账号（0=全部，便于试跑）")
    p.add_argument("--base-url", help="sub2api 地址（--apply 时必填）")
    p.add_argument("--token", help="admin access_token")
    p.add_argument("--email", help="管理员邮箱（配合 --password 登录）")
    p.add_argument("--password", help="管理员密码")
    p.add_argument("--insecure", action="store_true", help="忽略 HTTPS 证书校验")
    p.add_argument("--dry-run", action="store_true", help="--apply 时只打印将要执行的请求")
    p.add_argument("--export-file", default=None,
                   help="额外导出一份『账号 + proxy_id』的 sub2api 导入文件（试实例导入认不认 proxy_id）")
    args = p.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    for attr in ("proxies_json", "input", "out"):
        v = getattr(args, attr)
        if not os.path.isabs(v):
            setattr(args, attr, os.path.join(here, v))

    proxies = load_proxies(args.proxies_json)
    names = load_account_names(args.input, args.name_prefix)
    if args.limit:
        names = names[: args.limit]
    print(f"📦 代理 {len(proxies)} 个（{os.path.basename(args.proxies_json)}）｜账号 {len(names)} 个")

    plan, stats = build_plan(proxies, names, args)
    print(f"🎯 分配: 候选 {stats['candidates']}｜排除地区 {stats['excluded_country']}｜配对 {stats['assigned']}")
    if not plan:
        raise SystemExit("❌ 没有可分配的代理（检查 --exclude-countries / 清单内容）")
    s = plan[0]
    print(f"   示例: {s['account_name']} → #{s['proxy_id']} {s['proxy_name']} "
          f"({s['country_code']} {s['latency_ms']}ms {s['quality_grade']})")

    doc = {
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "source_proxies": os.path.basename(args.proxies_json),
        "source_accounts": os.path.basename(args.input),
        "policy": {
            "mode": "one-to-one(reuse)" if args.allow_reuse else "one-to-one",
            "exclude_countries": args.exclude_countries,
            "order": "quality_grade -> latency_ms -> account_count",
            "binding": "accounts.proxy_id <- proxies.id (PUT /api/v1/admin/accounts/{id})",
        },
        "stats": stats,
        "assignments": plan,
    }

    if not args.apply:
        json.dump(doc, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"\n✅ 映射已导出: {args.out}")
        if args.export_file:
            export_import_file(args, plan)
        print("   要真绑定：加 --apply --base-url https://你的sub2api --token <admin-token>")
        return 0

    if args.export_file:
        export_import_file(args, plan)

    # ---- apply ----
    if not args.base_url:
        raise SystemExit("❌ --apply 需要 --base-url")
    api = Api(args.base_url, token=args.token, insecure=args.insecure, dry_run=args.dry_run)
    if not api.token:
        if not (args.email and args.password):
            raise SystemExit("❌ 需要 --token，或者 --email + --password")
        api.login(args.email, args.password)
        print("🔑 登录成功")

    accounts = api.list_accounts(args.name_prefix, flavor=args.api_flavor)
    print(f"🔎 {args.base_url} 上匹配 '{args.name_prefix or '*'}' 的账号 {len(accounts)} 个（{args.api_flavor} 端）")

    # 配对：seq=按名字序号；order=按名字排序后逐位对齐
    pair_mode = args.pair_by
    if pair_mode == "auto":
        pair_mode = "seq" if all(account_seq(a.get("name"), args.name_prefix) for a in accounts) and accounts else "order"
    by_seq, ordered = {}, sorted(accounts, key=lambda a: str(a.get("name", "")))
    for a in accounts:
        seq = account_seq(a.get("name"), args.name_prefix)
        if seq:
            by_seq[seq] = a

    ok = fail = miss = 0
    for idx, item in enumerate(plan):
        if pair_mode == "seq":
            acc = by_seq.get(item["account_index"])
        else:
            acc = ordered[idx] if idx < len(ordered) else None
            if acc and acc.get("name") != item["account_name"]:
                acc = None  # 名字对不上就跳过，宁可不绑也不绑错
        if not acc:
            item["applied"] = False
            item["error"] = "sub2api 上没找到对应账号（按序号/顺序配对失败）"
            miss += 1
            continue
        item["account_id"] = acc.get("id")
        if args.api_flavor == "user":
            path, body = f"/api/v1/accounts/{acc['id']}", {"proxy_id": item["proxy_id"]}
        else:
            path, body = (f"/api/v1/admin/accounts/{acc['id']}",
                          {"name": acc.get("name"), "proxy_id": item["proxy_id"],
                           "confirm_mixed_channel_risk": True})
        try:
            api.call("PUT", path, body, write=True)
            item["applied"] = True
            ok += 1
            print(f"   ✅ #{acc['id']} {acc.get('name')} ← 代理 #{item['proxy_id']} ({item['country_code']})")
        except SystemExit as exc:
            item["applied"] = False
            item["error"] = str(exc)[:160]
            fail += 1
            print(f"   ❌ #{acc.get('id')} {acc.get('name')}: {item['error']}")

    doc["applied_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    doc["apply_result"] = {"ok": ok, "failed": fail, "not_found": miss}
    json.dump(doc, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n📊 绑定: 成功 {ok}｜失败 {fail}｜未匹配 {miss}")
    print(f"📄 映射文件: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
