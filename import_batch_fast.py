#!/usr/bin/env python3
"""import_batch_fast.py — 高速批量导入 TokenHarbor key 到 sub2api

流程：去重 → 快照 → 分批导入 → 并发绑分组 → 并发绑代理（均匀分配）→ 复核

用法:
  .venv/bin/python import_batch_fast.py --files 'th_keys_1024v*.txt' --dry-run
  .venv/bin/python import_batch_fast.py --files 'th_keys_1024v*.txt'
"""
import argparse, glob, json, os, re, sys, time
sys.path.insert(0, "/Users/zxc/tokenharbor-farmer")
from ss2a_client import api as slow_api
from ss2a_fast import api, pmap

GROUP_ID = 4687
GROUP_NAME = "tokenhour"
BASE_URL = "https://tokenharbor.ai"
MODEL_MAPPING = {
    "deepseek-v4.1-flash": "deepseek-v4.1-flash:free",
    "mimo-v2.6-flash": "mimo-v2.6-flash:free",
}
TEMP_RULES = [{
    "error_code": 429,
    "keywords": ["free_tier_limit_reached"],
    "duration_minutes": 10080,
    "scope": "account",
    "description": "免费额度用尽(滚动7天) → 停7天",
}]
BAD_CC = {"CN", "HK", "MO"}


def fetch_all_accounts(prefix="tokenhour-", tries=8):
    """拉全量账号；条数不足 total 就重试（接口偶发不全）"""
    for _ in range(tries):
        st, js = api("GET", "/api/v1/accounts",
                     params={"search": prefix, "page": 1, "page_size": 900}, timeout=90)
        d = (js or {}).get("data") or {}
        items = d.get("items") or []
        total = d.get("total")
        if isinstance(total, int) and len(items) >= total:
            return items
        time.sleep(1.5)
    return items


def seq_of(a):
    m = re.match(r"tokenhour-(\d+)", a.get("name") or "")
    return int(m.group(1)) if m else None


def load_keys(patterns):
    rows, seen_key, seen_mail, files = [], set(), set(), []
    for pat in patterns:
        for f in sorted(glob.glob(pat)):
            files.append(f)
            for line in open(f, encoding="utf-8"):
                p = line.rstrip("\n").split("\t")
                if len(p) < 3:
                    continue
                email, pwd, key = p[0], p[1], p[2]
                if not key.startswith("thk_live_") or key in seen_key or email in seen_mail:
                    continue
                seen_key.add(key); seen_mail.add(email)
                rows.append({"email": email, "password": pwd, "api_key": key, "source": os.path.basename(f)})
    return rows, files


