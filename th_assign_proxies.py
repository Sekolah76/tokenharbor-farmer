#!/usr/bin/env python3
"""th_assign_proxies.py — 给 sub2api 导入文件里的每个账号分配不同的（公开）代理

原理（sub2api 官方导入格式 sub2api-data v1 原生支持）：
  - 顶层 `proxies: [ {proxy_key, name, protocol, host, port, username, password, status, fallback_mode} ]`
  - 每个账号的 `proxy_key` 指向其中一个代理
  - 导入时 sub2api 先按 proxy_key 建/复用代理，再把账号绑到它（accounts.proxy_id），
    之后该账号访问上游就走这个代理
  - proxy_key 必须与 sub2api 的 buildProxyKey 完全一致：`protocol|host|port|username|password`
    （四段都 trim；用户名/密码为空时留空串）

流程：代理源(文件 / 公开源抓取) → 并发验活 + 出口地区 → 剔除 CN/HK/MO →
      可选：经代理打上游 /v1/models 探连通性（默认用无效 key，只看能不能连上）→
      按 mode 分配给每个账号 → 输出新导入文件（proxies + 每账号 proxy_key）

实测经验：免费公开代理总量大，但"能连到 tokenharbor"的比例只有几个百分点
（站点在 Cloudflare 后面，大量代理被边缘挡）；要 101 个一对一的，得从万级候选里筛，
或者用 --mode cycle 先轮换顶上。付费住宅/自建 VPS 出口才是稳的路子。

用法:
  # 抓公开代理（多源）→ 验活 → 探上游 → 循环分给所有账号
  python3 th_assign_proxies.py --fetch --fetch-all --probe-upstream

  # 用自己的代理清单，一对一分配
  python3 th_assign_proxies.py --proxies-file my-proxies.txt --mode unique

  # 只看筛出多少能用的（不生成文件）
  python3 th_assign_proxies.py --fetch --fetch-all --probe-upstream --check-only
"""
import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

DEFAULT_INPUT = "sub2api-tokenhour.json"
COUNTRY_URL_TMPL = "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/countries/{cc}/data.txt"
PUBLIC_LIST_SOURCES = [
    "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/http/data.txt",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt",
    "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
]
CHECK_URLS = [
    "https://ipwho.is/",
    "http://ip-api.com/json/?fields=status,countryCode,country,query,isp",
    "https://api.ipify.org?format=json",
]

try:  # socks5 需要 PySocks（pip install requests[socks]）
    import socks  # noqa: F401
    HAS_SOCKS = True
except ImportError:
    HAS_SOCKS = False


# ---------------------------------------------------------------- 代理行解析
def parse_proxy_line(line):
    """支持：scheme://[user:pass@]host:port | host:port | host:port:user:pass | user:pass@host:port"""
    raw = line.strip()
    if not raw or raw.startswith("#"):
        return None
    scheme = "http"
    m = re.match(r"^([a-zA-Z][a-zA-Z0-9]*)://(.*)$", raw)
    if m:
        scheme, raw = m.group(1).lower(), m.group(2)
    if scheme not in ("http", "https", "socks5", "socks5h"):
        return None

    username = password = ""
    if "@" in raw:
        cred, raw = raw.rsplit("@", 1)
        if ":" in cred:
            username, password = cred.split(":", 1)
        else:
            username = cred
    parts = raw.split(":")
    if len(parts) == 2:
        host, port = parts
    elif len(parts) == 4:
        host, port, username, password = parts
    elif len(parts) == 3:
        host, port, username = parts
    else:
        return None
    host, port = host.strip(), port.strip()
    if not host or not port.isdigit() or not (0 < int(port) < 65536):
        return None
    username, password = username.strip(), password.strip()
    if "|" in (host + username + password):
        return None
    return {"protocol": scheme, "host": host, "port": int(port), "username": username, "password": password}


def proxy_key(p):
    """与 sub2api buildProxyKey 完全一致"""
    return f"{p['protocol']}|{p['host']}|{p['port']}|{p.get('username', '')}|{p.get('password', '')}"


def proxy_url(p):
    cred = f"{p.get('username','')}:{p.get('password','')}@" if p.get("username") else ""
    return f"{p['protocol']}://{cred}{p['host']}:{p['port']}"


