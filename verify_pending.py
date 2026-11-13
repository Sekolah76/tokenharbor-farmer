#!/usr/bin/env python3
"""verify_pending.py — 给已注册但 verified=pending 的账号补做邮箱验证。

th_fast_register.py 为了速度把邮箱验证推后了（注册一次 ~33s，验证邮件要等 40-90s，
混在一起会拖慢并发）。这里统一补：读 jsonl 拿 email_token → 点验证链接。

用法:
  .venv/bin/python verify_pending.py --files 'th_keys_fast_*.jsonl'
"""
import argparse
import glob
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "/Users/zxc/tokenharbor-farmer")
os.environ.setdefault("TOR_AUTOSTART", "0")
import th_auto_register as tar

STAMP = time.strftime("%Y%m%d-%H%M%S")


def verify_one(rec):
    email = rec.get("email")
    etok = rec.get("email_token")
    if not etok:
        return {"email": email, "ok": False, "why": "no email_token"}
    try:
        ok = tar.verify_email(etok, max_wait=240)
    except Exception as e:
        return {"email": email, "ok": False, "why": f"{type(e).__name__}: {str(e)[:60]}"}
    return {"email": email, "ok": bool(ok), "why": "" if ok else "链接未点开"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", nargs="+", default=["th_keys_fast_*.jsonl"])
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--done-file", default="verified_done.jsonl")
    A = ap.parse_args()

    done = set()
    if os.path.exists(A.done_file):
        for line in open(A.done_file):
            try:
                done.add(json.loads(line).get("email"))
            except Exception:
                pass

    recs = []
    seen = set()
    for pat in A.files:
        for path in sorted(glob.glob(pat)):
            for line in open(path, encoding="utf-8"):
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                if r.get("stage") != "signedIn" or not r.get("api_key"):
                    continue
                em = r.get("email")
                if em in seen or em in done:
                    continue
                seen.add(em)
                recs.append(r)

    print(f"待验证 {len(recs)} 个（已完成的跳过 {len(done)} 个）")
    if not recs:
        return

    ok_n = 0
    fail = []
    t0 = time.time()

    def work(r):
        res = verify_one(r)
        with open(A.done_file, "a") as f:
            f.write(json.dumps({**res, "email": r.get("email"),
                                "password": r.get("password"),
                                "api_key": r.get("api_key")}, ensure_ascii=False) + "\n")
        return res, r

    with ThreadPoolExecutor(max_workers=A.workers) as ex:
        for i, (res, r) in enumerate(ex.map(work, recs), 1):
            if res["ok"]:
                ok_n += 1
            else:
                fail.append((res["email"], res["why"]))
            if i % 10 == 0 or i == len(recs):
                print(f"  ... {i}/{len(recs)} 成功 {ok_n} 失败 {len(fail)} "
                      f"({time.time()-t0:.0f}s)", flush=True)

    print(f"\n=== 邮箱验证: 成功 {ok_n} / 失败 {len(fail)} | {(time.time()-t0)/60:.1f} 分钟 ===")
    for e, w in fail[:10]:
        print(f"  ✗ {e}: {w}")

    # 汇总成导入用的 key 文件（verified=True）
    verified = {}
    for line in open(A.done_file):
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("ok") and d.get("api_key"):
            verified[d["email"]] = d
    out = f"th_keys_verified_{STAMP}.txt"
    with open(out, "w") as f:
        for em, d in verified.items():
            f.write(f"{em}\t{d.get('password')}\t{d['api_key']}\tverified=True\ttest=untested\n")
    print(f"已验证账号清单 → {out}（{len(verified)} 条）")


if __name__ == "__main__":
    main()
