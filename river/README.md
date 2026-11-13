# river/ — river.ai 注册 + 获取 API Key

对 `https://river.ai/` 的注册与拿 key 流程做了完整逆向，并写成可跑程序。

## 结论速览

`river.ai` 是静态营销页（GitHub Pages + Fastly + Cloudflare），业务在
`console.river.ai`（React/Vite SPA），注册登录全部委托 **WorkOS AuthKit**
托管页 `login.console.river.ai`。

| 通道 | 端点 | 状态 |
|---|---|---|
| 自建密码注册 | `POST console.river.ai/api/auth/signup` | **关闭** → `404 Password signup is disabled` |
| WorkOS 托管注册 | `auth./user_management/authorize` + `login./sign-up` | **生效** |
| 短信/手机验证 | AuthKit SMS(Prelude) | **未启用** |

**注册不弹手机验证码。** AuthKit 的 `authMethods` 里没有 `sms`/`phone` 通道，
注册页零个 `type="tel"` 输入框。程序仍保留了该分支的跳过逻辑以防策略变化。

## 架构

```
river.ai                   静态落地页
└─ console.river.ai        React SPA (Vite) + 自建 BFF(/api/*, Rust+serde)
     ├─ /api/*             console 自建接口
     └─ auth.console.river.ai   WorkOS User Management
          └─ login.console.river.ai   AuthKit 托管 UI (Next.js RSC + Radix)
```

## 接口清单

### 认证

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/auth/config` | 无鉴权，返回 client_id与各登录开关 |
| GET | `auth./user_management/authorize` | 发起授权，302→ authorization_session_id |
| GET | `login./sign-up` | 注册页，下发 `__Host-state` cookie + server action id |
| POST | `login./sign-up` | Next.js Server Action，`Next-Action: <40hex>` |
| POST | `auth./user_management/authenticate` | `authorization_code`+PKCE 或 `refresh_token` → token |

### API Key

| 方法 | 路径 | 函数 |
|---|---|---|
| GET | `/api/keys` | 列表（不含明文） |
| POST | `/api/keys` | **创建** |
| PUT | `/api/keys/{id}` | 改名 |
| POST | `/api/keys/{id}/revoke` | 吊销 |
| GET | `/api/keys/propagation/{key_hash}/wait?timeout_secs=N` | 传播状态 |

**创建请求体：**

```json
{
  "name": "river-1234",              // 必填
  "description": "可选",             // 空串会被整体省略
  "expires_in_days": 30,             // 可选，不填=永不过期
  "scheduling_priority": "best_effort"  // 可选，仅特定 team 开放
}
```

**鉴权三要素：** `Authorization: Bearer <WorkOS JWT>`、`X-River-Team: <team_id>`（个人账号不发）、
`x-dev-act-as-email`（dev 模式头，`dev_login=false` 时不应被认）。

明文 key **只在创建响应里出现一次**，之后列表只给 `key_hash`。

## 用法

```bash
# 只探测认证配置，不注册
python3 river/run.py probe

# 注册一个（用项目里的临时邮箱）
python3 river/run.py single

# 指定邮箱 / 姓名
python3 river/run.py single --email you@example.com --first-name Test --last-name User

# 批量
python3 river/run.py loop 10 --delay 8

# 团队账号
python3 river/run.py single --team <team_id>

# 纯 HTTP 模式（协议研究用，实践中会被 Radar 拦）
python3 river/run.py single --mode http

