#!/usr/bin/env python3
"""import_proxyscrape_v2.py — 高速导入 proxyscrape 代理到 sub2api（并发 + 连接复用 + 全程日志）

相比 v1 的三处提速和一处修复：
  1. 文件 sha256 去重：同一批文件重复下载（xxx.txt / xxx (1).txt）只读一份 ← 修复重复导入根因
  2. keep-alive 连接池（ss2a_fast）：单条 856ms → 314ms
  3. 并发创建 + 并发测活（默认 16 线程，实测 32 线程零 429）
  4. 每次创建后立刻把 host:port 加入去重集合，绝不再重复建

用法：
  .venv/bin/python import_proxyscrape_v2.py --dry-run                      # 只看计划
  .venv/bin/python import_proxyscrape_v2.py                                # 真导
  .venv/bin/python import_proxyscrape_v2.py --only-test --workers 16       # 只跑全量测活
  .venv/bin/python import_proxyscrape_v2.py --fake 20                      # 20 条假代理压测，跑完自动删
  .venv/bin/python import_proxyscrape_v2.py --files 'x*.txt' --prefix ps --workers 32
"""
import argparse
import glob
import hashlib
import json
import os
import re
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ss2a_fast import api, pmap, fetch_all_proxies  # noqa: E402

LINE_RE = re.compile(r"^(?P<user>[^:@\s]+):(?P<pass>[^@\s]+)@(?P<host>[0-9.]+):(?P<port>\d+)$")

_log_lock = threading.Lock()
_log_fh = None


