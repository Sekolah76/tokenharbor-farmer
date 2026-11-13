# cf-mail — 自建收信服务（Cloudflare Email Routing + Worker + KV）

解决什么问题
------------
现在每次注册尝试都要**预先创建一个临时邮箱**。YYDS Mail 和 tempmail.lol 的建箱接口都有配额/节流，
跑到 200+ 次尝试就被打成 `Too many account creation requests`，整个流程被卡住。

Cloudflare 的 **catch-all** 没有这个概念：`<任意前缀>@你的域名` 天然就能收信，**不需要预先建箱**，
所以邮箱不再成为瓶颈。

架构
----
```
外部来信
   ↓  MX: route1/2/3.mx.cloudflare.net
Cloudflare Email Routing
   ↓  Catch-all 规则
cf-mail Worker 的 email()  ──→  KV 存一封（含正文，保留 24h）
                                   ↑
我们的脚本 ──── GET /inbox?to=xxx ─┘   （带 X-Mail-Token）
```

前置条件
--------
- 一个托管在 Cloudflare 的域名（本文用 `215.im`）
- 该域名的 Email Routing 已启用（MX 指向 `route1/2/3.mx.cloudflare.net`）
- Node 环境 + wrangler：`npm i -g wrangler`
- **不要**给这个域名设自己的 MX（会和 Email Routing 冲突）—— 比如 `edu.jstyo.com` 的 MX 指向
  `smtp.215.im`，那类域名走的是你自己的邮件服务器，不能用 Email Routing

部署步骤
--------

### 方式 A：一条命令（不用装 wrangler）

先建一个 **Cloudflare API Token**：Dashboard → 右上头像 → **My Profile → API Tokens →
Create Token → Create Custom Token**，权限选：

| 范围 | 权限 |
|---|---|
| Account | Workers Scripts → **Edit** |
| Account | Workers KV Storage → **Edit** |
| Zone | Email Routing Rules → **Edit** |
| Zone | Zone → **Read** |

- **Account Resources**: 包含你的账号
- **Zone Resources**: 包含目标域名（如 `215.im`）

> ⚠️ Cloudflare API Token 是**约 40 位、无前缀**的字符串。
> 如果你拿到的是 `cfk_...` 之类带前缀的，那不是 API Token，认证会返回
> `1000 Invalid API Token`。

然后：

```bash
cd tokenharbor-farmer
CF_TOKEN=<你的_api_token> python3 cf-mail/deploy.py --domain 215.im
```

脚本会自动完成：校验 token → 找 zone/account → 建 KV namespace → 上传 Worker
（含 KV 绑定和 `MAIL_TOKEN` secret）→ 把 Email Routing 的 catch-all 指向该 Worker →
打印出三个环境变量和自测命令。

### 方式 B：用 wrangler

```bash
cd cf-mail
wrangler login

# 1. 建 KV
wrangler kv namespace create MAIL
#   → 复制输出的 id，填进 wrangler.toml 的 [[kv_namespaces]]

# 2. 设读信口令（脚本要用同一个）
wrangler secret put MAIL_TOKEN
#   输入一串随机强口令

# 3. 部署
wrangler deploy
#   → 拿到 https://cf-mail.<你的账号>.workers.dev
```

然后在 **Cloudflare 控制台 → Email → Email Routing**：
1. **Settings** 里启用 **Email Workers**
2. **Routes** 里把 **Catch-all** 的目标改成这个 Worker
3. 保持 MX 记录不变（`route1/2/3.mx.cloudflare.net`）

自测
----
```bash
BASE=https://cf-mail.<你的账号>.workers.dev
TOK=<你的 MAIL_TOKEN>

curl -s "$BASE/health"

# 从任意邮箱发一封信给 you@example.com，然后：
curl -s -H "X-Mail-Token: $TOK" "$BASE/inbox?to=you@example.com"

# 只想抓验证链接（脚本就是这么用的）
curl -s -H "X-Mail-Token: $TOK" \
  "$BASE/inbox?to=you@example.com&grep=verify-email"
```

接口
----
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 存活检查（无需口令） |
| GET | `/inbox?to=&grep=&limit=` | 列信，最新在前；`grep` 过滤正文含该串的信 |
| GET | `/message?id=` | 取单封全文（含 MIME 原文） |
| DELETE | `/inbox?to=` | 清空某地址 |

认证：请求头 `X-Mail-Token: <MAIL_TOKEN>`，或 query `?token=`。

接入脚本
--------
在项目根目录设：

```bash
export CF_MAIL_BASE="https://cf-mail.<你的账号>.workers.dev"
export CF_MAIL_TOKEN="<你的 MAIL_TOKEN>"
export CF_MAIL_DOMAIN="215.im"        # 用哪个域名发地址
```

然后 `th_auto_register.py` 的 `gen_email()` 会优先用这个 provider，
失败再回退 YYDS Mail → tempmail.lol。

免费额度
--------
| 项目 | 免费额度 | 我们的用量 |
|---|---|---|
| Workers 请求 | 10 万/天 | 每次注册 ~2 次读，绰绰有余 |
| KV 读 | 10 万/天 | 同上 |
| KV 写 | 1000/天 | 每账号 1-2 封信 → 约 500+ 账号/天 |

注意事项
--------
1. **域名信誉** —— tokenharbor 会对某些域名**静默丢弃**邮件（我们实测 `lowdn.com` 全部收不到、
   `nexushint.com` 全部收到）。自建域名必须**实测**能不能收到验证邮件，不能想当然。
   脚本里的 `yyds_bad_domains.json` 机制同样适用。
2. **Email Routing 转发上限** —— 如果 Catch-all 是"转发到某个邮箱"而不是"送到 Worker"，
   免费档有转发次数限制。送到 Worker 不受这个限制。
3. **别和自有 MX 冲突** —— 一个域名要么用 Cloudflare Email Routing，要么用你自己的
   `smtp.215.im`，不能同时。
4. **邮件保留 24h** 后自动过期（`TTL` 常量），避免 KV 无限堆积。
