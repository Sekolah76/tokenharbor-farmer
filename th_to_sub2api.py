#!/usr/bin/env python3
"""th_to_sub2api.py — 把 TokenHarbor 注册结果导出成 sub2api 导入文件（sub2api-data v1）

字段结构 1:1 对齐实战模板 sub2api-account-<时间戳>.json（sub2api 真实导出）：

{
  "type": "sub2api-data", "version": 1, "exported_at": "...", "proxies": [],
  "accounts": [{
    "name": "tokenhour-101|you@example.com",
    "notes": "TokenHarbor 池模式 | 分组: tokenhour | 节点: ...",
    "platform": "openai", "type": "apikey",
    "credentials": {
      "api_key": "thk_live_xxxxxxxxxxxxxxxx",
      "base_url": "https://tokenharbor.ai",
      "model_mapping": { "deepseek-v4.1-flash": "deepseek-v4.1-flash:free",
                         "mimo-v2.6-flash": "mimo-v2.6-flash:free" }
      // 可选（默认带，--no-pool-mode 则不写）：
      // "pool_mode": true, "pool_mode_retry_count": 3
    },
    "extra": {
      "email": "...", "group": "tokenhour", "node": "...",
      "openai_apikey_responses_websockets_v2_enabled": false,
      "openai_apikey_responses_websockets_v2_mode": "off",
      "openai_long_context_billing_enabled": false,
      "provider": "tokenharbor", "register_ip": "...",
      "source": "th_direct_results.jsonl", "tokenharbor_user_id": "..."
    },
    "concurrency": 1, "priority": 50, "rate_multiplier": 1,
    "auto_pause_on_expired": true
  }]
}

注意（照模板的取值，别踩坑）：
- base_url 不带 /v1：sub2api 的 buildOpenAIEndpointURL 会自动补 /v1，写 tokenharbor.ai 即可。
- model_mapping 是【白名单 + 别名映射】：key = 客户端请求的模型名，value = 打到上游的真实模型名。
  非空时该账号只放行 mapping 里出现的模型（支持通配符）；想放行全部模型就
  ① 用 --no-model-mapping 置空，或 ② 加 --passthrough 开 extra.openai_passthrough=true。
- pool_mode=true（默认写，--no-pool-mode 删掉）：上游 401/403/429 时在同一账号重试
  （默认 3 次、默认状态码就是 401/403/429），不把中转 key 打成 error 踢出调度。
  删掉后即普通模式：上游报错会按 sub2api 默认策略标记账号状态并 failover 到别的账号。
- extra 里两个 websockets_v2 开关 = off/false，长上下文阶梯计费关闭（中转站按标准价走）。

用法:
  python3 th_to_sub2api.py                        # 默认：带模板的 model_mapping + 池模式
  python3 th_to_sub2api.py --no-pool-mode         # 去掉池模式（credentials 只剩 api_key/base_url/model_mapping）
  python3 th_to_sub2api.py --no-model-mapping     # 不设白名单（全部模型可用）
  python3 th_to_sub2api.py --passthrough          # 透传模式：认证换上、模型全交给上游
  python3 th_to_sub2api.py --model-mapping "gpt-5:gpt-5-free,glm-4.6:glm-4.6:free"
  python3 th_to_sub2api.py --include-password     # 把登录密码一并写进 extra（默认不写）
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

DEFAULT_INPUT = "th_direct_results.jsonl"
DEFAULT_OUTPUT = "sub2api-tokenhour.json"
# 模板（sub2api-account-20261014113841.json）里的账号级模型映射
DEFAULT_MODEL_MAPPING = "deepseek-v4.1-flash=deepseek-v4.1-flash:free,mimo-v2.6-flash=mimo-v2.6-flash:free"


def load_records(path):
    """支持 jsonl / json(list) / json({accounts:[...]})"""
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read().strip()
    if not text:
        return []
    if path.endswith(".jsonl") or (not path.endswith(".json") and "\n{" in text):
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    data = json.loads(text)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("accounts"), list):
        return data["accounts"]
    raise SystemExit(f"❌ 无法解析 {path}: 期望 jsonl 或 json（list / {{accounts: [...]}}）")


def parse_model_mapping(raw):
    """'k=v,k2=v2' 或 JSON dict → dict[str,str]"""
    if not raw:
        return {}
    raw = raw.strip()
    if raw.startswith("{"):
        obj = json.loads(raw)
        return {str(k): str(v) for k, v in obj.items()}
    mapping = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            raise SystemExit(f"❌ --model-mapping 片段缺少 '=': {pair}")
        k, v = pair.split("=", 1)
        mapping[k.strip()] = v.strip()
    return mapping


def slugify_email(email: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._@-" else "_" for ch in email)


def build_credentials(api_key, args, mapping):
    cred = {
        "api_key": api_key,
        "base_url": args.base_url,
    }
    if mapping:
        cred["model_mapping"] = dict(sorted(mapping.items()))
    if not args.no_pool_mode:
        # 池模式：上游 401/403/429 时在同一账号重试，不把 key 打成 error
        cred["pool_mode"] = True
        cred["pool_mode_retry_count"] = args.retry_count
    return cred


def build_extra(raw, args, email):
    extra = {
        "email": email,
        "group": args.group,
        "node": raw.get("node", ""),
        "openai_apikey_responses_websockets_v2_enabled": False,
        "openai_apikey_responses_websockets_v2_mode": "off",
        "openai_long_context_billing_enabled": False,
        "provider": "tokenharbor",
        "register_ip": raw.get("ip", ""),
        "source": os.path.basename(args.input),
        "tokenharbor_user_id": raw.get("userId", ""),
    }
    if args.passthrough:
        extra["openai_passthrough"] = True
    if args.include_password and raw.get("password"):
        extra["password"] = raw["password"]
    return {k: v for k, v in extra.items() if v not in ("", None)}


def build_accounts(records, args, mapping):
    accounts, seen_keys = [], set()
    skipped = 0
    for raw in records:
        api_key = (raw.get("api_key") or "").strip()
        email = (raw.get("email") or "").strip()
        if not api_key or api_key in seen_keys:
            skipped += 1
            continue
        seen_keys.add(api_key)

        idx = len(accounts) + 1
        accounts.append({
            "name": f"{args.prefix}{idx:03d}|{slugify_email(email) or 'no-email'}",
            "notes": f"TokenHarbor{'' if args.no_pool_mode else ' 池模式'} | 分组: {args.group} | 节点: {raw.get('node', '-')}",
            "platform": args.platform,
            "type": "apikey",
            "credentials": build_credentials(api_key, args, mapping),
            "extra": build_extra(raw, args, email),
            "concurrency": args.concurrency,
            "priority": args.priority,
            "rate_multiplier": 1,
            "auto_pause_on_expired": True,
        })
    return accounts, skipped


def build_payload(accounts, args):
    return {
        "type": "sub2api-data",
        "version": 1,
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "proxies": [],
        "accounts": accounts,
    }


def self_check(payload):
    """按 sub2api validateDataHeader / validateDataAccount 的规则本地自检"""
    errs = []
    if payload["type"] != "sub2api-data":
        errs.append("type 必须是 sub2api-data")
    if payload["version"] != 1:
        errs.append("version 必须是 1")
    if not isinstance(payload["proxies"], list):
        errs.append("proxies 必须是数组（不能省）")
    if not isinstance(payload["accounts"], list) or not payload["accounts"]:
        errs.append("accounts 必须是非空数组")

    allowed_types = {"oauth", "setup-token", "apikey", "upstream", "bedrock", "service_account"}
    names, keys = set(), set()
    for i, a in enumerate(payload["accounts"], 1):
        tag = f"accounts[{i}]({a.get('name', '?')})"
        if not str(a.get("name", "")).strip():
            errs.append(f"{tag}: name 必填")
        if len(a.get("name", "")) > 100:
            errs.append(f"{tag}: name 超过 100 字符")
        if not str(a.get("platform", "")).strip():
            errs.append(f"{tag}: platform 必填")
        if a.get("type") not in allowed_types:
            errs.append(f"{tag}: type={a.get('type')} 非法")
        cred = a.get("credentials") or {}
        if not isinstance(cred, dict) or not cred:
            errs.append(f"{tag}: credentials 必填")
        if not cred.get("api_key"):
            errs.append(f"{tag}: credentials.api_key 缺失")
        if cred.get("pool_mode") is not None and cred.get("pool_mode") is not True:
            errs.append(f"{tag}: credentials.pool_mode 存在但不是 true")
        mm = cred.get("model_mapping")
        if mm is not None and (not isinstance(mm, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in mm.items())):
            errs.append(f"{tag}: credentials.model_mapping 必须是 string→string 映射")
        if a.get("concurrency", 0) < 0 or a.get("priority", 0) < 0:
            errs.append(f"{tag}: concurrency/priority 不能为负")
        if (a.get("rate_multiplier") or 0) < 0:
            errs.append(f"{tag}: rate_multiplier 不能为负")
        if a["name"] in names:
            errs.append(f"{tag}: name 重复")
        names.add(a["name"])
        keys.add(cred.get("api_key"))
    return errs, len(keys)


def main():
    p = argparse.ArgumentParser(description="TokenHarbor 注册结果 -> sub2api 导入文件（模板对齐版）")
    p.add_argument("-i", "--input", default=DEFAULT_INPUT, help=f"输入文件（默认 {DEFAULT_INPUT}）")
    p.add_argument("-o", "--output", default=DEFAULT_OUTPUT, help=f"输出文件（默认 {DEFAULT_OUTPUT}）")
    p.add_argument("--base-url", default="https://tokenharbor.ai", help="TokenHarbor API base（模板值 https://tokenharbor.ai，不带 /v1）")
    p.add_argument("--platform", default="openai", help="sub2api 平台标识（默认 openai）")
    p.add_argument("--group", default="tokenhour", help="池模式分组名（写进 notes/extra，默认 tokenhour）")
    p.add_argument("--prefix", default=None, help="账号名前缀（默认 <group>-）")
    p.add_argument("--concurrency", type=int, default=1, help="账号并发（tokenhour 账号统一 1）")
    p.add_argument("--priority", type=int, default=50, help="优先级，数值越小越优先（模板值 50）")
    p.add_argument("--retry-count", type=int, default=3, help="池模式同账号重试次数 0-10（模板值 3）")
    p.add_argument("--no-pool-mode", action="store_true", help="删掉池模式（不写 credentials.pool_mode / pool_mode_retry_count）")
    p.add_argument("--model-mapping", default=DEFAULT_MODEL_MAPPING, help=f"账号级模型映射 '请求模型=上游模型,...'（模板值：{DEFAULT_MODEL_MAPPING}）")
    p.add_argument("--no-model-mapping", action="store_true", help="置空 model_mapping（账号对所有模型放行）")
    p.add_argument("--passthrough", action="store_true", help="extra.openai_passthrough=true（透传模式，模型白名单失效）")
    p.add_argument("--include-password", action="store_true", help="把登录密码写进 extra（默认不写）")
    args = p.parse_args()
    if args.prefix is None:
        args.prefix = f"{args.group}-"
    args.retry_count = max(0, min(10, args.retry_count))

    here = os.path.dirname(os.path.abspath(__file__))
    args.input = args.input if os.path.isabs(args.input) else os.path.join(here, args.input)
    out_path = args.output if os.path.isabs(args.output) else os.path.join(here, args.output)

    if not os.path.exists(args.input):
        print(f"❌ 输入文件不存在: {args.input}")
        return 1

    mapping = {} if args.no_model_mapping else parse_model_mapping(args.model_mapping)
    records = load_records(args.input)
    accounts, skipped = build_accounts(records, args, mapping)
    if not accounts:
        print("❌ 没有可用账号（全部缺 api_key）")
        return 1

    payload = build_payload(accounts, args)
    errs, uniq_keys = self_check(payload)
    if errs:
        print("❌ 自检失败（按 sub2api 导入校验规则）：")
        for e in errs[:20]:
            print("   -", e)
        return 1

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print(f"✅ 生成 sub2api 导入文件: {out_path}")
    print(f"   账号: {len(accounts)} 条（唯一 api_key {uniq_keys} 个，跳过 {skipped} 条）")
    print(f"   平台/类型: {args.platform} / apikey | 上游: {args.base_url}")
    if args.no_pool_mode:
        print("   池模式: 已删除（credentials 不含 pool_mode / pool_mode_retry_count）")
    else:
        print(f"   池模式: pool_mode=true, retry={args.retry_count}（状态码用默认 401/403/429）")
    if mapping:
        print(f"   模型白名单(映射到上游): {json.dumps(mapping, ensure_ascii=False)}")
    else:
        print("   模型白名单: 无（全部模型放行）" + ("，透传模式已开" if args.passthrough else ""))
    print(f"   分组标记: {args.group}（notes / extra.group，导入后用 sub2api_push.py 绑定）")
    print()
    print("下一步（二选一）：")
    print("  A. 后台导入：账号管理 → 导入数据 → 选该 JSON；再搜 '{}' 全选 → 批量编辑 → 分组选 {}".format(args.prefix, args.group))
    print("  B. 一条龙：python3 sub2api_push.py --base-url https://你的sub2api --email admin@x --password xxx")
    return 0


if __name__ == "__main__":
    sys.exit(main())