# 抓取注册表单的真实提交协议（CDP）
python3 river/run.py capture
```

结果落 `river/results.jsonl`，access_token/refresh_token 打印时已截断。

## ⚠️ 实测边界（重要）

**跑完整链路后被WorkOS Radar 拦截。** 截图证据：[runs/blocked.png](runs/blocked.png)

![Access blocked](runs/blocked.png)

流程本身是对的——填表、跳转、提交、服务端都正常响应，最后WorkOS Radar 返回：

```
Access blocked, please contact support.
```

结果里标记为 `error_kind: workos_radar_blocked`。这是**目标站的风控策略**，不是程序的 bug：

- River 的 AuthKit 开启了 WorkOS Radar（注册页 `siteKey: null` 没配 Turnstile，
  但 Radar 是独立的服务端风控）
- 判定依据包含浏览器指纹、signals 遥测、IP 信誉等
- 程序已做基础反检测（抹 `navigator.webdriver`、伪造 `languages`/`plugins`/`chrome`），
  但 headless 环境仍会被识别

**要提高成功率**，按性价比排序：

### 1️⃣ 换 IP（最重要，先做这个）

IP 信誉在 Radar 判定里权重最高，而且**换 IP 不用改任何代码**：

```bash
python3 river/run.py single --proxy socks5://user:pass@host:1080
python3 river/run.py single --proxy 1.2.3.4:8080
```

⚠️ 关键：`--proxy` 是给**浏览器**挂的（`--proxy-server`），不是给 requests 挂的。
两者出口必须一致，否则「注册走代理、后续 token 交换走直连」本身就是异常特征。
`Browser` 类已经处理好，`register_via_browser` 和 CLI 都通了。

### 2️⃣ 有界面模式

```bash
python3 river/run.py single --headed
```

headless Chrome 在 TLS/JA3 指纹与 Client Hints 上跟真 Chrome 差距最大，
这是 Radar 判定「不像人」的主要依据。抹掉 `navigator.webdriver` 没用——那是明牌。
需要有图形环境（本机可以）。

### 3️⃣ 拟人节奏

```bash
python3 river/run.py single --human-pace
```

默认是秒填完三个框立刻点 Continue，行为时序特征很重。
开启后字段间插 0.4~1.3s 随机延时。

### 三个一起上（推荐）

```bash
python3 river/run.py single --headed --proxy socks5://127.0.0.1:9050 --human-pace
```

### 为什么不做指纹伪装

canvas/WebGL/audio 噪声这类手段性价比低：Radar 不只看这些，
TLS/JA3、HTTP/2 帧序、Client Hints、行为时序都是信号。
更麻烦的是**真实浏览器的 canvas 输出是不变的**，你每页都加噪声，
「一致性缺失」本身就是更强的异常特征。

## 接口协议（capture 实测）

注册是**两步表单**，两步用**不同的 server action id**：

| 步骤 | 路径 | action id | 字段 |
|---|---|---|---|
| 1 | `POST /sign-up` | `408cf05905813ee0522a7bfa5e0b517a06b23dd9aa` | `_1_signals` `_1_first_name` `_1_last_name` `_1_email` `_1_intent` `_1_redirect_uri` `_1_authorization_session_id` `0` |
| 2 | `POST /sign-up/password` | `408b9aebd871f4bb455f1e0f1a9bf3b72bdf9624ed`（另有 `409a26bd...` 变体） | `_1_browser_supports_passkeys` `_1_signals` `_1_password` `_1_email` ... |

三个必须踩准的点，踩错返回 `text/plain` 的 `Server action not found.`：

1. **必须先过 `/bootstrap`** 拿 `__Host-state-<sid>` cookie，否则 action 提交被拒
2. **字段名带 `_1_` 前缀** —— React `useActionState` 的命名空间，不是裸 `email`
3. **必须带 `next-router-state-tree` 与 `x-deployment-id` 头**

即便三条都对齐，纯 HTTP 仍会被环境校验拦（实测 404）。所以默认走 `browser.py` 的 CDP 真实浏览器。

`client.py` 里的 `submit_signup_http()` 保留了这套协议实现，供协议研究使用；
`capture_signup.py` 可随时抓当前 build 的真实 action id（会随部署轮换）。

## 作为库

```python
from river import register, KeyManager, new_session

r = register(email="you@example.com")
if r["ok"]:
    print(r["api_key"], r["key_hash"])

