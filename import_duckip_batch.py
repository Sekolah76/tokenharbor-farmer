#!/usr/bin/env python3
"""import_duckip_batch.py — 把本批 duckip 注册结果导进 sub2api（用户端接口），并按老规矩配好。

老规矩（与现有 383 个 tokenhour 账号保持一致）：
  · 平台 openai / type apikey / base_url https://tokenharbor.ai
  · model_mapping = {deepseek-v4.1-flash: deepseek-v4.1-flash:free,
                     mimo-v2.6-flash: mimo-v2.6-flash:free}
  · temp_unschedulable: 429 + free_tier_limit_reached → 停 7 天（10080 分钟），scope=account
  · extra: websockets_v2 off、长上下文计费关、provider=tokenharbor、group=tokenhour
  · priority=50 / rate_multiplier=1 / auto_pause_on_expired=true
  · 分组 tokenhour (id 4687)
  · 代理：从公共代理池按 延迟升序、占用数升序 取，排除 CN/HK/MO
  · 序号接着现有最大值往后编（tokenhour-384 → ...）

用法:
  # 1) 干跑：只看会做什么
  python3 import_duckip_batch.py --files th_keys_duckip_nt_*.txt --dry-run
  # 2) 真导
  python3 import_duckip_batch.py --files th_keys_duckip_nt_*.txt
"""
import argparse
import glob
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, "/Users/zxc/tokenharbor-farmer")
from ss2a_client import api

GROUP_ID = 4687                    # tokenhour
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


def retry(fn, n=4, delay=2.0):
    last = None
    for i in range(n):
        try:
            return fn()
        except Exception as e:
            last = e
            time.sleep(delay * (i + 1))
    raise last


def load_keys(files):
    """读 th_keys_*.txt（email\tpwd\tkey\tverified=..\ttest=..），按 key 去重。"""
    rows, seen = [], set()
    for pat in files:
        for path in sorted(glob.glob(pat)):
            for line in open(path, encoding="utf-8"):
                line = line.rstrip("\n")
                if not line.strip():
                    continue
                parts = line.split("\t")
                if len(parts) < 3:
                    continue
                email, pwd, key = parts[0], parts[1], parts[2]
                if not key.startswith("thk_"):
                    continue
                if key in seen:
                    continue
                seen.add(key)
                rows.append({"email": email, "password": pwd, "api_key": key,
                             "source": os.path.basename(path)})
    return rows


def fetch_existing(expect_all=True):
    """拉现有账号。接口会偶发返回不完整列表 —— 拉到数 != total 就重试，
    否则会按错误的序号起编、和历史账号撞名（2026-10-18 踩过一次）。"""
    got = []
    for attempt in range(6):
        st, js = api("GET", "/api/v1/accounts",
                     params={"search": "tokenhour", "page": 1, "page_size": 900})
        d = js.get("data") or {}
        items = d.get("items") or []
        total = d.get("total") or 0
        got = [a for a in items if (a.get("name") or "").startswith("tokenhour")]
        if not expect_all or (len(got) >= total > 0):
            return got
        time.sleep(2 + attempt * 2)
    return got


def seq_of(a):
    m = re.match(r"tokenhour-(\d+)", a.get("name") or "")
    return int(m.group(1)) if m else None


def fetch_proxies():
    st, js = api("GET", "/api/v1/proxies", params={"page": 1, "page_size": 300})
    d = js.get("data") or {}
    items = d.get("items") or (d.get("proxies") if isinstance(d, dict) else []) or []
    ok = [p for p in items
          if (p.get("country_code") or "").upper() not in BAD_CC
          and (p.get("status") or "active") == "active"]
    ok.sort(key=lambda p: ((p.get("latency_ms") or 99999), (p.get("account_count") or 0), p.get("id")))
    return ok


