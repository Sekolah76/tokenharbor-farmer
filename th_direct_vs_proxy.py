#!/usr/bin/env python3
"""th_direct_vs_proxy.py — 「最快代理 vs 无代理」端到端速度对比，取快者落地

适用：base_url 不含 tokenharbor.ai 的账号（其他上游，未必需要代理）
采样：交替 sequence [proxy, direct, proxy, direct]，每个模式 2 次，取中位；交替可抵消时间漂移
落地：--apply 时把赢家写回（proxy → proxy_id=N，direct → proxy_id=0 清空）
判平：直连不慢于代理 3% 时判直连胜（少一层依赖）

用法：
  python3 -u th_direct_vs_proxy.py --fastest-proxy 15743                 # 只测
  python3 -u th_direct_vs_proxy.py --fastest-proxy 15743 --apply         # 测 + 落地
  python3 -u th_direct_vs_proxy.py --ids 81014,81015 --apply             # 指定账号
"""
import argparse, datetime, json, os, queue, random, statistics, sys, threading, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ss2a_client import api, refresh
from progress import Progress

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "runs")
TOL = 0.03


def ttfb(aid, tok, timeout=45):
    req = urllib.request.Request(f"https://ss2a.top/api/v1/accounts/{aid}/test", data=b"{}",
        headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json",
                 "Accept": "text/event-stream"}, method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            buf = []
            for line in r:
                s = line.decode("utf-8", "replace").strip()
                if s:
                    buf.append(s)
                if len(buf) >= 2:
                    break
        kind = "ok" if '"type":"content"' in " ".join(buf) else ("err" if '"error"' in " ".join(buf) else "?")
        return round((time.time() - t0) * 1000), kind
    except Exception as exc:
        return None, str(exc)[:60]


def fetch_targets(ids=None):
    out, page = [], 1
    while True:
        st, js = api("GET", "/api/v1/accounts", params={"page": page, "page_size": 200})
        d = js.get("data") or {}
        items = d.get("items") or []
        out += items
        if not items or len(out) >= (d.get("total") or 0):
            break
        page += 1
    if ids:
        want = set(ids)
        return [a for a in out if a["id"] in want]
    keep = []
    for a in out:
        bu = ((a.get("credentials") or {}).get("base_url") or "").strip()
        if bu.startswith("http") and "tokenharbor.ai" not in bu:
            keep.append(a)
    return keep