# ---------------------------------------------------------------- 抓源
def fetch_public(args):
    out = []
    if args.fetch_all:
        for url in PUBLIC_LIST_SOURCES:
            try:
                r = requests.get(url, timeout=25)
                if r.status_code != 200:
                    print(f"   ⚠️ {url.split('/')[4]}: HTTP {r.status_code}")
                    continue
                lines = [l for l in r.text.splitlines() if l.strip()]
                print(f"   {url.split('/')[4]:<12} {len(lines)} 条（取前 {args.fetch_limit}）")
                out.extend(lines[: args.fetch_limit])
            except Exception as exc:  # noqa: BLE001
                print(f"   ⚠️ {url.split('/')[4]}: {type(exc).__name__}")
    ccs = [c.strip().upper() for c in args.fetch_countries.split(",") if c.strip()]
    for cc in ccs:
        url = COUNTRY_URL_TMPL.format(cc=cc)
        try:
            r = requests.get(url, timeout=20)
            if r.status_code != 200:
                continue
            lines = [l for l in r.text.splitlines() if l.strip()]
            print(f"   {cc:<12} {len(lines)} 条（取前 {args.fetch_per_country}）")
            out.extend(lines[: args.fetch_per_country])
        except Exception:  # noqa: BLE001
            continue
    return out


# ---------------------------------------------------------------- 验活
def check_one(line, args):
    p = parse_proxy_line(line)
    if not p:
        return None
    url = proxy_url(p)
    proxies = {"http": url, "https": url}
    started = time.time()
    for target in CHECK_URLS:
        try:
            r = requests.get(target, proxies=proxies, timeout=args.timeout,
                             headers={"User-Agent": "curl/8.0"}, allow_redirects=True)
            if r.status_code != 200:
                continue
            try:
                body = r.json()
            except Exception:  # noqa: BLE001
                body = {}
            if "ipify" in target:
                ip, cc, country, isp = body.get("ip", ""), "", "", ""
            elif "ipwho" in target:
                if not body.get("success", False):
                    continue
                ip, cc = body.get("ip", ""), body.get("country_code", "")
                country, isp = body.get("country", ""), (body.get("connection") or {}).get("isp", "")
            else:
                if body.get("status") != "success":
                    continue
                ip, cc = body.get("query", ""), body.get("countryCode", "")
                country, isp = body.get("country", ""), body.get("isp", "")
            p.update({"exit_ip": ip, "country_code": cc.upper(), "country": country, "isp": isp,
                      "latency_ms": int((time.time() - started) * 1000), "line": line.strip()})
            return p
        except Exception:  # noqa: BLE001
            continue
    return None


def probe_upstream(p, api_key, args):
    """经代理打一次上游 /v1/models，只判"能不能到"：
    200 = 能到且 key 有效；401/403(JSON 且有 error.code) = 能到但 key 无效/被拒；
    Cloudflare 挑战页 / region_blocked / 超时 / 5xx = 不可用。
    默认用无效 key 只测连通性，不消耗也不暴露真 key。"""
    url = proxy_url(p)
    try:
        r = requests.get(f"{args.upstream_base}/models",
                         headers={"Authorization": f"Bearer {api_key}"},
                         proxies={"http": url, "https": url}, timeout=args.timeout + 8)
        code = r.status_code
        text = r.text[:600]
        low = text.lower()
        body = {}
        try:
            body = r.json()
        except Exception:  # noqa: BLE001
            body = {}
        err = body.get("error") if isinstance(body, dict) else None
        err_code = str((err or {}).get("code") or "") if isinstance(err, dict) else ""
        if code == 200:
            return True, f"200 ({len((body.get('data') or []))} 模型)"
        if "cloudflare" in low or "just a moment" in low or "attention required" in low or low.lstrip().startswith("<html"):
            return False, f"{code} cloudflare-blocked"
        if code in (401, 403):
            if "region" in err_code.lower():
                return False, f"{code} region_blocked"
            if not err_code:
                return False, f"{code} non-api-response"
            return True, f"{code} {err_code}"
        return False, f"{code} {err_code or ''}".strip()
    except Exception as exc:  # noqa: BLE001
        return False, f"ERR {type(exc).__name__}"