def build_account(seq, row):
    return {
        "name": f"tokenhour-{seq}|{row['email']}",
        "notes": f"TokenHarbor | 分组: {GROUP_NAME} | 节点: duckip:{row['source']}",
        "platform": "openai",
        "type": "apikey",
        "credentials": {
            "api_key": row["api_key"],
            "base_url": BASE_URL,
            "model_mapping": MODEL_MAPPING,
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
        },
        "concurrency": 1,
        "priority": 50,
        "rate_multiplier": 1,
        "auto_pause_on_expired": True,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", nargs="+", required=True)
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rows = load_keys(args.files)
    print(f"待导入 key: {len(rows)} 条（去重后）")
    if not rows:
        raise SystemExit("没有可导入的 key")

    existing = retry(fetch_existing)
    seqs = [s for s in (seq_of(a) for a in existing) if s]
    start = (max(seqs) + 1) if seqs else 1
    print(f"现有账号 {len(existing)} 个，最大序号 {max(seqs) if seqs else 0} → 新号从 {start} 开始")

    accounts = []
    for i, row in enumerate(rows):
        acc = build_account(start + i, row)
        acc["concurrency"] = args.concurrency
        accounts.append(acc)

    payload = {
        "data": {
            "type": "sub2api-data",
            "version": 1,
            "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "proxies": [],
            "accounts": accounts,
        },
        "skip_default_group_bind": True,
    }
    dump = f"runs/import_duckip_{time.strftime('%Y%m%d-%H%M%S')}.json"
    os.makedirs("runs", exist_ok=True)
    json.dump(payload, open(dump, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"导入文件 → {dump}（{len(accounts)} 个账号）")

    if args.dry_run:
        for a in accounts[:3]:
            print(json.dumps(a, ensure_ascii=False)[:400])
        print("干跑结束（未写入）")
        return

    # ---- 1) 导入 ----
    st, js = api("POST", "/api/v1/accounts/data", body=payload)
    print("导入响应:", st, json.dumps(js, ensure_ascii=False)[:300])
    created = (js.get("data") or {}).get("account_created") if isinstance(js, dict) else None
    failed = (js.get("data") or {}).get("account_failed") if isinstance(js, dict) else None
    print(f"→ 成功 {created} / 失败 {failed}")

    # ---- 2) 拉新账号 ----
    time.sleep(2)
    after = retry(fetch_existing)
    new = [a for a in after if (seq_of(a) or 0) >= start]
    new.sort(key=lambda a: seq_of(a) or 0)
    print(f"识别到新账号 {len(new)} 个")

    # ---- 3) 绑分组 ----
    gok = gfail = 0
    for a in new:
        try:
            st2, _ = api("PUT", f"/api/v1/accounts/{a['id']}",
                         body={"group_ids": [GROUP_ID], "confirm_mixed_channel_risk": True})
            gok += 1 if st2 == 200 else 0
            gfail += 0 if st2 == 200 else 1
        except Exception as e:
            gfail += 1
            print(f"  分组失败 {a['name']}: {str(e)[:60]}")
    print(f"分组绑定: 成功 {gok} / 失败 {gfail}")

    # ---- 4) 绑代理 ----
    proxies = retry(fetch_proxies)
    print(f"可用代理 {len(proxies)} 个（排除 CN/HK/MO）")
    used = {}
    for a in after:
        if a.get("proxy_id"):
            used[a["proxy_id"]] = used.get(a["proxy_id"], 0) + 1
    order = sorted(proxies, key=lambda p: ((p.get("latency_ms") or 99999),
                                           used.get(p["id"], 0) + (p.get("account_count") or 0)))
    pok = pfail = 0
    for i, a in enumerate(new):
        p = order[i % len(order)]
        try:
            st3, _ = api("PUT", f"/api/v1/accounts/{a['id']}", body={"proxy_id": p["id"]})
            pok += 1 if st3 == 200 else 0
            pfail += 0 if st3 == 200 else 1
        except Exception as e:
            pfail += 1
            print(f"  代理失败 {a['name']}: {str(e)[:60]}")
    print(f"代理绑定: 成功 {pok} / 失败 {pfail}")

    # ---- 5) 复核 ----
    time.sleep(2)
    final = retry(fetch_existing)
    mine = [a for a in final if (seq_of(a) or 0) >= start]
    print(f"\n=== 复核（新号 {len(mine)} 个）===")
    bad = []
    for a in mine:
        probs = []
        if GROUP_ID not in (a.get("group_ids") or []):
            probs.append("无分组")
        if not a.get("proxy_id"):
            probs.append("无代理")
        if not (a.get("credentials_status") or {}).get("has_api_key"):
            probs.append("无key")
        if a.get("priority") != 50:
            probs.append(f"priority={a.get('priority')}")
        if a.get("concurrency") != args.concurrency:
            probs.append(f"concurrency={a.get('concurrency')}")
        if probs:
            bad.append((a.get("name"), probs))
    print(f"全部合规 {len(mine) - len(bad)} / {len(mine)}")
    for n, p in bad[:10]:
        print("  ⚠", n, p)
    print(f"\n完成。新号序号 {start} ~ {start + len(accounts) - 1}")


if __name__ == "__main__":
    main()