def build_account(seq, row):
    return {
        "name": f"tokenhour-{seq:03d}|{row['email']}",
        "notes": f"TokenHarbor 池模式 | 分组: {GROUP_NAME} | 节点: -",
        "platform": "openai",
        "type": "apikey",
        "credentials": {
            "api_key": row["api_key"],
            "base_url": BASE_URL,
            "model_mapping": MODEL_MAPPING,
            "pool_mode": True,
            "pool_mode_retry_count": 3,
            "temp_unschedulable_enabled": True,
            "temp_unschedulable_rules": TEMP_RULES,
        },
        "extra": {
            "email": row["email"],
            "group": GROUP_NAME,
            "provider": "tokenharbor",
            "openai_apikey_responses_websockets_v2_enabled": False,
            "openai_apikey_responses_websockets_v2_mode": "off",
            "openai_long_context_billing_enabled": False,
            "source": row["source"],
        },
        "concurrency": 1,
        "priority": 50,
        "rate_multiplier": 1,
        "auto_pause_on_expired": True,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", nargs="+", required=True)
    ap.add_argument("--chunk", type=int, default=100, help="每次导入的账号数")
    ap.add_argument("--workers", type=int, default=32, help="绑分组/代理的并发")
    ap.add_argument("--only-private", action="store_true", default=True, help="只用自己名下的私有代理")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    t_start = time.time()
    rows, files = load_keys(args.files)
    print(f"读取 {len(files)} 个文件 → 唯一账号 {len(rows)} 个")

    existing = fetch_all_accounts()
    ex_emails = {(a.get("extra") or {}).get("email") for a in existing}
    ex_emails.discard(None)
    todo = [r for r in rows if r["email"] not in ex_emails]
    print(f"面板现有 {len(existing)} 个 tokenhour 账号；待导入 {len(todo)} 个")

    if not todo:
        print("没有需要导入的账号")
        return

    # 序号：接现有最大值往后编
    nums = [seq_of(a) for a in existing if seq_of(a) is not None]
    start = (max(nums) + 1) if nums else 1
    used_names = {a.get("name") for a in existing}
    accounts, seq = [], start
    for r in todo:
        while f"tokenhour-{seq:03d}|{r['email']}" in used_names:
            seq += 1
        accounts.append(build_account(seq, r))
        used_names.add(f"tokenhour-{seq:03d}|{r['email']}")
        seq += 1
    print(f"命名区间: tokenhour-{start:03d} … tokenhour-{seq-1:03d}")

    payload_path = f"runs/import_v6_{time.strftime('%Y%m%d-%H%M%S')}.json"
    os.makedirs("runs", exist_ok=True)
    json.dump({"accounts": accounts}, open(payload_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"导入文件 → {payload_path}")

    if args.dry_run:
        print("样例:", json.dumps(accounts[0], ensure_ascii=False)[:400])
        print("干跑结束（未写入）")
        return

    # ---- 0) 快照 ----
    print("\n[0/4] 拍快照（改前必须）...")
    rc = os.system(f'cd /Users/zxc/tokenharbor-farmer && .venv/bin/python ss2a_snapshot.py '
                   f'--prefix tokenhour --label before_import_v6')
    print(f"快照退出码 {rc >> 8}")

    # ---- 1) 分批导入 ----
    print(f"\n[1/4] 分批导入（每批 {args.chunk}）...")
    created = failed = 0
    for i in range(0, len(accounts), args.chunk):
        chunk = accounts[i:i + args.chunk]
        body = {"data": {"type": "sub2api-data", "version": 1,
                         "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                         "proxies": [], "accounts": chunk},
                "skip_default_group_bind": True}
        st, js = api("POST", "/api/v1/accounts/data", body=body, timeout=180)
        d = (js or {}).get("data") or {}
        c, f = d.get("account_created", 0), d.get("account_failed", 0)
        created += c or 0; failed += f or 0
        print(f"  批 {i//args.chunk+1}: http={st} 新建={c} 失败={f}  ({time.time()-t_start:.0f}s)")

    # ---- 2) 定位新账号 ----
    print(f"\n[2/4] 回读账号列表定位 id...")
    time.sleep(2)
    after = fetch_all_accounts()
    by_email = {(a.get("extra") or {}).get("email"): a for a in after}
    new = [by_email[r["email"]] for r in todo if r["email"] in by_email]
    print(f"面板共 {len(after)} 个，匹配到本批 {len(new)} 个")

    # ---- 3) 并发绑分组 ----
    print(f"\n[3/4] 并发绑分组（{args.workers} 线程）...")
    def bind_group(a):
        st, _ = api("PUT", f"/api/v1/accounts/{a['id']}",
                    body={"group_ids": [GROUP_ID], "confirm_mixed_channel_risk": True})
        return st == 200
    gres = pmap(bind_group, new, workers=args.workers)
    gok = sum(1 for x in gres if x is True)
    print(f"  分组绑定 成功 {gok}/{len(new)}")

    # ---- 4) 并发绑代理（自己名下私有代理，均匀分配）----
    print(f"\n[4/4] 并发绑代理（自己名下私有代理，均匀分配）...")
    st, js = api("GET", "/api/v1/proxies", params={"page": 1, "page_size": 500}, timeout=90)
    prox = (js.get("data") or {}).get("items") or []
    mine = [p for p in prox if not p.get("is_public")]
    healthy = [p for p in mine
               if (p.get("latency_status") != "failed")
               and (p.get("status") or "active") == "active"
               and (p.get("country_code") or "").upper() not in BAD_CC]
    healthy.sort(key=lambda p: p["id"])
    print(f"  私有代理 {len(mine)} 个，健康可用 {len(healthy)} 个")
    if not healthy:
        print("  没有可用私有代理，跳过")
        return
    assignment = {}
    for i, a in enumerate(new):
        assignment[a["id"]] = healthy[i % len(healthy)]["id"]
    def bind_proxy(a):
        st, _ = api("PUT", f"/api/v1/accounts/{a['id']}", body={"proxy_id": assignment[a["id"]]})
        return st == 200
    pres = pmap(bind_proxy, new, workers=args.workers)
    pok = sum(1 for x in pres if x is True)
    from collections import Counter
    dist = Counter(assignment.values())
    print(f"  代理绑定 成功 {pok}/{len(new)}；分布 min={min(dist.values())} max={max(dist.values())} 用到的代理 {len(dist)} 个")

    # ---- 复核 ----
    print(f"\n=== 复核 ===")
    final = fetch_all_accounts()
    target_emails = {r["email"] for r in todo}
    mine_final = [a for a in final if (a.get("extra") or {}).get("email") in target_emails]
    no_group = [a for a in mine_final if GROUP_ID not in (a.get("group_ids") or [])]
    no_proxy = [a for a in mine_final if not a.get("proxy_id")]
    bad_conc = [a for a in mine_final if a.get("concurrency") != 1]
    has_key = [a for a in mine_final if (a.get("credentials_status") or {}).get("has_api_key")]
    print(f"  本批账号 {len(mine_final)}/{len(todo)}")
    print(f"  无分组 {len(no_group)} | 无代理 {len(no_proxy)} | 并发≠1 {len(bad_conc)} | 有key {len(has_key)}")
    print(f"  总耗时 {time.time()-t_start:.1f}s")


if __name__ == "__main__":
    main()
