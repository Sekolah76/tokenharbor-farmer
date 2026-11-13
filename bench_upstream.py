#!/usr/bin/env python3
"""bench_upstream.py — 给 Tor 优选上游节点

Tor 的所有中继连接都经 mihomo 代理出去，所以 mihomo 节点的延迟直接决定
Tor 每个请求的耗时。这个脚本逐个切换节点并测 Tor 请求延迟。

用法: python3 bench_upstream.py
"""
import json, statistics, subprocess, sys, time
import requests

sys.path.insert(0, ".")
import tor_parallel_farm as tpf
import urllib.parse

SOCK = tpf.__dict__.get("SOCK", None) or \
    "/var/folders/13/mjjw8v4s63qdq4xy9051f9700000gp/T/verge-mihomo.sock"
SECRET = "set-your-secret"
GROUP = urllib.parse.quote("宝可梦")

NODES = [
    "🇸🇬【亚洲】新加坡02丨直连",
    "🇸🇬【亚洲】新加坡01丨直连",
    "🇸🇬【亚洲】新加坡03丨直连",
    "🇸🇬 【亚洲】新加坡01丨Vless",
    "🇸🇬 【亚洲】新加坡02丨Vless",
    "🇯🇵【亚洲】日本01丨Vless",
    "🇯🇵【亚洲】日本02丨Vless",
    "🇯🇵【亚洲】日本03丨Vless",
    "🇺🇸【北美洲】美国02原生丨直连【2x】",
    "🇺🇸【北美洲】美国01原生丨直连【2x】",
]
SAMPLES = 3


def switch(node):
    subprocess.run(["curl", "-s", "-X", "PUT", "--unix-socket", SOCK,
                    "-H", f"Authorization: Bearer {SECRET}",
                    "-H", "Content-Type: application/json",
                    "-d", json.dumps({"name": node}),
                    f"http://localhost/proxies/{GROUP}"],
                   capture_output=True, timeout=15)
    time.sleep(2)


def direct_rtt():
    """mihomo 直连 RTT"""
    best = None
    for _ in range(2):
        t0 = time.time()
        try:
            requests.get("https://api.ipify.org", timeout=15)
            dt = time.time() - t0
            best = dt if best is None else min(best, dt)
        except Exception:
            return None
    return best


def tor_rtt():
    """经 Tor 的 RTT（用隔离流，等同实际注册时的路径）"""
    times = []
    for _ in range(SAMPLES):
        t0 = time.time()
        try:
            requests.get("https://api.ipify.org", proxies=tpf.stream_proxies(), timeout=60)
            times.append(time.time() - t0)
        except Exception:
            pass
    if not times:
        return None, None
    return statistics.median(times), min(times)


def main():
    print(f"=== 上游节点优选 | 每个节点 {SAMPLES} 次 Tor 采样 ===\n")
    tpf.start_tor()
    results = []
    try:
        for i, node in enumerate(NODES, 1):
            switch(node)
            d = direct_rtt()
            tm, tmin = tor_rtt()
            tag = "?" if tm is None else f"{tm:.2f}s"
            print(f"  [{i:>2}/{len(NODES)}] {node:34} 直连 {d and f'{d:.2f}s' or '  -  '}"
                  f" | Tor 中位 {tag:>7} 最快 {tmin and f'{tmin:.2f}s' or '-'}")
            if tm:
                results.append((tm, tmin, node, d))
    finally:
        tpf.stop_tor()

    print(f"\n{'='*70}")
    print("=== 排名（按 Tor 中位延迟，越小越快）===")
    for rank, (tm, tmin, node, d) in enumerate(sorted(results), 1):
        mark = "  ← 最快" if rank == 1 else ""
        print(f"  {rank}. {tm:.2f}s 中位 / {tmin:.2f}s 最快  | 直连 {d:.2f}s | {node}{mark}")
    if results:
        best = min(results)
        cur_idx = [n for n in NODES]
        print(f"\n结论：当前用的节点排在 "
              f"{[n for _,_,n,_ in sorted(results)].index(NODES[0])+1 if NODES[0] in [n for _,_,n,_ in sorted(results)] else '?'}"
              f" 位")
        print(f"最快的是：{best[2]}  ({best[0]:.2f}s vs 当前 {results[0][0]:.2f}s)")


if __name__ == "__main__":
    main()
