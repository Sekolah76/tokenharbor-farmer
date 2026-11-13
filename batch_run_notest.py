#!/usr/bin/env python3
"""batch_run_notest.py — 批量注册 N 个号，**跳过 key 测通环节**（用户要求"不用测试key"）。

与 batch_run.py 的差异只有一处：
  · finalize() 最后一步 tar.test_model(key)（真实打一次 chat/completions）被替换成空操作，
    省掉每个号一次上游请求；
  · 但 t.main() 靠 acct["test"]=="200" 判断"够了没/记不记数"，所以返回值仍是 "200" 只为
    控制流，落盘时统一改写为 "untested"（绝不伪造测通结论）；
  · 有效账号判据 = verified=True（邮箱验证信已点开）+ 有 key。

用法:
  source cf-mail/env.sh && TOR_UPSTREAM_NODE="🇸🇬【亚洲】新加坡01丨Vless" \
    .venv/bin/python -u batch_run_notest.py 100 16 60
"""
import os, sys, json, time, threading

os.environ.setdefault("TOR_AUTOSTART", "1")
import tor_parallel_farm as t
import th_auto_register as tar

SAVE_LOCK = threading.Lock()
UNTESTED = set()


def run():
    target = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    threads = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    mult = int(sys.argv[3]) if len(sys.argv) > 3 else 80
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = f"batch{target}_{stamp}"
    txt_out = f"th_keys_{name}.txt"
    json_out = f"th_keys_{name}.jsonl"

    # ---- patch 1: 跳过 key 测通（不打真实请求）----
    def fake_test(key):
        if key:
            UNTESTED.add(key)
        return True, "200"          # 仅喂控制流，落盘前会被改写

    tar.test_model = fake_test

    # ---- patch 2: 落盘统一三份，并把 test 改为 untested ----
    n_all, n_valid = [0], [0]

    def save_spy(acct):
        rec = dict(acct)
        if rec.get("api_key") in UNTESTED:
            rec["test"] = "untested"
        ok = bool(rec.get("verified")) and bool(rec.get("api_key"))
        with SAVE_LOCK:
            n_all[0] += 1
            if ok:
                n_valid[0] += 1
            with open("th_direct_results.jsonl", "a") as f:
                f.write(json.dumps(rec) + "\n")
            if rec.get("api_key"):
                with open("th_keys.txt", "a") as f:
                    f.write(f"{rec['email']}\t{rec['password']}\t{rec['api_key']}"
                            f"\tverified={rec.get('verified')}\ttest={rec.get('test')}\n")
            with open(json_out, "a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if ok:
                with open(txt_out, "a") as f:
                    f.write(f"{rec['email']}\t{rec['password']}\t{rec.get('api_key')}"
                            f"\tverified={rec.get('verified')}\ttest={rec.get('test')}\n")

    t.save = save_spy

    print(f"=== 批量注册(跳过测key) | 目标 {target} | 线程 {threads} | "
          f"尝试上限 {target*mult} ===", flush=True)
    print(f"    本批导出 → {txt_out} / {json_out}", flush=True)

    t0 = time.time()
    try:
        t.main(target, threads, max_attempts=target * mult)
    finally:
        dt = time.time() - t0
        print("\n=== 批次导出 ===", flush=True)
        print(f"  有效(已验证) key → {txt_out}（{n_valid[0]} 条）", flush=True)
        print(f"  全量记录 → {json_out}（{n_all[0]} 条）", flush=True)
        print(f"  本批耗时 {dt/60:.1f} 分钟（{dt/max(n_all[0],1):.0f}s / 成功注册）", flush=True)


if __name__ == "__main__":
    run()
