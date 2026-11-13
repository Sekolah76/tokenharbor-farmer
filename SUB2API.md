# TokenHarbor → sub2api 导入（分组 tokenhour）

> 字段结构 1:1 对齐实战模板 `sub2api-account-20261014113841.json`（sub2api 真实导出）。
> **当前 `sub2api-tokenhour.json` 已按需求删掉池模式**：`credentials` 只剩 `api_key / base_url / model_mapping`。
> 池模式在生成器里是可选开关——默认会写，`--no-pool-mode` 删除（本次就是这么导出的）。

## 产物

| 文件 | 作用 |
| --- | --- |
| `sub2api-tokenhour.json` | **101 个 TokenHarbor 账号**的 sub2api 导入文件（`sub2api-data` v1，无池模式） |
| `th_to_sub2api.py` | 生成器：注册结果（`th_direct_results.jsonl`）→ 导入文件，新注册完重跑一次 |
| `sub2api_push.py` | 一条龙：登录 → 建组/找组 `tokenhour` → 导入 → 把这批账号绑定到该分组 |

## 导入文件格式（对齐 Wei-Shaw/sub2api 的 `/api/v1/admin/accounts/data` + 实战模板）

```json
{
  "type": "sub2api-data",
  "version": 1,
  "exported_at": "2026-10-14T08:5x:xxZ",
  "proxies": [],
  "accounts": [
    {
      "name": "tokenhour-001|user@example.com",
      "notes": "TokenHarbor | 分组: tokenhour | 节点: ...",
      "platform": "openai",
      "type": "apikey",
      "credentials": {
        "api_key": "thk_live_xxxxxxxxxxxxxxxx",
        "base_url": "https://tokenharbor.ai",
        "model_mapping": {
          "deepseek-v4.1-flash": "deepseek-v4.1-flash:free",
          "mimo-v2.6-flash": "mimo-v2.6-flash:free"
        }
      },
      "extra": {
        "email": "...", "group": "tokenhour", "node": "...",
        "openai_apikey_responses_websockets_v2_enabled": false,
        "openai_apikey_responses_websockets_v2_mode": "off",
        "openai_long_context_billing_enabled": false,
        "provider": "tokenharbor", "register_ip": "...",
        "source": "th_direct_results.jsonl", "tokenharbor_user_id": "..."
      },
      "concurrency": 10,
      "priority": 50,
      "rate_multiplier": 1,
      "auto_pause_on_expired": true
    }
  ]
}
```

要点：

- **池模式（当前已删）**：原版是 `credentials.pool_mode: true` + `pool_mode_retry_count: 3`——上游 401/403/429 时在**同一个账号**重试、不把中转 key 标成 `error` 踢出调度。删掉后走 sub2api 默认策略（按错误码标记账号状态 + failover 到别的账号）。要恢复：`python3 th_to_sub2api.py`（默认就带），要删：`--no-pool-mode`。
- **`base_url` 不带 `/v1`**（模板取值）：sub2api 的 `buildOpenAIEndpointURL` 自动补 → `https://tokenharbor.ai/v1/chat/completions`。
- **`model_mapping` 是「白名单 + 别名」**：key = 客户端请求的模型名，value = 打到上游的真实模型名。**非空即白名单**——账号只放行 mapping 里出现的模型（支持通配符），模板里就是两个免费模型别名。想放开全部模型：
  - `--no-model-mapping` 置空；或
  - `--passthrough` 写 `extra.openai_passthrough: true`（透传，认证换上、模型交给上游）。
- **websockets v2 关闭** + **长上下文阶梯计费关闭**：中转站按标准价、走 HTTP/SSE 更稳。
- **分组不在导入文件里**：官方 `sub2api-data` v1 的账号结构没有分组字段（导入侧 `GroupIDs` 恒为 nil），分组名写在 `notes` / `extra.group` 做标记，导入后再绑定（见下）。

## 用法

### A. 后台导入 + 手工分组

1. 后台 → 账号管理 → **导入数据** → 选 `sub2api-tokenhour.json`（跳过默认分组绑定）。
2. 账号列表搜索 `tokenhour-` → 全选 → 批量编辑 → 分组选 `tokenhour`。

### B. 一条龙脚本（推荐）

```bash
# 邮箱密码登录
python3 sub2api_push.py --base-url https://your-sub2api.example.com \
    --email admin@example.com --password 'xxx'

# 或直接粘贴浏览器里的 access_token
python3 sub2api_push.py --base-url https://your-sub2api.example.com --token eyJhbGci...

# 已经手工导入过，只补分组绑定
python3 sub2api_push.py --base-url ... --token ... --skip-import

# 干跑（只打印请求，不写数据）
python3 sub2api_push.py --base-url ... --token ... --dry-run
```

脚本做的事：`POST /api/v1/auth/login` → `GET/POST /api/v1/admin/groups`（确保 `tokenhour` 分组存在，platform=openai）→ `POST /api/v1/admin/accounts/data`（导入）→ 按 `name` 前缀搜账号 → `POST /api/v1/admin/accounts/bulk-update`（`group_ids=[tokenhour]`, `confirm_mixed_channel_risk=true`）→ 复核分组内账号数。

## 重新生成（有新号 / 想改口径时）

```bash
python3 th_to_sub2api.py                       # 默认：池模式 + 模板 model_mapping
python3 th_to_sub2api.py --no-pool-mode        # 删池模式（当前文件就是这么导的）
python3 th_to_sub2api.py --no-model-mapping    # 不设模型白名单，全部模型放行
python3 th_to_sub2api.py --passthrough         # 透传模式
python3 th_to_sub2api.py --model-mapping "gpt-5:gpt-5-free,glm-4.6:glm-4.6:free"
python3 th_to_sub2api.py --include-password    # 需要的话把登录密码也写进 extra
python3 th_to_sub2api.py --concurrency 20 --priority 10
```

生成器内置与 sub2api 导入校验一致的自检（name/platform/type/credentials/映射类型/去重），不过就报错不落盘。
