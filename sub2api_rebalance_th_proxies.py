#!/usr/bin/env python3
"""sub2api_rebalance_th_proxies.py — 给 tokenhour-* 账号重摊代理（速度优先 + 负载平均）

策略
  1) 目标池：US 节点 / active / 延迟检测成功 / 质量非 D、F / 14 天内不过期
  2) 排序：用「实测中位延迟」(probe_rounds.json，轮次交错采样) 而不是平台缓存 latency_ms
  3) 分配：按 1/实测延迟 加权 + 每节点上限 cap 的最大余额法（largest remainder）——
     快节点多分一点，但谁都不会超过 cap，保证摊平
  4) 应用：PUT /api/v1/accounts/{id} {"proxy_id": N}，幂等（已在目标节点则跳过），
     留 rollback map，可一键回滚

用法
  python3 sub2api_rebalance_th_proxies.py                    # dry-run，打印方案
  python3 sub2api_rebalance_th_proxies.py --cap 6 --apply    # 真改
  python3 sub2api_rebalance_th_proxies.py --rollback runs/xxx.json
"""
import argparse, datetime, json, os, statistics, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ss2a_client import api

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "runs")
SEARCH = "tokenhour-"


def load_pool(rounds_path):
    rows = json.load(open(rounds_path, encoding="utf-8"))
    rows.sort(key=lambda r: (r["median"], r["proxy_id"]))
    return rows


def fetch_accounts():
    out, page = [], 1
    while True:
        st, js = api("GET", "/api/v1/accounts", params={"search": SEARCH, "page": page, "page_size": 200})
        d = js.get("data") or {}
        items = d.get("items") or []
        out += items
        if not items or len(out) >= (d.get("total") or 0):
            return out
        page += 1


def apportion(pool, total, cap):
    """按 1/延迟 加权分配 total 个名额，硬上限 cap。最大余额法。"""
    w = {r["proxy_id"]: 1.0 / r["median"] for r in pool}
    s = sum(w.values())
    quota = {pid: total * wi / s for pid, wi in w.items()}
    alloc = {pid: min(cap, int(q)) for pid, q in quota.items()}
    left = total - sum(alloc.values())
    # 余额从大到小补，仍然受 cap 限制
    order = sorted(quota, key=lambda pid: (quota[pid] - int(quota[pid])), reverse=True)
    i = 0
    while left > 0:
        pid = order[i % len(order)]
        if alloc[pid] < cap:
            alloc[pid] += 1
            left -= 1
        i += 1
        if i > len(order) * (cap + 2):
            raise SystemExit("容量不足：池子太小或 cap 太低")
    return alloc


def build_plan(pool, accounts, cap):
    alloc = apportion(pool, len(accounts), cap)
    slots = []
    for r in pool:                      # 快的排在前面 → 号按顺序领，快节点先被占满
        slots += [r["proxy_id"]] * alloc[r["proxy_id"]]
    accounts = sorted(accounts, key=lambda a: a["id"])
    plan = []
    for a, pid in zip(accounts, slots):
        plan.append({"account_id": a["id"], "name": a["name"],
                     "old_proxy_id": a.get("proxy_id"), "new_proxy_id": pid})
    return plan, alloc


def apply_plan(plan, workers=6, retries=4, label="改代理", log_path=None):
    """幂等应用；返回 (统计, 用时秒)"""
    import threading, queue
    from progress import Progress
    q = queue.Queue()
    skipped = 0
    for item in plan:
        if item["old_proxy_id"] == item["new_proxy_id"]:
            item["action"] = "skip"
            skipped += 1
        else:
            item["action"] = "pending"
            q.put(item)
    total = q.qsize()
    done = {"changed": 0, "failed": 0, "skipped": skipped}
    if total == 0:
        print(f"{label}: 无需改动（{skipped} 个已在目标节点）", flush=True)
        return done, 0.0
    prog = Progress(total, label, every_pct=5.0, log_path=log_path)
    lock = threading.Lock()

    def work():
        while True:
            try:
                it = q.get_nowait()
            except queue.Empty:
                return
            ok = False
            for attempt in range(retries):
                try:
                    st, js = api("PUT", f"/api/v1/accounts/{it['account_id']}",
                                 body={"proxy_id": it["new_proxy_id"]}, timeout=45)
                    if st == 200:
                        it["action"] = "changed"
                        with lock: done["changed"] += 1
                        ok = True
                        break
                    it["error"] = f"HTTP {st} {str(js)[:80]}"
                except Exception as exc:                                  # SSL 抖动
                    it["error"] = str(exc)[:80]
                time.sleep(1.5 * (attempt + 1))
            if not ok:
                it["action"] = "failed"
                with lock: done["failed"] += 1
            prog.tick(ok, account_id=it["account_id"], proxy_id=it["new_proxy_id"])
            q.task_done()
            time.sleep(0.15)

    threads = [threading.Thread(target=work, daemon=True) for _ in range(workers)]
    t0 = time.time()
    for t in threads: t.start()
    for t in threads: t.join()
    secs = round(time.time() - t0, 1)
    prog.done_(changed=done["changed"], failed=done["failed"])
    return done, secs


