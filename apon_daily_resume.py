#!/usr/bin/env python3
"""apon_daily_resume.py — 每日自动启用 apon 账号的调度。

背景（2026-10-21 实测）：
  apon 账号是 apinex 上游（https://api.apinex.bond），免费模型需**每日签到**，
  额度用完报 `Payment required (402): Free 10M tokens used...`，
  仓储层 SetError() 会把账号打成 status=error + schedulable=false
  （源码 account_repo.go: SetError → SetStatus(error) + SetSchedulable(false)）。

  单独 PUT {"schedulable": true} **没用** —— 返回 200 但复核仍是 false；
  必须用 **POST /api/v1/accounts/bulk-update**，body 用 `account_ids`（复数）：

    POST /api/v1/accounts/bulk-update
    {"account_ids": [82436, 82437], "schedulable": true}
    → {"success": 2, "failed": 0}，复核 schedulable=true ✅

  同时把 status 修回 active（error 状态本身也会挡调度）：
    PUT /api/v1/accounts/{id} {"status": "active", "schedulable": true}   ← status 生效

定时：schedule_create 每日 01:00 触发（用户指定）。

用法:
  .venv/bin/python apon_daily_resume.py --dry-run     # 只看会启用哪些
  .venv/bin/python apon_daily_resume.py               # 真启用
"""
import argparse
import json
import sys
import time

sys.path.insert(0, "/Users/zxc/tokenharbor-farmer")
from ss2a_client import api

APON_GROUP_ID = 4729


def retry(fn, n=4, delay=2):
    last = None
    for i in range(n):
        try:
            return fn()
        except Exception as e:
            last = e
            time.sleep(delay * (i + 1))
    raise last


def find_apon_accounts():
    """找 apon 账号：名字以 apon 开头，或挂在 apon 分组（4729）。"""
    st, js = retry(lambda: api("GET", "/api/v1/accounts",
                               params={"search": "apon", "page": 1, "page_size": 200}))
    d = js.get("data") or {}
    items = d.get("items") or []
    out = {}
    for a in items:
        name = (a.get("name") or "").lower()
        gids = a.get("group_ids") or []
        if name.startswith("apon") or APON_GROUP_ID in gids:
            out[a["id"]] = a
    # 再按分组搜一遍，防名字不带 apon
    st2, js2 = retry(lambda: api("GET", "/api/v1/accounts",
                                 params={"group_id": APON_GROUP_ID,
                                         "page": 1, "page_size": 200}))
    for a in ((js2.get("data") or {}).get("items") or []):
        out.setdefault(a["id"], a)
    return list(out.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--include-active", action="store_true",
                    help="已启用的也重发一遍（默认只处理 disabled/error 的）")
    A = ap.parse_args()

    accs = find_apon_accounts()
    print(f"apon 账号 {len(accs)} 个")

    need = [a for a in accs
            if a.get("schedulable") is not True or a.get("status") != "active"]
    if A.include_active:
        need = accs
    print(f"需要启用调度: {len(need)} 个"
          f"（已正常 {len(accs)-len(need)} 个）")
    for a in need:
        print(f"  {a.get('name'):10} schedulable={a.get('schedulable')} "
              f"status={a.get('status')} err={str(a.get('error_message'))[:50]}")

    if not need:
        print("全部正常，无需操作")
        return
    if A.dry_run:
        print("干跑结束")
        return

    ids = [a["id"] for a in need]
    st, js = retry(lambda: api("POST", "/api/v1/accounts/bulk-update",
                               body={"account_ids": ids, "schedulable": True}))
    d = (js.get("data") or {}) if isinstance(js, dict) else {}
    print(f"\nbulk-update(schedulable=true): HTTP {st} | "
          f"success={d.get('success')} failed={d.get('failed')}")

    # status 修回 active（error 状态挡调度）
    fixed = 0
    for a in need:
        if a.get("status") != "active":
            try:
                st2, _ = retry(lambda: api("PUT", f"/api/v1/accounts/{a['id']}",
                                           body={"status": "active"}))
                fixed += 1 if st2 == 200 else 0
            except Exception:
                pass
    print(f"status→active: {fixed} 个")

    # 复核
    time.sleep(2)
    accs2 = find_apon_accounts()
    ok = sum(1 for a in accs2 if a.get("schedulable") is True and a.get("status") == "active")
    bad = [(a.get("name"), a.get("schedulable"), a.get("status"))
           for a in accs2 if not (a.get("schedulable") is True and a.get("status") == "active")]
    print(f"\n复核: {ok}/{len(accs2)} 已启用且 active")
    for b in bad:
        print(f"  ⚠ {b}")


if __name__ == "__main__":
    main()
