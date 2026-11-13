#!/usr/bin/env python3
"""sub2api_assign_top50.py — 把「延迟最低的前 N 个代理」依次（轮转）分配到 tokenhour 账号

规则
  · 范围：base_url 含 tokenharbor.ai 的账号（tokenhour 组）
  · 代理池：读 runs/top50_ids.json（按面板 latency_ms 升序的前 N 个，含港澳排除）
  · 分配：账号按 id 升序，第 i 个号 → 池里第 (i mod N) 个代理，循环轮转
  · 结果：每代理 ⌊M/N⌋ 或 ⌈M/N⌉ 个号，天然均匀

用法
  python3 -u sub2api_assign_top50.py --pool runs/top50_ids.json            # dry-run
  python3 -u sub2api_assign_top50.py --pool runs/top50_ids.json --apply    # 落地
  python3 -u sub2api_assign_top50.py --pool runs/top50_ids.json --apply --pin runs/ab_top50.json
"""
import argparse, datetime, json, os, queue, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ss2a_client import api
from progress import Progress

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "runs")


def fetch_targets(search="tokenhour-"):
    out, page = [], 1
    while True:
        st, js = api("GET", "/api/v1/accounts", params={"search": search, "page": page, "page_size": 200})
        d = js.get("data") or {}
        items = d.get("items") or []
        out += items
        if not items or len(out) >= (d.get("total") or 0):
            break
        page += 1
    return [a for a in out if "tokenharbor.ai" in ((a.get("credentials") or {}).get("base_url") or "")]


def apply_plan(plan, workers=6, retries=4):
    q = queue.Queue()
    for it in plan:
        if it["old_proxy_id"] == it["new_proxy_id"]:
            it["action"] = "skip"
        else:
            it["action"] = "pending"
            q.put(it)
    total = q.qsize()
    if not total:
        print("无需改动：全部已在目标节点")
        return {"changed": 0, "failed": 0, "skipped": len(plan)}
    prog = Progress(total, "轮转分配", every_pct=5.0, log_path=os.path.join(RUNS, "assign_top50.progress.jsonl"))
    done = {"changed": 0, "failed": 0}
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
                except Exception as exc:
                    it["error"] = str(exc)[:80]
                time.sleep(1.5 * (attempt + 1))
            if not ok:
                it["action"] = "failed"
                with lock: done["failed"] += 1
            prog.tick(ok, account_id=it["account_id"], proxy_id=it["new_proxy_id"])
            time.sleep(0.12)

    ts = [threading.Thread(target=work, daemon=True) for _ in range(workers)]
    t0 = time.time()
    for t in ts: t.start()
    for t in ts: t.join()
    prog.done_()
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default="runs/top50_ids.json")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--pin", help="对照组：这些账号不动")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    pool = json.load(open(args.pool, encoding="utf-8"))
    accounts = fetch_targets()
    pinned = set()
    if args.pin:
        pinned = {int(x) for x in json.load(open(args.pin, encoding="utf-8")).get("control", [])}
    accounts = sorted([a for a in accounts if a["id"] not in pinned], key=lambda a: a["id"])
    N = len(pool)
    print(f"账号 {len(accounts)} 个（base_url 含 tokenharbor.ai）| 代理池 {N} 个 | 对照组 {len(pinned)} 个不动")
    print(f"轮转规则：第 i 个号 → 池中第 i mod {N} 个；预计每代理 {len(accounts)//N}~{len(accounts)//N + 1} 个")

    plan = []
    for i, a in enumerate(accounts):
        pid = pool[i % N]
        plan.append({"account_id": a["id"], "name": a["name"],
                     "old_proxy_id": a.get("proxy_id"), "new_proxy_id": pid})
    dist = {}
    for it in plan:
        dist[it["new_proxy_id"]] = dist.get(it["new_proxy_id"], 0) + 1
    print(f"落点：{min(dist.values())}~{max(dist.values())} 个/代理，用到 {len(dist)} 个代理")
    movers = sum(1 for it in plan if it["old_proxy_id"] != it["new_proxy_id"])
    print(f"需要改动 {movers} / {len(plan)}")
    print("\n前 12 条：")
    for it in plan[:12]:
        print(f"   {it['account_id']} {it['name'][:26]:28s} {it['old_proxy_id']} → {it['new_proxy_id']}")

    if not args.apply:
        print("\n[dry-run] 加 --apply 落地")
        return

    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(RUNS, f"assign_top50_{ts}.json")
    json.dump({"ts": ts, "pool": pool, "plan": plan, "dist": dist},
              open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"方案已存 {path}\n进度：tail -f {os.path.join(RUNS, 'assign_top50.progress.jsonl')}", flush=True)
    done = apply_plan(plan, args.workers)
    json.dump({"ts": ts, "pool": pool, "plan": plan, "dist": dist, "result": done},
              open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"应用完成: {done}")

    prog = Progress(len(plan), "复核", every_pct=10.0)
    bad, lost = [], []
    for it in plan:
        st, js = api("GET", f"/api/v1/accounts/{it['account_id']}")
        a = js.get("data") or {}
        if (a.get("proxy_id") or None) != it["new_proxy_id"]:
            bad.append(it["account_id"])
        c = a.get("credentials") or {}
        if not c.get("base_url"):
            lost.append(it["account_id"])
        prog.tick(not bad, account_id=it["account_id"])
    prog.done_()
    print(f"复核：不符 {len(bad)} {bad[:8]} | credentials 丢失 {len(lost)}")
    print(f"回滚文件：{path}（用 sub2api_rebalance_th_proxies.py --rollback 可回退）")


if __name__ == "__main__":
    main()
