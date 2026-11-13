#!/usr/bin/env python3
"""add_capacity_rule.py — 给 tokenhour 账号批量追加「模型容量满 → 模型级冷却」规则。

规则含义（对应报错）：
  "This model is at capacity for your account right now. Please retry in about 30 seconds."
  → 命中后只冷却**当前模型**（scope=model），不影响同账号其它模型。

⚠️ 为什么是 60 秒而不是 40 秒（源码硬限制，改不了）：
  sub2api 的 TempUnschedulableRule 只有 `duration_minutes int`（整数分钟），
  且 GetTempUnschedulableRules() 里：
      if rule.ErrorCode <= 0 || rule.DurationMinutes <= 0 || len(rule.Keywords) == 0 { continue }
  → duration_minutes 传 0 会被**直接丢弃**，所以最小可用值是 1（60 秒）。
    报错文案本身说 "retry in about 30 seconds"，60 秒足够覆盖。

⚠️ 大坑：PUT /api/v1/accounts/{id} 的 credentials 是**替换语义**。
   传 api_key: None 会把 key 清空（credentials_status 变 null）。
   必须从导入包/本地 key 文件取明文一并写全。

用法:
  .venv/bin/python add_capacity_rule.py --dry-run        # 只看会改几个
  .venv/bin/python add_capacity_rule.py                  # 真改
  .venv/bin/python add_capacity_rule.py --duration 2     # 想更保守就 2 分钟
"""
import argparse
import glob
import json
import re
import sys
import time

sys.path.insert(0, "/Users/zxc/tokenharbor-farmer")
from ss2a_client import api

GROUP_ID = 4687
BASE_URL = "https://tokenharbor.ai"
MAPPING = {"deepseek-v4.1-flash": "deepseek-v4.1-flash:free",
           "mimo-v2.6-flash": "mimo-v2.6-flash:free"}
CAPACITY_KEYWORD = "at capacity for your account"


def retry(fn, n=4, delay=1.5):
    last = None
    for i in range(n):
        try:
            return fn()
        except Exception as e:
            last = e
            time.sleep(delay * (i + 1))
    raise last


def fetch_all(prefix="tokenhour"):
    """拉全量账号，条数不足 total 就重试（接口会偶发返回不全）。"""
    got = []
    for attempt in range(8):
        st, js = api("GET", "/api/v1/accounts",
                     params={"search": prefix, "page": 1, "page_size": 900})
        d = js.get("data") or {}
        items = d.get("items") or []
        total = d.get("total") or 0
        got = [a for a in items if (a.get("name") or "").startswith(prefix)]
        if len(got) >= total > 0:
            return got
        time.sleep(2 + attempt * 2)
    return got


def build_keymap():
    """email → api_key 明文，来源：本地 key 文件 + 导入包。"""
    km = {}
    for path in glob.glob("th_keys_*.txt"):
        try:
            for line in open(path, encoding="utf-8"):
                p = line.rstrip("\n").split("\t")
                if len(p) >= 3 and p[2].startswith("thk_"):
                    km[p[0]] = p[2]
        except Exception:
            pass
    for path in glob.glob("runs/import_*.json"):
        try:
            doc = json.load(open(path, encoding="utf-8"))
            for a in (doc.get("data") or {}).get("accounts") or []:
                em = (a.get("extra") or {}).get("email")
                key = (a.get("credentials") or {}).get("api_key")
                if em and key and key.startswith("thk_"):
                    km.setdefault(em, key)
        except Exception:
            pass
    return km


def rules_for(duration):
    return [
        {"error_code": 429, "keywords": ["free_tier_limit_reached"],
         "duration_minutes": 10080, "scope": "account",
         "description": "免费额度用尽(滚动7天) → 停7天"},
        {"error_code": 429, "keywords": [CAPACITY_KEYWORD],
         "duration_minutes": duration, "scope": "model",
         "description": f"模型容量满 → 该模型冷却{duration}分钟"},
    ]


