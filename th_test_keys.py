#!/usr/bin/env python3
"""th_test_keys.py — 批量测试 TokenHarbor key 是否可用（带断点续跑）

默认对每个 key 真打一次最小推理请求：POST /v1/chat/completions
（model 默认 mimo-v2.5:free，max_tokens=1，免费模型零成本）+ 顺带 GET /v1/models 探鉴权。

特性：
- 进度实时落盘（<out>.jsonl，每完成一条 append），中途 Ctrl-C / 中断都不丢
- --resume 跳过进度文件里已判 ok 的 key，接着上次继续
- 网络层异常自动重试（默认 2 次，间隔 3s）——高并发下 SSLError 属站点/链路抖动，不是 key 问题

出口地区注意：tokenharbor 对 /v1 做地区封锁，港/澳/大陆出口返回 403 region_blocked，
那不是 key 失效——脚本会把 region_blocked 单独归类。

用法:
  python3 th_test_keys.py                                  # 并发 6，全部真打
  python3 th_test_keys.py --workers 3 --resume             # 低并发续跑（推荐网络抖动时）
  python3 th_test_keys.py --models-only                    # 只探 /v1/models（最快）
  python3 th_test_keys.py -i th_direct_results.jsonl
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests

DEFAULT_INPUT = "sub2api-tokenhour.json"


def load_keys(path):
    """支持 sub2api 导入文件 / jsonl / th_keys.txt"""
    if path.endswith(".txt"):
        out = []
        for line in open(path, encoding="utf-8"):
            parts = line.strip().split("\t")
            if len(parts) >= 3:
                out.append({"email": parts[0], "api_key": parts[2]})
        return out
    if path.endswith(".jsonl"):
        rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
        return [{"email": r.get("email", ""), "api_key": r.get("api_key", "")} for r in rows if r.get("api_key")]
    doc = json.load(open(path, encoding="utf-8"))
    accts = doc["accounts"] if isinstance(doc, dict) else doc
    out = []
    for a in accts:
        cred = a.get("credentials") or {}
        key = cred.get("api_key") or a.get("api_key")
        if key:
            out.append({"email": (a.get("extra") or {}).get("email") or a.get("email", ""), "api_key": key})
    return out


def classify(http_status, body, exc=None):
    """把一次请求判定成可读的结论"""
    if exc is not None:
        return "network_error", f"ERR {type(exc).__name__}: {str(exc)[:80]}"
    err = {}
    if isinstance(body, dict):
        err = body.get("error") or {}
        if not isinstance(err, dict):
            err = {"message": str(err)}
    code = str(err.get("code") or "").strip()
    msg = str(err.get("message") or "").strip()
    if http_status == 200:
        return "ok", "200"
    if http_status == 403 and "region" in (code + msg).lower():
        return "region_blocked", f"403 {code or 'region_blocked'}"
    if http_status in (401, 403):
        return "invalid_key", f"{http_status} {code or msg[:60]}"
    if http_status == 429:
        return "rate_limited", "429"
    if http_status == 402 or "quota" in (code + msg).lower() or "balance" in (code + msg).lower():
        return "no_quota", f"{http_status} {code or msg[:60]}"
    if http_status >= 500:
        return "upstream_error", f"{http_status} {msg[:60]}"
    return "other", f"{http_status} {code or msg[:60]}"


def call_with_retry(fn, retries, delay):
    """fn 返回 requests.Response；网络异常（含 SSL/连接重置）重试"""
    last = None
    for attempt in range(retries + 1):
        try:
            return fn(), None
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < retries:
                time.sleep(delay)
    return None, last


def json_body(resp):
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return {}


def test_one(rec, args, session):
    key = rec["api_key"]
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    out = {"email": rec.get("email", ""), "key": key, "key_tail": key[-8:]}
    started = time.time()

    r, exc = call_with_retry(lambda: session.get(f"{args.base_url}/models", headers=headers, timeout=args.timeout),
                             args.retries, 3)
    if r is None:
        out["models_status"], out["models_count"] = None, 0
        out["models_verdict"], out["models_detail"] = classify(None, None, exc)
    else:
        out["models_status"] = r.status_code
        out["models_count"] = len((json_body(r).get("data") or [])) if r.status_code == 200 else 0
        out["models_verdict"], out["models_detail"] = classify(r.status_code, json_body(r))

    if args.models_only:
        out["verdict"], out["detail"] = out["models_verdict"], out["models_detail"]
        out["elapsed"] = round(time.time() - started, 2)
        return out

    body = {}
    r, exc = call_with_retry(lambda: session.post(
        f"{args.base_url}/chat/completions",
        headers=headers,
        timeout=args.timeout,
        json={"model": args.model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1},
    ), args.retries, 3)
    if r is None:
        out["chat_status"] = None
        out["verdict"], out["detail"] = classify(None, None, exc)
    else:
        body = json_body(r)
        out["chat_status"] = r.status_code
        out["verdict"], out["detail"] = classify(r.status_code, body)
        if r.status_code == 200:
            out["chat_model"] = body.get("model", "")
            out["usage"] = body.get("usage", {})
    out["elapsed"] = round(time.time() - started, 2)
    return out


def load_progress(path):
    """进度文件 → {key: 最后一次记录}"""
    done = {}
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("key"):
                done[rec["key"]] = rec
    return done


def main():
    p = argparse.ArgumentParser(description="批量测 TokenHarbor key 可用性（可续跑）")
    p.add_argument("-i", "--input", default=DEFAULT_INPUT, help=f"key 来源（默认 {DEFAULT_INPUT}）")
    p.add_argument("--base-url", default="https://tokenharbor.ai/v1", help="API base")
    p.add_argument("--model", default="mimo-v2.5:free", help="测试用模型（默认免费模型）")
    p.add_argument("--workers", type=int, default=6, help="并发数（默认 6；网络抖动时降到 2-3）")
    p.add_argument("--timeout", type=int, default=60, help="单请求超时秒（默认 60）")
    p.add_argument("--retries", type=int, default=2, help="网络异常重试次数（默认 2）")
    p.add_argument("--models-only", action="store_true", help="只探 /v1/models，不打推理")
    p.add_argument("--progress", default=None, help="进度文件（默认 <out>.jsonl）")
    p.add_argument("--resume", action="store_true", help="跳过进度文件里已判 ok 的 key")
    p.add_argument("--out", default=None, help="结果 JSON 路径（默认 th_key_test_<时间戳>.json）")
    args = p.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    args.input = args.input if os.path.isabs(args.input) else os.path.join(here, args.input)
    if not os.path.exists(args.input):
        print(f"❌ 找不到 {args.input}")
        return 1
    records = load_keys(args.input)
    if not records:
        print("❌ 没有解析到任何 key")
        return 1

    out_path = args.out or os.path.join(here, f"th_key_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    progress_path = args.progress or (out_path + ".jsonl")

    done = load_progress(progress_path) if args.resume else {}
    skip_ok = {k for k, v in done.items() if v.get("verdict") == "ok"}
    todo = [r for r in records if r["api_key"] not in skip_ok]

    mode = "只探 /v1/models" if args.models_only else f"真打 chat/completions（model={args.model}, max_tokens=1）"
    print(f"🔍 key {len(records)} 个 | 待测 {len(todo)} 个" +
          (f" | 续跑跳过已通过 {len(skip_ok)} 个" if skip_ok else "") +
          f" | {mode} | 并发 {args.workers} | 重试 {args.retries}")
    print("-" * 74)

    results, t0, done_cnt = [], time.time(), 0
    if todo:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(test_one, rec, args, requests.Session()): rec for rec in todo}
            for fut in as_completed(futures):
                res = fut.result()
                results.append(res)
                done[res["key"]] = res
                done_cnt += 1
                mark = "✅" if res["verdict"] == "ok" else "❌"
                print(f"[{done_cnt:>3}/{len(todo)}] {mark} {res['verdict']:<14} {res['elapsed']:>5.1f}s "
                      f"{res['email'][:38]:<38} …{res['key_tail']}  {res['detail'][:40]}", flush=True)
                with open(progress_path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(res, ensure_ascii=False) + "\n")

    # 汇总：所有 key 的最新记录（含续跑带过来的旧结果）
    all_results = [done[r["api_key"]] for r in records if r["api_key"] in done]
    all_results.sort(key=lambda r: (r["verdict"] != "ok", r["email"]))
    by_verdict = {}
    for r in all_results:
        by_verdict[r["verdict"]] = by_verdict.get(r["verdict"], 0) + 1

    summary = {
        "tested_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "base_url": args.base_url,
        "mode": mode,
        "model": args.model,
        "workers": args.workers,
        "total_keys": len(records),
        "tested": len(all_results),
        "ok": by_verdict.get("ok", 0),
        "by_verdict": by_verdict,
        "elapsed_sec_this_run": round(time.time() - t0, 1),
        "results": all_results,
    }
    json.dump(summary, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print("-" * 74)
    print(f"📊 汇总: 共 {len(records)} 个 key，已测 {len(all_results)}，可用 ✅ {summary['ok']}，"
          f"异常 ❌ {len(all_results) - summary['ok']}（本轮 {summary['elapsed_sec_this_run']}s）")
    for k, v in sorted(by_verdict.items(), key=lambda kv: -kv[1]):
        print(f"   {k:<16} {v}")
    bad = [r for r in all_results if r["verdict"] != "ok"]
    if bad:
        print("\n异常明细：")
        for r in bad[:40]:
            print(f"   - {r['email'][:40]:<40} …{r['key_tail']}  {r['detail']}")
        if len(bad) > 40:
            print(f"   …… 其余 {len(bad) - 40} 条见 {out_path}")
    print(f"\n📄 结果: {out_path}\n📄 进度: {progress_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
