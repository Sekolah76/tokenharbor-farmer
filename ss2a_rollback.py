#!/usr/bin/env python3
"""ss2a_rollback.py — 用快照把账号字段回退。

配合 ss2a_snapshot.py 用：改之前拍照，改坏了回退。

⚠️ api_key 的处理：
  快照里**不含 api_key 明文**（读取接口不回显）。回退时默认走「省略 api_key」模式，
  实测 PUT 不传 api_key 时系统会保留原值。若快照时期 key 就是空的，
  用 --with-local-keys 从本地 th_keys_*.txt / runs/import_*.json 找回明文补上。

用法:
  # 看快照里有什么
  .venv/bin/python ss2a_rollback.py --snapshot runs/snapshots/xxx.json --dry-run
  # 回退全部字段
  .venv/bin/python ss2a_rollback.py --snapshot runs/snapshots/xxx.json
  # 只回退指定字段（比如只修 proxy_id）
  .venv/bin/python ss2a_rollback.py --snapshot xxx.json --fields proxy_id group_ids
"""
import argparse
import glob
import json
import sys
import time

sys.path.insert(0, "/Users/zxc/tokenharbor-farmer")
from ss2a_client import api


def retry(fn, n=4, delay=1.5):
    last = None
    for i in range(n):
        try:
            return fn()
        except Exception as e:
            last = e
            time.sleep(delay * (i + 1))
    raise last


def build_keymap():
    km = {}
    for path in glob.glob("th_keys_*.txt"):
        try:
            for line in open(path, encoding="utf-8"):
                p = line.rstrip("\n").split("\t")
                if len(p) >= 3 and p[2].startswith("thk_"):
                    km[p[0]] = p[2]
        except Exception:
            pass
    for path in glob.glob("runs/import_*.json"):
        try:
            doc = json.load(open(path, encoding="utf-8"))
            for a in (doc.get("data") or {}).get("accounts") or []:
                em = (a.get("extra") or {}).get("email")
                key = (a.get("credentials") or {}).get("api_key")
                if em and key and key.startswith("thk_"):
                    km.setdefault(em, key)
        except Exception:
            pass
    return km


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--fields", nargs="+", default=None,
                    help="只回退这些字段（默认：credentials + 代理/分组/并发/优先级）")
    ap.add_argument("--with-local-keys", action="store_true",
                    help="从本地文件补 api_key 明文（快照里没有）")
    ap.add_argument("--dry-run", action="store_true")
    A = ap.parse_args()

    doc = json.load(open(A.snapshot, encoding="utf-8"))
    snap = doc.get("accounts") or []
    print(f"快照 {A.snapshot}：{len(snap)} 个账号（{doc.get('created_at')}，"
          f"label={doc.get('label')}）")

    # 当前状态
    st, js = api("GET", "/api/v1/accounts",
                 params={"search": doc.get("prefix", "tokenhour"), "page": 1, "page_size": 900})
    cur = {(a.get("name") or ""): a for a in ((js.get("data") or {}).get("items") or [])}
    print(f"当前线上 {len(cur)} 个（同前缀）")

    km = build_keymap() if A.with_local_keys else {}

    fields = A.fields or ["credentials", "proxy_id", "group_ids",
                          "concurrency", "priority", "rate_multiplier",
                          "auto_pause_on_expired", "notes"]
    print(f"将回退字段: {fields}")

    todo = []
    for s in snap:
        live = cur.get(s.get("name"))
        if not live:
            continue
        body = {}
        for f in fields:
            if f == "credentials":
                c = dict(s.get("credentials") or {})
                em = (s.get("extra") or {}).get("email") or \
                     (s.get("name") or "").split("|")[-1]
                key = km.get(em)
                if key:
                    c["api_key"] = key
                # 没明文就省略 api_key —— 系统保留原值（实测）
                body["credentials"] = c
            elif f in s:
                body[f] = s[f]
        if body:
            todo.append((live["id"], s.get("name"), body))

    print(f"待回退 {len(todo)} 个")
    if A.dry_run:
        for i, (aid, nm, body) in enumerate(todo[:3]):
            print(f"  [{i+1}] {nm} → {json.dumps(body, ensure_ascii=False)[:200]}")
        print("干跑结束")
        return

    ok = fail = 0
    t0 = time.time()
    for i, (aid, nm, body) in enumerate(todo, 1):
        try:
            st2, _ = retry(lambda: api("PUT", f"/api/v1/accounts/{aid}", body=body))
            if st2 == 200:
                ok += 1
            else:
                fail += 1
                print(f"  ✗ {nm}: HTTP {st2}")
        except Exception as e:
            fail += 1
            print(f"  ✗ {nm}: {str(e)[:70]}")
        if i % 40 == 0:
            print(f"  ... {i}/{len(todo)} 成功 {ok} 失败 {fail} ({time.time()-t0:.0f}s)",
                  flush=True)

    print(f"\n回退: 成功 {ok} | 失败 {fail} | {time.time()-t0:.0f}s")

    # 复核
    time.sleep(2)
    st3, js3 = api("GET", "/api/v1/accounts",
                   params={"search": doc.get("prefix", "tokenhour"),
                           "page": 1, "page_size": 900})
    fin = {(a.get("name") or ""): a for a in ((js3.get("data") or {}).get("items") or [])}
    diff = 0
    for s in snap:
        live = fin.get(s.get("name"))
        if not live:
            continue
        for f in ("proxy_id", "group_ids", "concurrency", "priority"):
            if f in s and s[f] != live.get(f):
                diff += 1
                break
    print(f"复核: 字段与快照不一致的账号 {diff} 个")
    no_key = sum(1 for a in fin.values()
                 if not (a.get("credentials_status") or {}).get("has_api_key"))
    print(f"缺 key 的账号: {no_key} 个")


if __name__ == "__main__":
    main()