def has_rule(account):
    rules = ((account.get("credentials") or {}).get("temp_unschedulable_rules") or [])
    for r in rules:
        if CAPACITY_KEYWORD in (r.get("keywords") or []):
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=int, default=1,
                    help="冷却分钟数（最小 1 = 60 秒；0 会被服务端丢弃）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only-missing", action="store_true", default=True,
                    help="只改还没有这条规则的账号（默认开）")
    ap.add_argument("--no-snapshot", action="store_true",
                    help="跳过改前快照（不建议：出错就没法回退）")
    A = ap.parse_args()

    accs = fetch_all()
    print(f"tokenhour 账号 {len(accs)} 个")
    todo = [a for a in accs if not (A.only_missing and has_rule(a))]
    print(f"需要加规则的 {len(todo)} 个（已有规则的 {len(accs)-len(todo)} 个跳过）")
    if not todo:
        print("无需操作")
        return

    km = build_keymap()
    print(f"本地可用明文 key: {len(km)} 条")

    missing_key = [a for a in todo
                   if ((a.get("extra") or {}).get("email") not in km)]
    if missing_key:
        print(f"ℹ {len(missing_key)} 个账号没有本地明文 key —— 走「省略 api_key」模式")
        print("   （实测 PUT 不传 api_key 时系统会保留原值，has_api_key 仍为 true）")

    if A.dry_run:
        for a in todo[:3]:
            print("  将改:", a.get("name"))
        print("干跑结束")
        return

    # ★ 铁律：真改之前自动拍快照（--no-snapshot 可跳过，但不建议）
    if not A.no_snapshot:
        try:
            import subprocess as _sp
            label = f"before_capacity_{time.strftime('%H%M%S')}"
            r = _sp.run([sys.executable, "ss2a_snapshot.py",
                         "--prefix", "tokenhour", "--label", label],
                        capture_output=True, text=True, timeout=600)
            for line in (r.stdout or "").strip().splitlines():
                if "快照 →" in line or "✗" in line or "⚠" in line:
                    print("  " + line.strip())
            if r.returncode != 0:
                print("✗ 快照失败，拒绝继续改（避免无法回退）")
                return
        except Exception as e:
            print(f"✗ 快照异常 {type(e).__name__}: {str(e)[:80]} —— 拒绝继续改")
            return
    else:
        print("⚠ 已跳过快照（--no-snapshot）")

    rules = rules_for(A.duration)
    ok = skip = fail = 0
    t0 = time.time()
    for i, a in enumerate(todo, 1):
        em = (a.get("extra") or {}).get("email")
        key = km.get(em)
        c = a.get("credentials") or {}
        cred = {
            "base_url": c.get("base_url") or BASE_URL,
            "model_mapping": c.get("model_mapping") or MAPPING,
            "temp_unschedulable_enabled": True,
            "temp_unschedulable_rules": rules,
        }
        if key:
            cred["api_key"] = key          # 有明文就写全
        # 没有明文就省略 —— 系统会保留原 key（已实测验证）
        body = {"credentials": cred}
        try:
            st, _ = retry(lambda: api("PUT", f"/api/v1/accounts/{a['id']}", body=body))
            if st == 200:
                ok += 1
            else:
                fail += 1
                print(f"  ✗ {a.get('name')}: HTTP {st}")
        except Exception as e:
            fail += 1
            print(f"  ✗ {a.get('name')}: {str(e)[:70]}")
        if i % 40 == 0:
            print(f"  ... {i}/{len(todo)} 成功 {ok} 失败 {fail} ({time.time()-t0:.0f}s)",
                  flush=True)

    print(f"\n改规则: 成功 {ok} | 跳过 {skip} | 失败 {fail} | {time.time()-t0:.0f}s")

    # 复核
    time.sleep(2)
    final = fetch_all()
    with_rule = sum(1 for a in final if has_rule(a))
    no_key = sum(1 for a in final
                 if not (a.get("credentials_status") or {}).get("has_api_key"))
    print(f"复核: {len(final)} 个账号，带容量规则 {with_rule} 个，缺 key {no_key} 个")
    if no_key:
        print("⚠ 有账号丢了 key，用 fix 脚本补：")


if __name__ == "__main__":
    main()
