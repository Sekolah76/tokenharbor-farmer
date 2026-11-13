#!/usr/bin/env python3
"""import_proxyscrape.py — 把 proxyscrape 高匿 HTTP 代理批量导入 sub2api（幂等）

- 读 /Users/zxc/Downloads/proxyscrape_premium_http_proxies*.txt，行格式 user:pass@host:port
- POST /api/v1/proxies（用户端实测可用，owner_user_id=423）
- 幂等：按名字查已存在的跳过；每条 4 次重试 + 1~2s 退避
- 结束后拉列表复核
"""
import json
import re
import sys
import time
import glob as globmod

sys.path.insert(0, "/Users/zxc/tokenharbor-farmer")
from ss2a_client import api

PREFIX = "proxyscrape"
FILES = sorted(globmod.glob("/Users/zxc/Downloads/proxyscrape_premium_http_proxies*.txt"))

LINE_RE = re.compile(r"^(?P<user>[^:@\s]+):(?P<pass>[^@\s]+)@(?P<host>[0-9.]+):(?P<port>\d+)$")


def load_lines():
    rows = []
    for f in FILES:
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                m = LINE_RE.match(line)
                if not m:
                    print(f"[skip] 不认识行格式: {line[:60]}")
                    continue
                rows.append(m.groupdict())
    return rows


def fetch_existing_names():
    """拉全量（校验条数==total），返回已有 proxyscrape-* 名字集合与 host:port 集合"""
    for attempt in range(6):
        st, js = api("GET", "/api/v1/proxies", params={"page": 1, "page_size": 500})
        if st != 200:
            time.sleep(2)
            continue
        data = js.get("data", {})
        items, total = data.get("items", []), data.get("total", 0)
        # 翻页拿全
        page = 1
        all_items = list(items)
        while len(all_items) < total and page < 20:
            st, js = api("GET", "/api/v1/proxies", params={"page": page + 1, "page_size": 500})
            if st != 200:
                break
            all_items += js.get("data", {}).get("items", [])
            page += 1
        if total and len(all_items) < total:
            print(f"[warn] 拉到 {len(all_items)}/{total}，重试 {attempt+1}/6")
            time.sleep(2)
            continue
        names = {i["name"] for i in all_items if i.get("name", "").startswith(PREFIX)}
        hosts = {(i.get("host"), i.get("port")) for i in all_items}
        return names, hosts
    raise RuntimeError("拉代理列表连续失败")


def create_one(idx, row, retry=4):
    body = {
        "name": f"{PREFIX}-{idx:03d}",
        "protocol": "http",
        "host": row["host"],
        "port": int(row["port"]),
        "username": row["user"],
        "password": row["pass"],
    }
    for a in range(retry):
        st, js = api("POST", "/api/v1/proxies", body=body)
        if st in (200, 201):
            return True, js.get("data", {}).get("id")
        msg = str(js.get("message", ""))
        if "exist" in msg.lower() or "duplicate" in msg.lower():
            return True, None  # 视为已存在
        time.sleep(1 + a)
    return False, None


def main():
    rows = load_lines()
    print(f"共解析 {len(rows)} 条代理（来自 {len(FILES)} 个文件）")
    names, hosts = fetch_existing_names()
    print(f"面板现有 {PREFIX}-* 代理 {len(names)} 个")

    created, skipped, failed = 0, 0, 0
    for i, row in enumerate(rows, 1):
        name = f"{PREFIX}-{i:03d}"
        if name in names or (row["host"], int(row["port"])) in hosts:
            skipped += 1
            continue
        ok, pid = create_one(i, row)
        if ok:
            created += 1
        else:
            failed += 1
            print(f"[fail] {name} {row['host']}:{row['port']}")
        if i % 20 == 0:
            print(f"进度 {i}/{len(rows)}  created={created} skipped={skipped} failed={failed}")
        time.sleep(0.3)

    print(f"\n导入完成: created={created} skipped={skipped} failed={failed}")

    # 复核
    n2, h2 = fetch_existing_names()
    print(f"复核: 面板现有 {PREFIX}-* 代理 {len(n2)} 个")
    missing = [f"{PREFIX}-{i:03d}" for i in range(1, len(rows) + 1) if f"{PREFIX}-{i:03d}" not in n2]
    if missing:
        print(f"缺失: {missing}")
    else:
        print("复核通过：序号连续无缺失")
    json.dump({"total": len(rows), "created": created, "skipped": skipped, "failed": failed},
              open("runs/import_proxyscrape_result.json", "w"), indent=2)


if __name__ == "__main__":
    main()
