#!/usr/bin/env python3
"""river/node_sweep.py — 逐节点扫：切换 Clash Verge 节点 → 验出口 → 试注册页 CF 挑战。

Clash Verge (mihomo) 用 unix socket 控制（-ext-ctl-unix），无需 secret。
代理组「良心云」是全局 Selector，切它即切全局出口。

用法：
    .venv/bin/python river/node_sweep.py            # 扫全部可用节点
    .venv/bin/python river/node_sweep.py --limit 8  # 只扫前 8 个
    .venv/bin/python river/node_sweep.py --group 良心云
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SOCK = "/var/folders/13/mjjw8v4s63qdq4xy9051f9700000gp/T/verge-mihomo.sock"
PROXY = "http://127.0.0.1:7890"
DEFAULT_GROUP = "良心云"

LOGIN = "https://login.console.river.ai"


def api(path, method="GET", body=None):
    """走 unix socket 调 mihomo API。"""
    url = f"http://localhost{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    # urllib 不支持 unix socket，用 curl 转发
    cmd = ["curl", "-s", "--max-time", "8", "--unix-socket", SOCK]
    if method != "GET":
        cmd += ["-X", method]
    if data:
        cmd += ["-H", "Content-Type: application/json", "-d", data.decode()]
    cmd.append(url)
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15).stdout
        return json.loads(out) if out.strip() else {}
    except Exception:
        return {}


def list_nodes(group=DEFAULT_GROUP):
    d = api("/proxies")
    proxies = d.get("proxies", {})
    g = proxies.get(group)
    if not g:
        return None, []
    return g.get("now", ""), [n for n in (g.get("all") or [])
                               if n not in ("DIRECT", "REJECT")]


def switch(group, node):
    """切节点。"""
    return api(f"/proxies/{urllib.parse.quote(group)}", "PUT", {"name": node})


def current_ip():
    for url in ("https://api.ipify.org", "https://icanhazip.com"):
        try:
            r = subprocess.run(
                ["curl", "-s", "--max-time", "12", "-x", PROXY, url],
                capture_output=True, text=True, timeout=18)
            ip = r.stdout.strip()
            if ip and len(ip) < 40:
                return ip
        except Exception:
            pass
    return None


def test_signup_cf():
    """headless Chrome 挂 7890 访问注册页，返回 (状态, 出口信息)。

    状态: ok=表单可见 / cf=Cloudflare 挑战 / err=其他
    """
    from river.browser import Browser, fetch_config, LOGIN, REDIRECT_URI
    import urllib.parse as up

    try:
        cid = fetch_config()["workos_client_id"]
    except Exception:
        return "err", "config 拉不到"

    q = f"client_id={up.quote(cid)}&redirect_uri={up.quote(REDIRECT_URI, safe='')}"
    try:
        with Browser(headless=True, port=9510 + os.getpid() % 40,
                     profile=f"/tmp/rv-sweep-{os.getpid()}",
                     proxy=PROXY) as b:
            b.goto(f"{LOGIN}/?{q}", wait=9)
            # 登录页正常 → 点 Sign up 看能不能过
            t = b.js("return document.title;") or ""
            if "请稍候" in t or "just a moment" in t.lower():
                return "cf_login", t
            # 点 Sign up
            b.js("""return (() => {
                const el=[...document.querySelectorAll('a,button,[role=link]')]
                  .find(x=>/^sign ?up$/i.test((x.innerText||'').trim()));
                if(el) el.click(); })();""")
            # 等表单（给 CF 留 35s 自动通过时间）
            for _ in range(7):
                b.pump(5)
                t = b.js("return document.title;") or ""
                fn = b.js("return (()=>{const e=document.querySelector("
                          "'input[name=first_name]');"
                          "return !!(e&&e.offsetWidth>0);})()")
                if fn:
                    return "ok", f"{t} | 路径 {b.js('return location.pathname;')}"
                if "请稍候" in t or "just a moment" in t.lower():
                    continue
                return "err", f"{t} | {(b.text() or '')[:80]}"
            return "cf_signup", "CF 挑战未过（35s）"
    except Exception as e:
        return "err", str(e)[:80]


def main():
    global urllib
    import urllib.parse  # noqa: F401

    ap = argparse.ArgumentParser()
    ap.add_argument("--group", default=DEFAULT_GROUP)
    ap.add_argument("--limit", type=int, default=0, help="只扫前 N 个节点")
    ap.add_argument("--regions", default="",
                    help="逗号分隔的地区过滤，如 新加坡,日本")
    ap.add_argument("--stop-on-ok", action="store_true", default=True)
    args = ap.parse_args()

    cur, nodes = list_nodes(args.group)
    if not nodes:
        print(f"拿不到组 {args.group} 的节点列表")
        return 1

    # 过滤：跳过信息节点（套餐/流量）与无延迟的
    skip_kw = ("剩余流量", "套餐到期", "官网", "PASS", "REJECT")
    cand = [n for n in nodes if not any(k in n for k in skip_kw)]
    if args.regions:
        regs = [r.strip() for r in args.regions.split(",") if r.strip()]
        cand = [n for n in cand if any(r in n for r in regs)]
    if args.limit:
        cand = cand[:args.limit]

    print(f"组: {args.group} | 当前: {cur} | 计划扫描 {len(cand)} 个节点\n")
    print(f"{'节点':40s} {'出口IP':16s} 结果")
    print("-" * 90)

    good = []
    for i, node in enumerate(cand):
        if node == cur:
            print(f"{node[:40]:40s} {'(当前节点)':16s} 跳过")
            continue
        r = switch(args.group, node)
        time.sleep(2.5)                      # 等连接重建
        ip = current_ip()
        st, info = test_signup_cf()
        mark = {"ok": "✅ 可注册", "cf_login": "⛔ CF(登录页)",
                "cf_signup": "⛔ CF(注册页)", "err": "· 异常"}.get(st, st)
        print(f"{node[:40]:40s} {str(ip)[:16]:16s} {mark}  {info[:60]}")
        if st == "ok":
            good.append(node)
            print(f"\n✓ 找到能过的节点：{node}（出口 {ip}）")
            print(f"✓ 已停在该节点，可以直接跑：")
            print(f"  cd /Users/zxc/tokenharbor-farmer && source cf-mail/env.sh")
            print(f"  .venv/bin/python river/run.py loop 2 --delay 8 "
                  f"--max-retry 2 --proxies {PROXY}")
            return 0
        time.sleep(1)

    print(f"\n扫完 {len(cand)} 个节点，能过的：{len(good)} 个")
    if not good:
        print("都没过。可选地区重试：--regions 新加坡 或 日本")
    return 0 if good else 2


import urllib.parse  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())