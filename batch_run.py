#!/usr/bin/env python3
"""batch_run.py — 批量注册 N 个号，并把本批 key 单独导出到新文件。

用法:
  TOR_AUTOSTART=1 .venv/bin/python -u batch_run.py [数量] [线程] [尝试倍数]

设计:
  · 旧库（th_direct_results.jsonl / th_keys.txt）保持原样追加，历史不断档；
  · 本批产出单独落 th_keys_batch<N>_<时间戳>.txt（制表符，可直接复用）+ 同名 .jsonl；
  · 实时落盘：每出一条就写一行，中途中断也不丢已有产出。
"""
import os, sys, json, time, threading

os.environ.setdefault("TOR_AUTOSTART", "1")
import tor_parallel_farm as t


def run():
    target  = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    threads = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    mult    = int(sys.argv[3]) if len(sys.argv) > 3 else 80
    stamp   = time.strftime("%Y%m%d-%H%M%S")
    name    = f"batch{target}_{stamp}"
    txt_out  = f"th_keys_{name}.txt"
    json_out = f"th_keys_{name}.jsonl"

    lock = threading.Lock()
    n_all, n_valid = [0], [0]
    orig_save = t.save

    def save_spy(acct):
        rec = dict(acct)
        ok = rec.get("verified") and rec.get("test") == "200"
        with lock:
            n_all[0] += 1
            if ok:
                n_valid[0] += 1
            with open(json_out, "a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if ok:
                with open(txt_out, "a") as f:
                    f.write(f"{rec['email']}\t{rec['password']}\t{rec.get('api_key')}"
                            f"\tverified={rec.get('verified')}\ttest={rec.get('test')}\n")
        orig_save(acct)

    t.save = save_spy
    print(f"=== 批量注册 | 目标 {target} | 线程 {threads} | 尝试上限 {target*mult} ===", flush=True)
    print(f"    本批导出 → {txt_out} / {json_out}", flush=True)

    t0 = time.time()
    try:
        t.main(target, threads, max_attempts=target * mult)
    finally:
        dt = time.time() - t0
        print(f"\n=== 批次导出 ===", flush=True)
        print(f"  有效 key → {txt_out}（{n_valid[0]} 条）", flush=True)
        print(f"  全量记录 → {json_out}（{n_all[0]} 条）", flush=True)
        print(f"  本批耗时 {dt/60:.1f} 分钟（{dt/max(n_all[0],1):.0f}s / 成功注册）", flush=True)


if __name__ == "__main__":
    run()