def log(msg, who="main", file_only=False):
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} [{who}] {msg}"
    with _log_lock:
        if not file_only:
            print(line, flush=True)
        if _log_fh:
            _log_fh.write(line + "\n")
            _log_fh.flush()


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_rows(files):
    """读文件 + sha256 去重 + 行去重，返回 (rows, 统计)"""
    seen_hash, seen_host = {}, set()
    rows, stat = [], {"files": 0, "dup_files": 0, "dup_lines": 0, "bad_lines": 0}
    for f in files:
        digest = sha256(f)
        if digest in seen_hash:
            stat["dup_files"] += 1
            log(f"文件内容与 {os.path.basename(seen_hash[digest])} 完全相同，跳过: {os.path.basename(f)}")
            continue
        seen_hash[digest] = f
        stat["files"] += 1
        with open(f, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                m = LINE_RE.match(line)
                if not m:
                    stat["bad_lines"] += 1
                    continue
                row = m.groupdict()
                key = (row["host"], int(row["port"]))
                if key in seen_host:
                    stat["dup_lines"] += 1
                    continue
                seen_host.add(key)
                rows.append(row)
    return rows, stat


def create_one(args_tuple):
    idx, row, prefix = args_tuple
    name = f"{prefix}-{idx:03d}"
    body = {"name": name, "protocol": "http", "host": row["host"],
            "port": int(row["port"]), "username": row["user"], "password": row["pass"]}
    t = time.time()
    st, js = api("POST", "/api/v1/proxies", body=body)
    ms = (time.time() - t) * 1000
    pid = (js.get("data") or {}).get("id")
    ok = st in (200, 201)
    if not ok:
        msg = str(js.get("message", js))[:120]
        if "exist" in msg.lower() or "duplicate" in msg.lower():
            log(f"跳过已存在 {name} → {msg}", who="w")
            return ("skip", name, None, ms)
        log(f"失败 {name} HTTP {st} {msg}", who="w")
        return ("fail", name, None, ms)
    log(f"created {name} id={pid} {ms:.0f}ms", who="w", file_only=True)
    return ("ok", name, pid, ms)


def test_one(args_tuple):
    name, pid = args_tuple
    t = time.time()
    st, js = api("POST", f"/api/v1/proxies/{pid}/test")
    ms = (time.time() - t) * 1000
    d = (js.get("data") or {})
    if st == 200 and d.get("success"):
        log(f"test ok {name} latency={d.get('latency_ms')}ms cc={d.get('country_code')} client={ms:.0f}ms",
            who="t", file_only=True)
        return ("ok", name, d.get("latency_ms"), d.get("country_code"), ms)
    log(f"test fail {name} msg={str(d.get('message'))[:90]} client={ms:.0f}ms", who="t")
    return ("fail", name, None, None, ms)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", default="/Users/zxc/Downloads/proxyscrape_premium_http_proxies*.txt")
    ap.add_argument("--prefix", default="proxyscrape")
    ap.add_argument("--workers", type=int, default=32, help="创建/改名/删除并发（纯写库，实测 32 线程零 429）")
    ap.add_argument("--test-workers", type=int, default=8,
                    help="测活并发（服务端要真拨号探测，>12 会因探测队列拥塞误判失败）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only-test", action="store_true", help="跳过创建，只对现有同名代理全量测活")
    ap.add_argument("--fake", type=int, default=0, help="生成 N 条假代理做压测（跑完自动删除）")
    ap.add_argument("--skip-test", action="store_true", help="导入完不测活")
    ap.add_argument("--log", default="")
    args = ap.parse_args()

    global _log_fh
    log_path = args.log or os.path.join(HERE, "runs", f"import_proxyscrape_{time.strftime('%Y%m%d_%H%M%S')}.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    _log_fh = open(log_path, "w", encoding="utf-8")
    log(f"日志文件: {log_path}")
    t_all = time.time()

    # 已有代理快照
    t = time.time()
    existing = fetch_all_proxies()
    log(f"拉取面板现有代理 {len(existing)} 个（{(time.time()-t)*1000:.0f}ms）")
    exist_names = {p.get("name") for p in existing}
    exist_hosts = {(p.get("host"), p.get("port")) for p in existing}

    if args.only_test:
        cand = [p for p in existing if (p.get("name") or "").startswith(args.prefix + "-")]
        log(f"测活目标 {len(cand)} 个，线程 {args.test_workers}")
        t = time.time()
        res = pmap(test_one, [(p["name"], p["id"]) for p in cand], workers=args.test_workers)
        # 失败项单线程复测一次：并发下服务端探测队列会误判，复测才准
        retry = [(r[1], dict((p["name"], p["id"]) for p in cand)[r[1]]) for r in res if r[0] == "fail"]
        if retry:
            log(f"复测失败项 {len(retry)} 个（单线程）")
            fixed = pmap(test_one, retry, workers=1)
            fixed_map = {r[1]: r for r in fixed}
            res = [fixed_map.get(r[1], r) if r[0] == "fail" else r for r in res]
        okc = [r for r in res if r[0] == "ok"]
        okc.sort(key=lambda r: r[2] or 9999)
        log(f"测活完成 {len(cand)} 条 {(time.time()-t):.1f}s：通 {len(okc)} / 排 {len(res)}")
        if okc:
            log(f"延迟 min {okc[0][2]}ms / 中位 {okc[len(okc)//2][2]}ms / max {okc[-1][2]}ms")
        json.dump([{"name": r[1], "latency_ms": r[2], "country": r[3]} for r in res],
                  open(os.path.join(HERE, "runs", "proxyscrape_test_report.json"), "w"),
                  indent=1, ensure_ascii=False)
        log(f"总耗时 {time.time()-t_all:.1f}s")
        return

    # 解析文件
    if args.fake:
        rows = [{"user": "perftest", "pass": "x", "host": f"10.255.{i % 250}.{i}", "port": 99}
                for i in range(1, args.fake + 1)]
        stat = {"files": 0, "dup_files": 0, "dup_lines": 0, "bad_lines": 0}
        log(f"压测模式：生成 {len(rows)} 条假代理")
    else:
        files = sorted(glob.glob(args.files))
        if not files:
            raise SystemExit(f"没匹配到文件: {args.files}")
        t = time.time()
        rows, stat = load_rows(files)
        log(f"解析 {len(files)} 个文件 → 去重后 {len(rows)} 条（{stat['dup_files']} 个文件重复, "
            f"{stat['dup_lines']} 条行重复, {stat['bad_lines']} 条格式错；{(time.time()-t)*1000:.0f}ms）")

    # 幂等过滤
    todo = []
    for i, row in enumerate(rows, 1):
        name = f"{args.prefix}-{i:03d}"
        if name in exist_names or (row["host"], int(row["port"])) in exist_hosts:
            continue
        todo.append((i, row, args.prefix))
    log(f"待创建 {len(todo)} 条（已存在跳过 {len(rows)-len(todo)} 条）")
    if args.dry_run:
        for i, row, _p in todo[:10]:
            log(f"  将创建 {args.prefix}-{i:03d} ← {row['host']}:{row['port']}")
        if len(todo) > 10:
            log(f"  ... 其余 {len(todo)-10} 条略")
        log("dry-run 结束，未做任何修改")
        return

    # 并发创建
    created, skipped, failed = [], [], []
    if todo:
        counter = {"n": 0}

        def prog(done, total):
            if done % 20 == 0 or done == total:
                log(f"创建进度 {done}/{total} ok={len(created)}")

        t = time.time()
        res = pmap(create_one, todo, workers=args.workers, progress=prog)
        dt = time.time() - t
        for r in res:
            if r[0] == "ok":
                created.append(r[2])
                idx = int(r[1].rsplit("-", 1)[1])
                row = rows[idx - 1]
                exist_hosts.add((row["host"], int(row["port"])))
            elif r[0] == "skip":
                skipped.append(r[1])
            else:
                failed.append(r[1])
        log(f"创建阶段完成：新建 {len(created)} / 跳过 {len(skipped)} / 失败 {len(failed)}，"
            f"耗时 {dt:.1f}s（{dt/max(len(todo),1)*1000:.0f}ms/条，{args.workers} 线程）")

    # 并发测活（全新拉名单，覆盖本次新建 + 历史存在的）
    if not args.skip_test:
        cur = fetch_all_proxies()
        cand = [p for p in cur if (p.get("name") or "").startswith(args.prefix + "-")]
        log(f"测活 {len(cand)} 条，线程 {args.test_workers}")
        t = time.time()
        pair = [(p["name"], p["id"]) for p in cand]
        res = pmap(test_one, pair, workers=args.test_workers)
        retry = [(r[1], dict(pair)[r[1]]) for r in res if r[0] == "fail"]
        if retry:
            log(f"复测失败项 {len(retry)} 个（单线程）")
            fixed_map = {r[1]: r for r in pmap(test_one, retry, workers=1)}
            res = [fixed_map.get(r[1], r) if r[0] == "fail" else r for r in res]
        dt = time.time() - t
        okc = sorted([r for r in res if r[0] == "ok"], key=lambda r: r[2] or 9999)
        log(f"测活完成 {(time.time()-t):.1f}s：通 {len(okc)} / 共 {len(res)}")
        if okc:
            log(f"延迟 min {okc[0][2]}ms / 中位 {okc[len(okc)//2][2]}ms / max {okc[-1][2]}ms")
        json.dump([{"name": r[1], "latency_ms": r[2], "country": r[3]} for r in res],
                  open(os.path.join(HERE, "runs", "proxyscrape_test_report.json"), "w"),
                  indent=1, ensure_ascii=False)

    # 压测模式收尾：删掉假代理
    if args.fake and created:
        log(f"压测收尾：删除 {len(created)} 条假代理")
        t = time.time()
        pmap(lambda pid: api("DELETE", f"/api/v1/proxies/{pid}"), created, workers=args.workers)
        log(f"清理完成 {(time.time()-t):.1f}s")

    log(f"全部完成，总耗时 {time.time()-t_all:.1f}s | 日志: {log_path}")


if __name__ == "__main__":
    main()