#!/usr/bin/env python3
"""ss2a_snapshot.py — 改任何字段前，先把账号整体导出成可回退快照。

为什么必须做：
  2026-10-20 踩过两次：
    ① PUT credentials 传 api_key:None → key 被清空（credentials_status 变 null）
    ② 接口偶发返回不全，序号从 1 重编 → 和历史账号撞名
  一旦批量改错，没有快照就只能逐个手工修。

快照内容：每个账号的**完整可回退字段**（不含 api_key —— 读取接口不回显，
回退时用「省略 api_key」模式，实测系统会保留原值）。

用法:
  .venv/bin/python ss2a_snapshot.py                     # tokenhour 全量
  .venv/bin/python ss2a_snapshot.py --prefix tokenhour --label before_rule
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, "/Users/zxc/tokenharbor-farmer")
from ss2a_client import api

SNAP_DIR = "runs/snapshots"

# 这些字段是「可回退」的：PUT 能写回去
SNAP_FIELDS = ("id", "name", "notes", "platform", "type", "credentials",
               "extra", "proxy_id", "group_ids", "concurrency", "priority",
               "rate_multiplier", "auto_pause_on_expired", "status")


def fetch_all(prefix):
    """拉全量；条数不足 total 就重试（接口会偶发返回不全）。"""
    got = []
    for attempt in range(8):
        st, js = api("GET", "/api/v1/accounts",
                     params={"search": prefix, "page": 1, "page_size": 900})
        d = js.get("data") or {}
        items = d.get("items") or []
        total = d.get("total") or 0
        got = [a for a in items if (a.get("name") or "").startswith(prefix)]
        if len(got) >= total > 0:
            return got, total
        print(f"  ⚠ 拉取不全（{len(got)}/{total}），重试 {attempt+1}/8")
        time.sleep(2 + attempt * 2)
    return got, total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", default="tokenhour")
    ap.add_argument("--label", default="manual")
    A = ap.parse_args()

    os.makedirs(SNAP_DIR, exist_ok=True)
    accs, total = fetch_all(A.prefix)
    print(f"{A.prefix} 账号 {len(accs)} 个（接口 total={total}）")
    if len(accs) < total:
        print("✗ 拉取数量不足，拒绝出快照（不完整快照 = 假安全）")
        sys.exit(1)

    snap = []
    for a in accs:
        row = {k: a.get(k) for k in SNAP_FIELDS}
        row["_has_api_key"] = (a.get("credentials_status") or {}).get("has_api_key")
        snap.append(row)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = f"{SNAP_DIR}/accounts_{A.prefix}_{A.label}_{stamp}.json"
    json.dump({"prefix": A.prefix, "label": A.label, "created_at": stamp,
               "count": len(snap), "accounts": snap},
              open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"✓ 快照 → {path}（{len(snap)} 个账号）")

    # 摘要，便于人工核对
    keys = sum(1 for s in snap if s["_has_api_key"])
    prox = sum(1 for s in snap if s.get("proxy_id"))
    grp = sum(1 for s in snap if s.get("group_ids"))
    print(f"  含 api_key: {keys} | 有代理: {prox} | 有分组: {grp}")
    return path


if __name__ == "__main__":
    main()