# ---------------------------------------------------------------- main
def main():
    p = argparse.ArgumentParser(description="给 sub2api 账号分配不同的（公开）代理")
    p.add_argument("-i", "--input", default=DEFAULT_INPUT, help=f"账号导入文件（默认 {DEFAULT_INPUT}）")
    p.add_argument("-o", "--output", default=None, help="输出文件（默认 <input>-with-proxies.json）")
    p.add_argument("--proxies-file", default=None, help="代理清单文件（每行一个）")
    p.add_argument("--fetch", action="store_true", help="从公开源抓取代理")
    p.add_argument("--fetch-all", action="store_true", help="抓全量源（proxifly/TheSpeedX/monosans/…）")
    p.add_argument("--fetch-limit", type=int, default=1500, help="每个全量源最多取多少条（默认 1500）")
    p.add_argument("--fetch-countries", default="US,SG,JP,DE,NL,GB,FR,CA,IN,ID,TR,BR,VN,TH,MY",
                   help="按国家抓（proxifly；默认不含 CN/HK/MO）")
    p.add_argument("--fetch-per-country", type=int, default=200, help="每个国家最多取多少条（默认 200）")
    p.add_argument("--max-candidates", type=int, default=4000, help="候选上限（默认 4000，防止跑飞）")
    p.add_argument("--block-countries", default="CN,HK,MO",
                   help="验活后剔除的出口国家（默认 CN,HK,MO——tokenharbor 对这三地返回 403 region_blocked）")
    p.add_argument("--workers", type=int, default=60, help="验活并发（默认 60）")
    p.add_argument("--timeout", type=int, default=6, help="单次探测超时秒（默认 6）")
    p.add_argument("--no-check", action="store_true", help="跳过验活（信任清单里全是活的）")
    p.add_argument("--check-only", action="store_true", help="只验活/探测，不生成导入文件")
    p.add_argument("--probe-upstream", action="store_true",
                   help="经每个代理打一次上游 /v1/models 探连通性（默认用无效 key，只判能不能连上）")
    p.add_argument("--probe-key", default=None, help="探测用的 key（默认用无效 key；传真实 key 才会返回 200）")
    p.add_argument("--probe-limit", type=int, default=400,
                   help="最多探测多少个活代理（按延迟从低到高，默认 400；0=全探）")
    p.add_argument("--upstream-base", default="https://tokenharbor.ai/v1", help="上游 base（默认 tokenharbor）")
    p.add_argument("--mode", choices=["cycle", "unique"], default="cycle",
                   help="分配方式：cycle 循环复用一个代理给多个账号；unique 要求代理数 >= 账号数")
    p.add_argument("--fallback", choices=["none", "direct", "proxy"], default="direct",
                   help="代理 fallback_mode（默认 direct：代理失效走直连）")
    p.add_argument("--proxy-name-prefix", default="tokenhour-proxy-", help="代理名前缀")
    p.add_argument("--live-out", default="runs/proxies-live.json", help="验活/探测结果落盘路径")
    args = p.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    args.input = args.input if os.path.isabs(args.input) else os.path.join(here, args.input)
    live_path = args.live_out if os.path.isabs(args.live_out) else os.path.join(here, args.live_out)

    # 1) 候选代理
    lines = []
    if args.proxies_file:
        path = args.proxies_file if os.path.isabs(args.proxies_file) else os.path.join(here, args.proxies_file)
        lines += [l.strip() for l in open(path, encoding="utf-8") if l.strip()]
        print(f"📄 从 {path} 读入 {len(lines)} 条候选")
    if args.fetch:
        print("🌐 抓取公开代理…")
        lines += fetch_public(args)
    lines = list(dict.fromkeys(lines))
    if not lines:
        print("❌ 没有候选代理（用 --proxies-file 或 --fetch）")
        return 1
    if not HAS_SOCKS:
        before = len(lines)
        lines = [l for l in lines if not re.match(r"^\s*socks", l)]
        if before != len(lines):
            print(f"   ⚠️ 没装 PySocks，跳过 {before - len(lines)} 条 socks 代理（pip install requests[socks] 可启用）")
    if args.max_candidates and len(lines) > args.max_candidates:
        print(f"   ⚠️ 候选 {len(lines)} 条超过上限，随机截取 {args.max_candidates} 条")
        rest = lines[args.max_candidates:]
        lines = lines[: args.max_candidates]
    print(f"🔢 去重后候选 {len(lines)} 条")

    # 2) 验活
    live, dead, blocked = [], 0, []
    if args.no_check:
        live = [c for c in (parse_proxy_line(l) for l in lines) if c]
        print(f"⏭  跳过验活，直接采用 {len(live)} 条")
    else:
        print(f"🔬 验活中（并发 {args.workers}，超时 {args.timeout}s，剔除 {args.block_countries}）…")
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(check_one, line, args) for line in lines]
            for fut in as_completed(futures):
                res = fut.result()
                if not res:
                    dead += 1
                    continue
                cc = res.get("country_code", "")
                if args.block_countries and cc in {c.strip().upper() for c in args.block_countries.split(",")}:
                    blocked.append(res)
                    continue
                live.append(res)
        live.sort(key=lambda x: x.get("latency_ms", 9999))
        print(f"✅ 可用 {len(live)} 条｜不通 {dead} 条｜地区受限剔除 {len(blocked)} 条（{time.time()-t0:.0f}s）")
        if live:
            from collections import Counter
            print("   出口国家分布:", dict(Counter(x["country_code"] or "?" for x in live).most_common(10)))
            print(f"   延迟: 最快 {live[0].get('latency_ms')}ms / 中位 {live[len(live)//2].get('latency_ms')}ms")
        os.makedirs(os.path.dirname(live_path), exist_ok=True)
        json.dump({"checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "live": live, "blocked": blocked},
                  open(live_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"💾 验活结果: {live_path}")

    if not live:
        print("❌ 没有可用代理")
        return 1

    # 3) 可选：经代理打上游（默认无效 key 只测连通性）
    if args.probe_upstream:
        key = args.probe_key or "sk-probe-invalid"
        used = "传入的真 key" if args.probe_key else "无效探测 key（只看能不能连上）"
        pool_list = live if not args.probe_limit else live[: args.probe_limit]
        print(f"🎯 经代理探测 {args.upstream_base}（{used}，探测 {len(pool_list)} 条，并发 {min(args.workers, 40)}）…")
        ok_list, bad = [], []
        with ThreadPoolExecutor(max_workers=min(args.workers, 40)) as pool:
            futures = {pool.submit(probe_upstream, x, key, args): x for x in pool_list}
            for fut in as_completed(futures):
                x = futures[fut]
                ok, detail = fut.result()
                x["upstream_probe"] = detail
                (ok_list if ok else bad).append(x)
        live = sorted(ok_list, key=lambda x: x.get("latency_ms", 9999))
        print(f"✅ 能到上游 {len(live)} 条｜打不通 {len(bad)} 条")
        json.dump({"checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "live": live, "bad": bad},
                  open(live_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        if not live:
            print("❌ 没有能打通上游的代理（免费代理被 Cloudflare 挡是常态，换来源或改用 cycle 模式）")
            return 1

    if args.check_only:
        print("⏭  --check-only：不生成导入文件")
        return 0

    # 4) 分配
    doc = json.load(open(args.input, encoding="utf-8"))
    accounts = doc["accounts"]
    if args.mode == "unique" and len(live) < len(accounts):
        print(f"⚠️ unique 模式需要 {len(accounts)} 个代理，当前只有 {len(live)} 个 → 退回 cycle 模式")
        args.mode = "cycle"

    assign = live[: len(accounts)] if args.mode == "unique" else [live[i % len(live)] for i in range(len(accounts))]

    data_proxies, key_to_name = [], {}
    for pr in assign:
        k = proxy_key(pr)
        if k in key_to_name:
            continue
        name = f"{args.proxy_name_prefix}{len(key_to_name)+1:03d}"
        key_to_name[k] = name
        item = {
            "proxy_key": k,
            "name": name,
            "protocol": pr["protocol"],
            "host": pr["host"],
            "port": pr["port"],
            "status": "active",
            "fallback_mode": args.fallback,
            "expiry_warn_days": 0,
        }
        if pr.get("username"):
            item["username"] = pr["username"]
        if pr.get("password"):
            item["password"] = pr["password"]
        data_proxies.append(item)

    for acc, pr in zip(accounts, assign):
        acc["proxy_key"] = proxy_key(pr)
        acc["notes"] = (acc.get("notes") or "") + f" | 代理: {key_to_name[proxy_key(pr)]}"

    doc["proxies"] = data_proxies
    out_path = args.output or (args.input[:-5] + "-with-proxies.json")
    out_path = out_path if os.path.isabs(out_path) else os.path.join(here, out_path)
    json.dump(doc, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    uniq = len({proxy_key(p) for p in assign})
    print()
    print(f"✅ 生成: {out_path}")
    print(f"   账号 {len(accounts)} 个｜代理 {len(data_proxies)} 个（{args.mode} 模式，"
          f"{'一对一' if uniq == len(accounts) else f'{uniq} 个代理循环覆盖 {len(accounts)} 个账号'}，"
          f"fallback_mode={args.fallback}）")
    print(f"   示例: {accounts[0]['name']} → {key_to_name[accounts[0]['proxy_key']]} "
          f"({assign[0]['host']}:{assign[0]['port']} {assign[0].get('country_code','')} "
          f"{assign[0].get('latency_ms','?')}ms {assign[0].get('upstream_probe','')})")
    print()
    print("导入（sub2api 会先建代理再绑账号）:")
    print(f"   python3 sub2api_push.py --base-url https://你的sub2api --token <admin-token> "
          f"--file {os.path.basename(out_path)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
