#!/usr/bin/env python3
"""proxy_health_rotate.py — 代理失效自动换一个（随机从健康池里挑）。

判断依据（service 端已经算好，不要自己瞎探）：
  每个代理对象自带这些字段：
    latency_status   success | failed        ← 连不连得上（failed = 真挂）
    latency_message  失败原因，如 "all probe URLs failed, last error: ... 429"
    quality_status   healthy | failed | challenge | null   ← 业务质量
    quality_grade    A | B | C | D | F | null
    quality_score    0-100
    quality_checked  unix 时间戳
    account_count    当前承载账号数

为什么不用自己 TCP 探活：
  公共代理列表里 protocol/host/port **全是空的**（不暴露坐标，防滥用），
  本地根本没地址可连。之前用 `socket.create_connection(cloudflare)` 是把
  「本机能否上网」当成「代理好不好」，得到的全是假信号（119 个里报 45 个失败，
  而服务端实测只有 2 个 failed）。

sub2api 原生能力边界：
  代理只有 fallback_mode = none|proxy|direct + 固定 backup_proxy_id，
  **不能随机轮换**。所以「随机换」必须由本脚本主动巡检 + 改 proxy_id 实现。

用法:
  .venv/bin/python proxy_health_rotate.py --dry-run          # 只看要换哪些
  .venv/bin/python proxy_health_rotate.py                    # 真换
  .venv/bin/python proxy_health_rotate.py --watch 300        # 每 5 分钟巡检一次
  .venv/bin/python proxy_health_rotate.py --enable-fallback direct   # 顺手开兜底
"""
import argparse
import json
import os
import random
import sys
import time
from collections import Counter

sys.path.insert(0, "/Users/zxc/tokenharbor-farmer")
from ss2a_client import api

BAD_CC = {"CN", "HK", "MO"}          # tokenharbor 对这三地 403 region_blocked
SNAP_DIR = "runs/snapshots"


def retry(fn, n=4, delay=1.5):
    last = None
    for i in range(n):
        try:
            return fn()
        except Exception as e:
            last = e
            time.sleep(delay * (i + 1))
    raise last


def fetch_accounts(prefix="tokenhour"):
    got = []
    for attempt in range(8):
        st, js = api("GET", "/api/v1/accounts",
                     params={"search": prefix, "page": 1, "page_size": 900})
        d = js.get("data") or {}
        items = d.get("items") or []
        total = d.get("total") or 0
        got = [a for a in items if (a.get("name") or "").startswith(prefix)]
        if len(got) >= total > 0:
            return got
        time.sleep(2 + attempt * 2)
    return got


def fetch_proxies():
    st, js = api("GET", "/api/v1/proxies", params={"page": 1, "page_size": 300})
    d = js.get("data") or {}
    return d.get("items") or d.get("proxies") or []


def proxy_ok(p):
    """代理是否可用：latency 没 failed + quality 没 failed + 地区合格 + active。"""
    if (p.get("status") or "active") != "active":
        return False, "status!=active"
    if (p.get("latency_status") or "") == "failed":
        return False, f"latency failed: {str(p.get('latency_message'))[:50]}"
    if (p.get("quality_status") or "") == "failed":
        return False, f"quality failed (grade {p.get('quality_grade')})"
    cc = (p.get("country_code") or "").upper()
    name = (p.get("name") or "")
    # 国家码字段可能为空，退化成看名字里的中文地区
    for bad, cn in (("HK", "香港"), ("CN", "中国"), ("MO", "澳门")):
        if cc == bad or cn in name:
            return False, f"地区不合格({bad})"
    return True, ""


def snapshot(prefix, label):
    os.makedirs(SNAP_DIR, exist_ok=True)
    accs = fetch_accounts(prefix)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = f"{SNAP_DIR}/accounts_{prefix}_{label}_{stamp}.json"
    rows = [{k: a.get(k) for k in ("id", "name", "credentials", "extra",
                                   "proxy_id", "group_ids", "concurrency", "priority")}
            for a in accs]
    json.dump({"prefix": prefix, "label": label, "created_at": stamp,
               "count": len(rows), "accounts": rows},
              open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"  ✓ 快照 → {path}（{len(rows)} 个）")
    return path