def bind(aid, pid, retries=3):
    for i in range(retries):
        st, js = api("PUT", f"/api/v1/accounts/{aid}", body={"proxy_id": pid if pid else 0}, timeout=45)
        if st == 200:
            return True
        time.sleep(1.2 * (i + 1))
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fastest-proxy", type=int, default=0)
    ap.add_argument("--proxies", help="逗号分隔的多个候选节点，逐一同直连比")
    ap.add_argument("--use-current", action="store_true",
                    help="代理模式用「各号当前所在节点」而不是统一绑到 --fastest-proxy（公平对比现状）")
    ap.add_argument("--map", metavar="RUN_JSON",
                    help="代理模式用某次 rebalance 方案里的 new_proxy_id 作为「现状节点」")
    ap.add_argument("--ids")
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    ids = [int(x) for x in args.ids.split(",")] if args.ids else None
    targets = fetch_targets(ids)
    cur = {a["id"]: a.get("proxy_id") for a in targets}
    if args.map:
        plan = json.load(open(args.map, encoding="utf-8"))["plan"]
        m = {int(x["account_id"]): int(x["new_proxy_id"]) for x in plan}
        cur = {aid: m.get(aid, cur.get(aid)) for aid in cur}
        print(f"现状节点取自方案 {args.map}（{len(m)} 条）")
    mode_label = "现状节点" if args.use_current else f"最快代理#{args.fastest_proxy}"
    print(f"目标账号 {len(targets)} 个 | 代理模式 = {mode_label}", flush=True)
    for a in targets:
        print(f"   {a['id']:6d} {a['name'][:26]:28s} {((a.get('credentials') or {}).get('base_url') or '')[:40]:42s} 现绑={a.get('proxy_id')}")
    tok, _, _ = refresh()

    proxy_ids = [int(x) for x in args.proxies.split(",")] if args.proxies else ([args.fastest_proxy] if args.fastest_proxy else [])
    modes = [f"proxy:{p}" for p in proxy_ids] + ["direct"]
    seq = []
    for r in range(args.rounds):
        for i, m in enumerate(modes if r % 2 == 0 else modes[::-1]):
            order = targets[:]
            random.Random(3000 + r * 10 + i).shuffle(order)
            for a in order:
                seq.append((r, m, a["id"]))

    res = {}
    lock = threading.Lock()
    prog = Progress(len(seq), "对比(代理/直连)", every_pct=5.0,
                    log_path=os.path.join(RUNS, "direct_vs_proxy.progress.jsonl"))

    def work():
        while True:
            try:
                r, m, aid = q.get_nowait()
            except queue.Empty:
                return
            if m.startswith("proxy:"):
                pid = cur.get(aid) if args.use_current else int(m.split(":")[1])
                pid = pid or 0                      # 现状本来就没代理 → 代理模式等同直连
            else:
                pid = 0
            ok = bind(aid, pid)
            ms, kind = (None, "bind-fail") if not ok else ttfb(aid, tok)
            with lock:
                if ms:
                    res.setdefault(aid, {}).setdefault(m, []).append({"ms": ms, "kind": kind, "round": r})
            prog.tick(bool(ms), account_id=aid, mode=m, ms=ms)
            time.sleep(0.1)

    q = queue.Queue()
    for item in seq:
        q.put(item)
    threads = [threading.Thread(target=work, daemon=True) for _ in range(args.workers)]
    t0 = time.time()
    for t in threads: t.start()
    for t in threads: t.join()
    prog.done_()

    rows = []
    for a in targets:
        d = res.get(a["id"], {})
        by_mode = {m: (int(statistics.median([x["ms"] for x in d.get(m, [])])) if d.get(m) else None) for m in modes}
        pv = [x["ms"] for x in d.get("proxy", [])]
        dv = [x["ms"] for x in d.get("direct", [])]
        pk = int(statistics.median(pv)) if pv else None
        dk = int(statistics.median(dv)) if dv else None
        if not proxy_ids:
            avail = {m: v for m, v in by_mode.items() if v}
            if avail:
                win_mode = min(avail, key=lambda m: avail[m])
                win_mode = "direct" if (win_mode == "direct" or avail["direct"] <= avail[win_mode] * (1 + TOL)) and "direct" in avail else win_mode
            else:
                win_mode = None
        else:
            avail = {m: v for m, v in by_mode.items() if v}
            win_mode = min(avail, key=lambda m: avail[m]) if avail else None
        kinds = {x["kind"] for x in d.get("proxy", []) + d.get("direct", [])}
        if pk is None and dk is None:
            win = None
        elif pk is None:
            win = "direct"
        elif dk is None:
            win = "proxy"
        else:
            win = "direct" if dk <= pk * (1 + TOL) else "proxy"
        rows.append({"account_id": a["id"], "name": a["name"],
                     "base_url": (a.get("credentials") or {}).get("base_url"),
                     "old_proxy": a.get("proxy_id"),
                     "proxy_ms": pk, "direct_ms": dk, "proxy_n": len(pv), "direct_n": len(dv),
                     "proxy_samples": pv, "direct_samples": dv, "kinds": sorted(kinds),
                     "ms_by_mode": by_mode, "winner_mode": win_mode,
                     "winner": ("proxy" if (win_mode or "").startswith("proxy") else ("direct" if win_mode == "direct" else None))})

    mh = " | ".join(f"{m}" for m in modes)
    print(f"\n{'账号':>7} {'名称':24s} {'直连ms':>7}  {mh}")
    for r in sorted(rows, key=lambda x: x["account_id"]):
        cells = "  ".join(f"{(r['ms_by_mode'].get(m) or 0):>7}" for m in modes)
        best = r['ms_by_mode'].get(r['winner_mode']) if r['winner_mode'] else None
        print(f"{r['account_id']:>7} {r['name'][:24]:24s} {str(r['direct_ms']):>7}  {cells}   → {r['winner_mode']} ({best}ms)")
    wp = sum(1 for r in rows if r["winner"] == "proxy")
    wd = sum(1 for r in rows if r["winner"] == "direct")
    print(f"\n代理胜 {wp} | 直连胜 {wd} | 测不出来 {len(rows)-wp-wd}")
    for m in modes + (["proxy"] if not proxy_ids else []):
        vals = [r["ms_by_mode"].get(m) for r in rows if r["ms_by_mode"].get(m)]
        if vals:
            print(f"  全体 {m:6s} 中位 {int(statistics.median(vals))}ms  n={len(vals)}")

    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(RUNS, f"direct_vs_proxy_{ts}.json")
    json.dump({"ts": ts, "fastest_proxy": args.fastest_proxy, "tol": TOL, "rows": rows},
              open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"结果已存 {path}")

    if not args.apply:
        print("[dry-run] 加 --apply 落地")
        return

    changed = 0
    prog2 = Progress(len(rows), "落地赢家", every_pct=10.0)
    for r in rows:
        target = int(r["winner_mode"].split(":")[1]) if (r["winner_mode"] or "").startswith("proxy:") else (args.fastest_proxy if r["winner"] == "proxy" else 0)
        if r["winner"] is None or r["winner_mode"] is None:
            prog2.tick(False, account_id=r["account_id"]); continue
        ok = bind(r["account_id"], target)
        changed += 1 if ok else 0
        prog2.tick(ok, account_id=r["account_id"], target=target or "direct")
    prog2.done_()
    bad = []
    for r in rows:
        st, js = api("GET", f"/api/v1/accounts/{r['account_id']}")
        a = js.get("data") or {}
        want = int(r["winner_mode"].split(":")[1]) if (r["winner_mode"] or "").startswith("proxy:") else (args.fastest_proxy if r["winner"] == "proxy" else None)
        if (a.get("proxy_id") or None) != (want or None):
            bad.append(r["account_id"])
    print(f"落地 {changed}/{len(rows)} | 读回复核不符 {len(bad)} {bad[:8]}")


if __name__ == "__main__":
    main()