def verify(plan, label="复核"):
    """复核：新分配是否落地，credentials 是否没被清空"""
    from progress import Progress
    prog = Progress(len(plan), label, every_pct=10.0)
    ok = bad = 0
    lost = []
    for it in plan:
        st, js = api("GET", f"/api/v1/accounts/{it['account_id']}")
        a = js.get("data") or {}
        if a.get("proxy_id") != it["new_proxy_id"]:
            bad += 1
            prog.tick(False, account_id=it["account_id"])
            continue
        ok += 1
        c = a.get("credentials") or {}
        if not (c.get("base_url") and c.get("model_mapping")):
            lost.append(it["account_id"])
        prog.tick(True, account_id=it["account_id"])
    prog.done_(ok=ok, bad=bad, lost=len(lost))
    return ok, bad, lost


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default="/tmp/probe_rounds.json")
    ap.add_argument("--cap", type=int, default=6)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--pin", metavar="JSON", help="对照组：文件里 control 数组的账号保持原节点不动")
    ap.add_argument("--verify", metavar="RUN_JSON", help="只复核某次方案的落地情况（带进度）")
    ap.add_argument("--rollback", metavar="RUN_JSON")
    args = ap.parse_args()

    if args.verify:
        run = json.load(open(args.verify, encoding="utf-8"))
        plan = run["plan"]
        print(f"复核 {args.verify}：{len(plan)} 个账号，开始逐条 GET …", flush=True)
        ok, bad, lost = verify(plan, label="复核")
        print(f"复核结果: 命中 {ok} | 未落地 {bad} | credentials 缺失 {len(lost)} {lost[:8]}")
        return

    if args.rollback:
        run = json.load(open(args.rollback, encoding="utf-8"))
        plan = [{"account_id": p["account_id"], "name": p["name"],
                 "old_proxy_id": p["new_proxy_id"], "new_proxy_id": p["old_proxy_id"],
                 "action": "pending"} for p in run["plan"]]
        print(f"回滚 {len(plan)} 个账号 → 原分配")
        done, secs = apply_plan(plan, args.workers, label="回滚",
                                log_path=os.path.join(RUNS, "rebalance.progress.jsonl"))
        print(f"回滚完成: {done} 用时 {secs}s")
        return

    pool = load_pool(args.pool)
    accounts = fetch_accounts()
    pinned = set()
    if args.pin:
        pinned = {int(x) for x in json.load(open(args.pin, encoding="utf-8")).get("control", [])}
        accounts = [a for a in accounts if a["id"] not in pinned]
        print(f"对照组固定不动 {len(pinned)} 个号")
    plan, alloc = build_plan(pool, accounts, args.cap)
    print(f"账号 {len(accounts)} 个 | 目标池 {len(pool)} 个节点 | cap {args.cap}")
    print(f"每节点分配: {min(alloc.values())}~{max(alloc.values())} 个")
    lat = {r['proxy_id']: r['median'] for r in pool}
    w = sum(lat[p['new_proxy_id']] for p in plan) / len(plan)
    old_lat = [lat[p['old_proxy_id']] for p in plan if p['old_proxy_id'] in lat]
    print(f"预计机队加权延迟: {w:.0f}ms（现状同口径 {statistics.median(old_lat):.0f}ms/中位）")
    movers = sum(1 for p in plan if p["old_proxy_id"] != p["new_proxy_id"])
    print(f"需要改动的账号: {movers} / {len(plan)}")

    if not args.apply:
        print("\n[dry-run] 前 15 条：")
        for p in plan[:15]:
            print(f"  {p['account_id']}  {p['name'][:34]:36s} {p['old_proxy_id']} → {p['new_proxy_id']}")
        print("\n每节点落点：")
        for r in pool:
            print(f"  {r['proxy_id']:>6} 实测{r['median']:>5}ms  分配 {alloc[r['proxy_id']]:>2} 个  {r['name']}")
        return

    os.makedirs(RUNS, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(RUNS, f"rebalance_th_proxy_{ts}.json")
    json.dump({"ts": ts, "cap": args.cap, "pool_size": len(pool),
               "alloc": alloc, "plan": plan}, open(path, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"方案已存 {path}")
    print(f"进度可实时看：tail -f {os.path.join(RUNS, 'rebalance.progress.jsonl')}", flush=True)
    done, secs = apply_plan(plan, args.workers, label="改代理",
                            log_path=os.path.join(RUNS, "rebalance.progress.jsonl"))
    json.dump({"ts": ts, "cap": args.cap, "alloc": alloc, "plan": plan, "result": done},
              open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"应用完成: {done} 用时 {secs}s")
    ok, bad, lost = verify(plan, label="复核")
    print(f"复核: 命中 {ok} | 未落地 {bad} | credentials 缺失 {len(lost)} {lost[:8]}")
    print(f"回滚命令: python3 {os.path.basename(__file__)} --rollback {path}")


if __name__ == "__main__":
    main()
