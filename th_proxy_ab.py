#!/usr/bin/env python3
"""th_proxy_ab.py — 对指定账号做轮次交错的端到端延迟采样（不改绑定，只测）

用法:
  python3 th_proxy_ab.py --ids-file runs/ab_sample.json --rounds 2 --out runs/ab_before.json
采样方式：把所有账号顺序打乱，逐轮各测一次，取中位 —— 让时间漂移均匀落在每个账号上。
"""
import argparse, json, os, random, statistics, sys, threading, queue, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ss2a_client import refresh, api
from progress import Progress

def ttfb(aid, tok, timeout=45):
    req = urllib.request.Request(f"https://ss2a.top/api/v1/accounts/{aid}/test", data=b"{}",
        headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json",
                 "Accept": "text/event-stream"}, method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            for line in r:
                if line.strip():
                    return round((time.time() - t0) * 1000)
    except Exception:
        return None
    return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids-file", required=True)
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    spec = json.load(open(args.ids_file, encoding="utf-8"))
    sample = spec["sample"]; control = spec.get("control", [])
    tok, _, _ = refresh()
    targets = sample + control
    res = {aid: [] for aid in targets}
    lock = threading.Lock()
    q = queue.Queue()
    for r in range(args.rounds):
        order = targets[:]
        random.Random(2000 + r).shuffle(order)
        for aid in order:
            q.put((r, aid))

    def work():
        while True:
            try:
                r, aid = q.get_nowait()
            except queue.Empty:
                return
            v = ttfb(aid, tok)
            with lock:
                if v:
                    res[aid].append(v)
            prog.tick(bool(v), round=r + 1, account_id=aid, ms=v)
            q.task_done()
            time.sleep(0.05)

    prog = Progress(len(targets) * args.rounds, f"测量({args.rounds}轮)",
                    every_pct=5.0, log_path=args.out.replace(".json", ".progress.jsonl"))
    threads = [threading.Thread(target=work, daemon=True) for _ in range(args.workers)]
    t0 = time.time()
    for t in threads: t.start()
    for t in threads: t.join()
    prog.done_()

    out = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "rounds": args.rounds, "per_account": {}, "groups": {}}
    for aid, vals in res.items():
        if vals:
            out["per_account"][str(aid)] = {"n": len(vals), "median": int(statistics.median(vals)), "vals": sorted(vals)}
    s = [out["per_account"][str(a)]["median"] for a in sample if str(a) in out["per_account"]]
    c = [out["per_account"][str(a)]["median"] for a in control if str(a) in out["per_account"]]
    out["groups"]["sample"] = {"n": len(s), "median": int(statistics.median(s)) if s else None,
                               "mean": int(statistics.mean(s)) if s else None}
    out["groups"]["control"] = {"n": len(c), "median": int(statistics.median(c)) if c else None,
                                "mean": int(statistics.mean(c)) if c else None}
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    json.dump(out, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"用时 {time.time()-t0:.0f}s | 样本 {out['groups']['sample']} | 对照 {out['groups']['control']}")
    print(f"→ {args.out}")

if __name__ == "__main__":
    main()