def run_once(A):
    accs = fetch_accounts(A.prefix)
    proxies = fetch_proxies()
    pmap = {p["id"]: p for p in proxies}

    ok_pool, bad_list = [], []
    for p in proxies:
        good, why = proxy_ok(p)
        (ok_pool if good else bad_list).append(p if good else (p, why))

    print(f"账号 {len(accs)} | 代理 {len(proxies)} | 健康 {len(ok_pool)} | 问题 {len(bad_list)}")
    if bad_list:
        print("  问题代理:")
        for p, why in bad_list[:8]:
            print(f"    #{p['id']} {(p.get('name') or '')[:26]:28} {why}")

    load = Counter(a.get("proxy_id") for a in accs if a.get("proxy_id"))

    # 账号当前代理不在健康池 → 需要换
    good_ids = {p["id"] for p in ok_pool}
    need = []
    for a in accs:
        pid = a.get("proxy_id")
        if not pid:
            need.append((a, "无代理"))
        elif pid not in good_ids:
            p = pmap.get(pid)
            why = "代理已删除" if not p else dict((x[0]["id"], x[1]) for x in bad_list).get(pid, "不可用")
            need.append((a, why))

    print(f"\n需要换代理的账号 {len(need)} 个")
    for a, why in need[:10]:
        print(f"  {a['name'][:34]:36} ← {why}")

    if not need:
        return 0, 0

    # 候选池：健康 + 承载最少的那批，随机挑（用户要「随机一个」）
    ok_pool.sort(key=lambda p: load.get(p["id"], 0))
    top = ok_pool[: max(15, len(ok_pool) // 3)]
    print(f"候选池 {len(top)} 个（健康且承载最少）")

    if A.dry_run:
        for a, why in need[:5]:
            print(f"  [干跑] {a['name'][:30]} {a.get('proxy_id')} → #{random.choice(top)['id']}")
        return 0, len(need)

    if not A.no_snapshot:
        print("\n改前快照:")
        snapshot(A.prefix, f"before_proxyrotate_{time.strftime('%H%M%S')}")

    okn = failn = 0
    t0 = time.time()
    for i, (a, _) in enumerate(need, 1):
        newp = random.choice(top)
        try:
            st, _ = retry(lambda: api("PUT", f"/api/v1/accounts/{a['id']}",
                                      body={"proxy_id": newp["id"]}))
            if st == 200:
                okn += 1
                load[newp["id"]] += 1
            else:
                failn += 1
        except Exception as e:
            failn += 1
            print(f"  ✗ {a['name'][:30]}: {str(e)[:60]}")
        if i % 20 == 0:
            print(f"  ... {i}/{len(need)} 成功 {okn} 失败 {failn} ({time.time()-t0:.0f}s)",
                  flush=True)
    print(f"\n换代理: 成功 {okn} | 失败 {failn} | {time.time()-t0:.0f}s")
    return okn, failn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", default="tokenhour")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-snapshot", action="store_true")
    ap.add_argument("--watch", type=int, default=0, help="每 N 秒巡检一次（0=只跑一次）")
    ap.add_argument("--enable-fallback", choices=["none", "proxy", "direct"], default=None)
    A = ap.parse_args()

    if A.enable_fallback:
        proxies = fetch_proxies()
        ok_pool = [p for p in proxies if proxy_ok(p)[0]]
        done = 0
        for p in ok_pool:
            body = {"fallback_mode": A.enable_fallback}
            if A.enable_fallback == "proxy":
                others = [x for x in ok_pool if x["id"] != p["id"]]
                if not others:
                    continue
                body["backup_proxy_id"] = random.choice(others)["id"]
            if A.dry_run:
                done += 1
                continue
            try:
                st, _ = api("PUT", f"/api/v1/proxies/{p['id']}", body=body)
                done += 1 if st == 200 else 0
            except Exception:
                pass
        print(f"fallback={A.enable_fallback}: 设置 {done} 个代理"
              f"{'（干跑）' if A.dry_run else ''}\n")

    if A.watch <= 0:
        run_once(A)
        return

    print(f"看护模式：每 {A.watch}s 巡检一次（Ctrl-C 退出）")
    while True:
        try:
            print(f"\n=== {time.strftime('%H:%M:%S')} ===")
            run_once(A)
        except Exception as e:
            print(f"巡检异常: {type(e).__name__}: {str(e)[:80]}")
        time.sleep(A.watch)


if __name__ == "__main__":
    main()