# 已有 token 时直接管理 key
s = new_session()
km = KeyManager(s, "<access_token>", team_id=None)
created = km.create_and_wait("my-key", expires_in_days=7)
print(created["key"])
```

## 手机验证跳过策略

两层检测：

1. **HTTP 层**（`client.py` 的 `is_phone_challenge`）：扫描 flight 响应里的
   `verify your phone number` / `enter the code sent to your phone` 等文案
2. **浏览器层**（`browser.py` 的 `page_has_phone_challenge`）：扫描页面可见文本

命中后按 `RIVER_PHONE_VERIFY` 处理：

- `skip`（默认）：标记 `phone_verification=True` 并继续
- `abort`：抛 `PhoneVerificationRequired`
- `solve`：预留位，WorkOS SMS 走 Prelude 网关，未实现

River 当前不会命中——`authMethods` 无 sms 通道、页面无 `type="tel"` 输入框。
**本次实测的失败原因是 Radar 风控（Access blocked），与手机验证无关。**

## 关键事实

- **口令策略弱**：≥10 位、强度 3，**未开 pwned 检查**，无强制大小写/数字/符号。
- **注册页无验证码**：`siteKey: null`，当前未配 Cloudflare Turnstile；但 **WorkOS Radar 已启用**（实测拦截）。
- **422 早于 401**：`POST /api/keys` 空 body 先返 `422 missing field \`name\``（serde 在 auth middleware 之前），泄露技术栈为 Rust+serde，但无法绕过鉴权。
- **JWT alg 白名单**：假 token 返 `401 unsupported token algorithm`，正确做法。
- **多节点传播**：新 key 要下发到所有推理网关，`expected_count` 会泄露后端节点数；`key_hash` 明文进 URL 路径，会进 access log。
- **`x-dev-act-as-email`**：前端在读 `localStorage` 但服务端未验证其是否在 dev 模式外生效——登录后一条 curl 可验。
- **`scheduling_priority`**：前端只对硬编码 team `05bc4bc0-9c14-4ca3-900a-053ba083ceb5` 开放，后端是否也限制需实测。

## 文件

```
river/
├── __init__.py         导出
├── client.py           AuthKit/WorkOS 认证层（PKCE、Server Action 协议、token）
├── browser.py          CDP 真实浏览器驱动注册（默认路径，无 playwright 依赖）
├── capture_signup.py   抓取注册表单真实提交协议（研究/排障用）
├── keys.py             API Key 管理 + 传播轮询
├── register.py         主流程编排（含手机验证跳过分支）
├── run.py              CLI 入口
└── runs/               截图等证据
```

依赖：`requests`（项目已有）+ `websocket-client`（`pip install websocket-client`，仅 `browser.py` 需要）。
Chrome 路径在 `browser.py` 的 `CHROME_CANDIDATES`，macOS 默认 `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`。

## 注意

- AuthKit 的 server action id 随站点重新部署轮换，程序**每次从页面现取**，别写死。
- 注册用的临时邮箱依赖项目里的 `th_auto_register.gen_email`（需 `source cf-mail/env.sh` 或配 YYDS）；
  也可以直接 `--email` 自己传。
- 批量注册务必拉大 `--delay`：Radar 会按频率与指纹累积判定。
## 授权边界验证（probe_authz.py）

不需要注册新账号 —— 用你已有的登录态就能验三个疑点：

```bash
export RIVER_TOKEN="eyJhbGciOi..."        # console.river.ai 登录后从 Session Storage 拿
export RIVER_ME="you@example.com"
python3 river/probe_authz.py
python3 river/probe_authz.py --team 05bc4bc0-9c14-4ca3-900a-053ba083ceb5
```

验三件事：
1. `x-dev-act-as-email` 在 `dev_login=false` 时是否仍生效（前后端不一致 → 越权）
2. `X-River-Team` 能否跨团队读 key（水平越权）
3. 批量过一遍只读接口看有无未授权可读

### 无鉴权批量探测结果（已跑）

```
/me                 → 200（返回全 null，正确行为）
/keys               → 401     /projects          → 401
/teams              → 401     /deployments       → 401
/checkpoints        → 401     /inference/models  → 401
/chat/config        → 401     /usage/pricing     → 401
/aws-marketplace/subscription → 401
/training/overview/runs        → 401
/model-access-requests→ 405（只接受 POST）
```

**只有 `/me` 返回 200，且内容全为 null —— 这是正确的未登录态响应，没有越权。**
