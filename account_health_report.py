#!/usr/bin/env python3
"""account_health_report.py — 拉取 sub2api 账号健康度并分类（可调度 / 停用 / 冷却中）

用法：
  .venv/bin/python account_health_report.py --group 4687
  .venv/bin/python account_health_report.py               # 全部账号
"""
import argparse
import collections
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ss2a_fast import api  # noqa: E402

FIELDS = ("id", "name", "status", "schedulable", "priority", "concurrency", "proxy_id",
          "group_ids", "last_used_at", "error_message", "temp_unschedulable",
          "temp_unschedulable_reason", "temp_unschedulable_until", "auto_pause_on_expired",
          "expires_at", "rate_multiplier")


def fetch_accounts():
    """拉全量账号（校验条数对得上 total）"""
    for attempt in range(6):
        st, js = api("GET", "/api/v1/accounts", params={"page": 1, "page_size": 500})
        if st != 200:
            time.sleep(2)
            continue
        d = js.get("data") or {}
        total = d.get("total", 0)
        items = list(d.get("items") or [])
        page = 1
        while total and len(items) < total and page < 40:
            page += 1
            st, js = api("GET", "/api/v1/accounts", params={"page": page, "page_size": 500})
            if st != 200:
                break
            items += (js.get("data") or {}).get("items") or []
        if total and len(items) < total:
            print(f"[warn] 拉到 {len(items)}/{total}，重试 {attempt+1}/6")
            time.sleep(2)
            continue
        return items, total
    raise SystemExit("拉账号列表连续失败")


def parse_ts(v):
    """支持字符串/整数时间戳，返回 epoch 秒或 None"""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("Z", "+00:00")
    try:
        from datetime import datetime
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", type=int, default=0, help="只看某分组 id")
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()

    t = time.time()
    items, total = fetch_accounts()
    print(f"账号总数 {total}，实际拉到 {len(items)}（{(time.time()-t):.1f}s）")

    if args.group:
        sel = [a for a in items if args.group in (a.get("group_ids") or [])]
        print(f"分组 {args.group} 内账号 {len(sel)}")
    else:
        sel = items

    now = time.time()
    buckets = collections.Counter()
    cooling, paused, errored, ok = [], [], [], []
    for a in sel:
        status = (a.get("status") or "").lower()
        sched = a.get("schedulable")
        until = parse_ts(a.get("temp_unschedulable_until"))
        reason = a.get("temp_unschedulable_reason") or ""
        left = (until - now) if until else 0
        if a.get("temp_unschedulable") and left > 0:
            cooling.append((a, reason, left))
            buckets["冷却中(临时不可调度)"] += 1
        elif status != "active":
            errored.append((a, (a.get("error_message") or "")[:80]))
            buckets[f"status={status}"] += 1
        elif sched is False:
            paused.append((a, (a.get("error_message") or "")[:80]))
            buckets["schedulable=false"] += 1
        else:
            ok.append(a)
            buckets["可调度"] += 1

    print("\n== 分类统计 ==")
    for k, v in buckets.most_common():
        print(f"  {k}: {v}")

    if cooling:
        agg = collections.Counter(r for _, r, _ in cooling)
        print("\n== 冷却中按原因聚合 ==")
        for r, c in agg.most_common():
            print(f"  {c} 个 | {r[:100] or '(无原因)'}")
        longest = sorted(cooling, key=lambda x: -x[2])[:8]
        print("== 冷却最久的 8 个 ==")
        for a, r, lf in longest:
            print(f"  {a['id']} {a.get('name')[:36]} 剩 {lf/3600:.1f}h | {r[:60]}")

    if errored:
        print("== status!=active 样例 (最多 10) ==")
        for a, err in errored[:10]:
            print(f"  {a['id']} {a.get('name')[:34]} status={a.get('status')} | {err}")
    if paused:
        print("== schedulable=false 样例 (最多 10) ==")
        for a, err in paused[:10]:
            print(f"  {a['id']} {a.get('name')[:34]} | {err}")

    print("\n== 可调度号的关键参数分布 ==")
    for f in ("priority", "concurrency", "proxy_id"):
        c = collections.Counter(str(a.get(f)) for a in ok)
        print(f"  {f}: {dict(c.most_common(6))}")

    with_proxy = sum(1 for a in ok if a.get("proxy_id"))
    print(f"  可调度且捆绑代理: {with_proxy}/{len(ok)}")

    out = args.json_out or os.path.join(HERE, "runs", f"account_health_group{args.group or 'all'}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump({"total": total, "selected": len(sel), "buckets": dict(buckets),
               "ok_ids": [a["id"] for a in ok],
               "cooling": [{"id": a["id"], "name": a.get("name"), "reason": r, "left_hours": round(lf/3600, 2)}
                           for a, r, lf in cooling],
               "paused": [{"id": a["id"], "name": a.get("name"), "err": e} for a, e in paused],
               "errored": [{"id": a["id"], "name": a.get("name"), "status": a.get("status"), "err": e}
                           for a, e in errored]},
              open(out, "w"), indent=1, ensure_ascii=False)
    print(f"\n明细已存: {out}")


if __name__ == "__main__":
    main()