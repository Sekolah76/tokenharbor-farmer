#!/usr/bin/env python3
"""全量测活 proxyscrape-* 代理池，输出成功/延迟/国家分布"""
import json, sys, time, collections
sys.path.insert(0, "/Users/zxc/tokenharbor-farmer")
from ss2a_client import api

ps = json.load(open("runs/proxyscrape_final.json"))
ok, bad, cc = [], [], collections.Counter()
for i, p in enumerate(ps, 1):
    r = None
    for _ in range(3):
        st, js = api("POST", f"/api/v1/proxies/{p['id']}/test")
        if st == 200:
            r = js.get("data", {}); break
        time.sleep(1)
    if r and r.get("success"):
        ok.append((p["name"], r.get("latency_ms"), r.get("country_code")))
        cc[r.get("country_code")] += 1
    else:
        bad.append((p["name"], (r or {}).get("message")))
    if i % 20 == 0:
        print(f"进度 {i}/100 ok={len(ok)} bad={len(bad)}", flush=True)

ok.sort(key=lambda x: x[1] or 9999)
print(f"\n通 {len(ok)} / {len(ps)}，失败 {len(bad)}")
print("延迟: 最小", ok[0][1], "中位", ok[len(ok)//2][1], "最大", ok[-1][1])
print("国家分布:", dict(cc))
if bad: print("失败样例:", bad[:10])
json.dump({"ok": ok, "bad": bad, "countries": dict(cc)},
          open("runs/proxyscrape_test_report.json", "w"), indent=1, ensure_ascii=False)
