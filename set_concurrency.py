#!/usr/bin/env python3
"""set_concurrency.py — 把指定前缀的账号并发统一改成 N（默认 1）。

只发 {"concurrency": N} 这一个字段，不动 credentials —— 实测单号验证过
（model_mapping / temp_rules / base_url / extra / proxy_id / group_ids 全保留）。

用法:
  .venv/bin/python set_concurrency.py                # tokenhour 前缀，改成 1
  .venv/bin/python set_concurrency.py --prefix tokenhour --to 1
  .venv/bin/python set_concurrency.py --dry-run      # 只看会改几个
"""
import argparse
import collections
import sys
import time

sys.path.insert(0, "/Users/zxc/tokenharbor-farmer")
from ss2a_client import api


def retry(fn, n=4, delay=1.5):
    last = None
    for i in range(n):
        try:
            return fn()
        except Exception as e:
            last = e
            time.sleep(delay * (i + 1))
    raise last


def fetch(prefix):
    st, js = retry(lambda: api("GET", "/api/v1/accounts",
                               params={"search": prefix, "page": 1, "page_size": 800}))
    items = (js.get("data") or {}).get("items") or []
    return [a for a in items if (a.get("name") or "").startswith(prefix)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", default="tokenhour")
    ap.add_argument("--to", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-snapshot", action="store_true",
                    help="跳过改前快照（不建议：出错就没法回退）")
    A = ap.parse_args()

    accs = fetch(A.prefix)
    print(f"{A.prefix} 账号 {len(accs)} 个")
    print(f"当前并发分布: {dict(collections.Counter(a.get('concurrency') for a in accs))}")

    todo = [a for a in accs if a.get("concurrency") != A.to]
    print(f"需要改成 {A.to} 的: {len(todo)} 个")
    if A.dry_run:
        for a in todo[:5]:
            print(f"  {a['name']}  {a.get('concurrency')} → {A.to}")
        return
    if not todo:
        print("已经是目标值，无需操作")
        return

    # ★ 铁律：真改之前自动拍快照
    if not A.no_snapshot:
        try:
            import subprocess as _sp
            label = f"before_concurrency_{time.strftime('%H%M%S')}"
            r = _sp.run([sys.executable, "ss2a_snapshot.py",
                         "--prefix", A.prefix, "--label", label],
                        capture_output=True, text=True, timeout=600)
            for line in (r.stdout or "").strip().splitlines():
                if "快照 →" in line or "✗" in line or "⚠" in line:
                    print("  " + line.strip())
            if r.returncode != 0:
                print("✗ 快照失败，拒绝继续改")
                return
        except Exception as e:
            print(f"✗ 快照异常 {type(e).__name__}: {str(e)[:80]} —— 拒绝继续改")
            return
    else:
        print("⚠ 已跳过快照（--no-snapshot）")

    ok = fail = 0
    t0 = time.time()
    for i, a in enumerate(todo, 1):
        try:
            st, js = retry(lambda: api("PUT", f"/api/v1/accounts/{a['id']}",
                                       body={"concurrency": A.to}))
            if st == 200:
                ok += 1
            else:
                fail += 1
                print(f"  ✗ {a['name']}: HTTP {st} {str(js)[:80]}")
        except Exception as e:
            fail += 1
            print(f"  ✗ {a['name']}: {str(e)[:80]}")
        if i % 20 == 0:
            print(f"  ... {i}/{len(todo)} 成功 {ok} 失败 {fail} ({time.time()-t0:.0f}s)", flush=True)
    print(f"\n改并发: 成功 {ok} / 失败 {fail}，耗时 {time.time()-t0:.0f}s")

    # 复核
    final = fetch(A.prefix)
    dist = dict(collections.Counter(a.get("concurrency") for a in final))
    print(f"复核 {len(final)} 个账号，并发分布: {dist}")
    bad = [a for a in final if a.get("concurrency") != A.to]
    print("全部合规" if not bad else f"⚠ 仍有 {len(bad)} 个不是 {A.to}")
    for a in bad[:10]:
        print("  ⚠", a.get("name"), a.get("concurrency"))

    # credentials / 分组 / 代理完整性抽检
    no_key = sum(1 for a in final if not (a.get("credentials_status") or {}).get("has_api_key"))
    no_proxy = sum(1 for a in final if not a.get("proxy_id"))
    no_group = sum(1 for a in final if not a.get("group_ids"))
    no_map = sum(1 for a in final if not (a.get("credentials") or {}).get("model_mapping"))
    print(f"完整性: 无key {no_key} | 无代理 {no_proxy} | 无分组 {no_group} | 无映射 {no_map}")


if __name__ == "__main__":
    main()
