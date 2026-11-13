# 🏭 TokenHarbor Farmer

> **批量自动注册 TokenHarbor 账号 + 开通免费模型 + 注入 9Router / 导入 sub2api —— 2026-11 首选 1024proxy 住宅 + 真浏览器 v4 流水线（20 个号约 3 分钟），duckip 备选。**

命令行工具，用于批量/自动创建 [TokenHarbor](https://tokenharbor.ai) 账号：

1. **注册** —— 优先真浏览器点 18+ 确认（绕过 Turnstile），协议模式作回退
2. **验证邮箱** —— 自动收 Cloudflare catch-all 邮件 + 自动点验证链接
3. **开通免费模型** —— 同浏览器会话直接 `POST /api/me/privacy`
4. **创建 API key** —— 同浏览器会话直接 `POST /api/keys`（不再二次登录）
5. **测试 key** —— 调用 `mimo-v2.5:free` 实测（**可关闭**，见 `--no-test` 系脚本）
6. **注入 9Router** —— 新连接**追加**到已有节点（不删旧 key）
7. **导入 sub2api** —— 批量导入 ss2a.top 面板（分组 / 代理 / 限流规则一条龙）

**绕过 `Too many sign-ups from this network`** —— 2026-11 首选 **1024proxy 动态住宅 + 独立 SID**（每个 SID 一个美国住宅 IP）；
次选 **duckip 住宅隧道**；Tor 仅在 duckip 不可用时兜底（2026-10 起机场节点普遍切断 Tor TLS）。

---

## 📦 功能

| 功能 | 状态 |
|---|---|
| **Rich TUI 菜单（点击 1/2/3 选择）** | ✅ |
| **1024proxy 住宅 + 浏览器 v4 流水线（2026-11 首选，20 号/3 分钟）** | ✅ |
| **住宅代理农场（duckip SOCKS5 隧道，2026-10 旧首选）** | ✅ |
| **住宅农场「跳过测 key」版（`duckip_farm_notest.py`）** | ✅ |
| **sub2api 批量导入（分组 / 代理 / 429 规则 / 复核）** | ✅ |
| **出口预筛 Tor 农场（烧伤记忆 + NEWNYM + 榨干模式）** | ✅ |
| **批量包装器（每批独立 key 文件，实时落盘）** | ✅ |
| **Tor 并行农场（流隔离；机场节点封 Tor TLS 时不可用）** | ⚠️ 依赖节点 |
| **Tor 自动启停（可选，`TOR_AUTOSTART=1`）** —— 默认不主动启动 Tor | ✅ |
| 自动注册（Next.js Server Action） | ✅ |
| Tor 出口轮换（绕过限流） | ✅ |
| 自动验证邮箱 | ✅ |
| 开通免费模型 | ✅ |
| 1 账号 = 1 API key | ✅ |
| 测试 key（`mimo-v2.5:free`） | ✅ |
| 注入 9Router（追加，不删除） | ✅ |
| 崩溃续跑（state JSON） | ✅ |
| 循环 注册→登出 | ✅ |
| 批量注册 | ✅ |

---

## 🧠 可用模型（TokenHarbor）

注册完成后，账号可访问以下**免费档**模型（通过 9Router 调用：`th/<model>`）：

| 免费模型（`:free`） | 说明 |
|---|---|
| `th/deepseek-v4-flash:free` | DeepSeek V4 Flash —— 快、便宜、带推理 |
| `th/mimo-v2.5:free` | MiMo V2.5 —— 通用对话 |
| `th/qwen3.8-27b:free` | Qwen 3.8 27B —— 开源权重 |

**付费模型**（免费账号**无法**访问 —— 需要余额/套餐）：
`th/gpt-5.6-luna` · `th/gpt-5.6-sol` · `th/gpt-5.6-terra` · `th/claude-opus-5` · `th/claude-sonnet-5` · `th/claude-fable-5` · `th/gemini-3.7-flash` · `th/grok-4.6` · `th/deepseek-v4-pro` · `th/glm-5.3` · `th/kimi-k3` · `th/mimo-v2.5-pro` · `th/qwen3.8-max` · `th/th-orchestra`

---

## 📋 环境要求

| 依赖 | 说明 |
|---|---|
| Python 3.10+ | `pip install requests pysocks playwright` + `python -m playwright install chromium` |
| 1024proxy（v4 首选） | 动态住宅账号 + IP 白名单/账密；本机用 `1024proxy` skill 的 `chain_pool.py` 经 mihomo 海外出口接入 |
| Tor（仅备选） | `brew install tor`（macOS）/ `apt install tor`（Linux）/ [tor-expert-bundle](https://www.torproject.org/download/tor/)（Windows）—— **无需手动启动**，见 [Setup Tor](#3-配置-tor) |
| 9Router（可选） | 用于注入 —— 数据库路径通过环境变量 `NINE_ROUTER_DB` 指定 |
| Supabase anon（可选） | 用于登出 —— 环境变量 `TH_ANON_KEY` |

---

## 🚀 克隆与使用（终端）

### 1. 克隆仓库
```bash
git clone https://github.com/Sekolah76/tokenharbor-farmer.git
cd tokenharbor-farmer
```

### 2. 安装依赖
```bash
pip install requests pysocks
```

### 3. 配置 Tor

**Tor 自动启动默认关闭** —— 脚本不会在你不知情的时候拉起 Tor。两种模式：

| 模式 | 行为 |
|---|---|
| **默认**（不设 `TOR_AUTOSTART`） | 要求 Tor **已在运行**，脚本只用不管 —— 既不启动也不关闭它。没跑就直接报错并给出手动启动命令 |
| **`TOR_AUTOSTART=1`** | 脚本自己拉起 Tor，注册结束再关掉（独占端口、会清掉残留的旧 tor 进程，保证跑完端口一定释放） |

自动启动模式下（`TOR_AUTOSTART=1`）：

- **注册开始时启动** —— 脚本自己写 `torrc` 到 `$TMPDIR/th-tor-run/`，拉起 Tor，等 bootstrap 完成（实测 4-20 秒）
- **注册结束时关闭** —— farm 一结束就关掉 Tor，端口释放、内存归还，不留任何后台进程
- **独占端口** —— 如果 9050 被上次残留的 tor 进程占着，先清掉它再启动自己的（保证跑完端口一定释放，不会攒下多个 Tor 进程）。若占用者**不是** tor 程序，则明确报错、**不会误杀**
- **异常也会清理** —— `try/finally` + `atexit` + `SIGINT/SIGTERM` 三重保险，Ctrl-C 或被 kill 都会关掉 Tor

```bash
# 安装（macOS）
brew install tor
# 安装（Linux）
sudo apt install tor
# 安装（Windows）：下载 tor-expert-bundle，把 tor.exe 放进 PATH

# 用法 A：手动起 Tor，脚本只使用（默认）
tor -f ~/tor/torrc          # 需要 SocksPort 9050 + ControlPort 9051
python3 tor_parallel_farm.py 8 16     # 16 线程是实测甜点

# 用法 B：让脚本自己启停 Tor
TOR_AUTOSTART=1 python3 tor_parallel_farm.py 8 16
```

可选环境变量：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `TOR_AUTOSTART` | 关闭 | 设为 `1` 开启 Tor 自动启停（默认关闭，见上表） |
| `TOR_BIN` | `tor` | tor 二进制路径（自动启动模式用） |
| `TOR_UPSTREAM_PROXY` | `127.0.0.1:7890` | 自动启动模式下 Tor 经此代理出网（mihomo）。**直连 Tor 网络通常被 ISP 屏蔽** —— 如果 Tor 卡在 `Bootstrapped 5%`，就是这个代理没开。设为 `""` 表示直连 |
| `TOR_KEEP_ALIVE` | — | 自动启动模式下设为 `1` 则跑完不关 Tor（调试用） |
| `TOR_SKIP_LOGIN` | **开启** | 跳过每次尝试的 `GET /login`，省 1/3 的 Tor 请求。**实测注册仍能成功**，每账号尝试次数 25 → 18。上游代理是共享瓶颈，少一个请求就多三成尝试。设 `0` 关闭 |
| `TOR_UPSTREAM_NODE` | — | 开跑前把 mihomo 切到这个节点（Tor 的上游）。**节点选择对 Tor 延迟影响可达 38%**（实测 1.79s ~ 2.47s），而且**和节点自身的直连延迟无关** —— 用 `bench_upstream.py` 实测再填 |
| `TOR_BRIDGES` | — | obfs4 网桥行（换行分隔）。设了就走网桥。**⚠ 本机实测不可用**，见下方说明 |
| `OBFS4PROXY` | 自动解析 | obfs4proxy 路径。**必须绝对路径** —— Tor 用 `exec` 启动 PT 时不走 PATH，写裸名字会让它反复以 status 1 退出、Tor 永远卡在 2%。脚本已用 `shutil.which` 自动解析 |
| `TOR_MAX_ATTEMPTS` | `目标×40` | 尝试次数上限。默认按成功率 2.5% 估算，**低谷时段（~1.8%）会不够** —— 补少量号时尤其明显（目标 1 只有 40 次预算）。要补 1 个号建议设 `TOR_MAX_ATTEMPTS=200` |
| `TOR_SOCKS_PORT` / `TOR_CONTROL_PORT` | `9050` / `9051` | 端口冲突时修改 |

> **注意**：`th_farm_multi.py`（旧的多 Tor 方案，启 8 个 Tor 实例占用 9050-9750 端口）和 `th_tor_farm.py` 仍然保留但已**过时** —— 需要手动启 Tor 且资源占用高得多。建议只用 `tor_parallel_farm.py`：1 个 Tor 进程 + 流隔离 = 出口 IP 近乎无限。

### 4. 配置 9Router 注入（重要 —— 让 key 进到你自己的 9router）

所有 farm 出来的 key 会自动注入到**你自己的** 9router。脚本**自动探测** 9router 数据库路径，找不到再手动设置。

**A. 自动探测（无需配置，推荐）：**
```bash
# 脚本自动在这些常见位置查找 9router 数据库：
#   Windows: ~/AppData/Roaming/9router/db/data.sqlite
#   Linux/macOS: ~/.9router/db/data.sqlite
#   Docker: /app/data/data.sqlite
python db_path.py    # → 显示探测到的数据库路径
```

**B. 手动设置（9router 装在别处时）：**
```bash
# Windows (CMD):
set NINE_ROUTER_DB=C:\path\to\9router\db\data.sqlite

# Windows (PowerShell):
$env:NINE_ROUTER_DB="C:\path\to\9router\db\data.sqlite"

# Linux/macOS:
export NINE_ROUTER_DB="/path/to/9router/data.sqlite"

# 怎么找数据库路径：
#   1. 打开 9router 安装目录
#   2. 在 db/ 或 data/ 目录里找 "data.sqlite"
#   3. 或者：看 9router 设置 → 数据路径
```

**C. 手动覆盖（可选 —— 9router 需要特殊配置时）：**
```bash
# 默认已正确（自动探测 + tokenharbor.ai/v1）。仅在必要时覆盖：
export TH_PROVIDER="openai-compatible-chat-xxxx"   # 强制使用指定 provider 节点
export TH_BASE_URL="https://tokenharbor.ai/v1"     # base URL 不同时修改
export TH_PREFIX="th"                               # 模型前缀不同时修改
```

**D. 注入的工作方式（透明说明）：**
```bash
# 1. 每个账号成功后 → 在你的 9router 数据库里新建 1 条连接
# 2. 如果 9router 已有 TokenHarbor 节点 → key 合并进那个节点
# 3. 如果还没有节点 → 创建新节点（provider id 属于你自己）
# 4. 不删除任何 key/连接 —— 只做追加
# 5. 免费模型（mimo/deepseek/qwen）自动锁定到每条连接
```

**E. 验证注入成功：**
```bash
# 查看 9router 里的 TokenHarbor 连接数：
python -c "import sqlite3,db_path; c=sqlite3.connect(db_path.find_9router_db()); print('conn tokenharbor:', c.execute(\"SELECT COUNT(*) FROM providerConnections WHERE data LIKE '%tokenharbor.ai%'\").fetchone()[0])"
# 然后在 9router UI 刷新 → 看到 TokenHarbor 节点数量增加
```

### 5. 运行

```bash
# 🎨 可视化模式（推荐）—— 点击选择菜单：
python th_tui.py

# ✅ farm 前检查环境（提前发现问题）：
python th_preflight.py
```

**📟 TUI 界面（点击选择）** —— 下面是程序真实输出（TUI 本身是印尼语界面，不影响使用）：
```
🔥 Tor (9050): ✅ READY
📦 Akun tersimpan: 100

┌──────┬──────────────────────┬──────────────────────────────────────────┐
│ No   │ 操作                 │ 说明                                     │
╞══════╪══════════════════════╪══════════════════════════════════════════╡
│ 1    │ Register 1 akun      │ 注册 + 验证 + 同意 + key + 注入          │
│ 2    │ Batch Farm (N)       │ 批量 farm + 自动注入 + 续跑              │
│ 3    │ Loop register→logout │ 注册、登出、循环（N 次）                 │
│ 4    │ Test API key         │ 用免费模型测试 key                       │
│ 5    │ Enable Free Models   │ 为已有账号开通免费模型                   │
│ 6    │ List Akun            │ 查看已保存账号                           │
│ 7    │ Status 9Router       │ 查看 th 连接数 + 免费模型                │
│ 0    │ Exit                 │ 退出                                     │
└──────┴──────────────────────┴──────────────────────────────────────────┘
Pilih menu [0/1/2/3/4/5/6/7] (1): _
```

**📈 完整流程**（准备 → 预检 → farm → 结果）：见 [`WORKFLOW.md`](WORKFLOW.md)

```bash
# ⚙️ 命令行模式：

# ===== 方案 A：代理节点轮换（成功率高，适合少量账号）=====
python sweep_nodes.py 1        # 注册 1 个
python sweep_nodes.py 5 2      # 注册 5 个，2 个工作线程

# ===== 方案 B：Tor 并行农场（出口池无限，适合批量）=====
source cf-mail/env.sh                  # 自建邮箱 + 跳过 login + 优选上游
python tor_parallel_farm.py 8 16     # 目标 8 个，16 线程（实测甜点）
# 只补 1-2 个号时提高尝试预算（默认 目标×40 在低谷期不够）
TOR_MAX_ATTEMPTS=200 python tor_parallel_farm.py 1 16

# 注册 1 个账号（看流程）
python th_auto_register.py single

# 循环 注册→登出
python th_auto_register.py loop 10

# 给 9router 添加 kv customModels（让 th 模型显示出来）
python inject_th_kv.py

# （如果 qwen 免费模型没出现）给所有 th 连接加 modelLock：
python fix_th_model_locks.py

# （如果新连接在 9router UI 里不显示 —— provider 不一致）修 provider：
python fix_th_provider.py

# 📤 导出账号到 2 个文件（邮箱+密码+key / 仅 key）：
python th_export.py
```

```bash
# ===== 方案 D：1024proxy 住宅 + 真浏览器 v4 流水线（2026-11 首选）=====
# 前置：1024proxy 账号（~/Downloads/data.csv）+ 1024proxy skill 的 chain pool
# chain pool 把本地端口经 mihomo SOCKS5 转发到 1024proxy 海外网关：
#   浏览器连 127.0.0.1:18130..18169，每个端口 = 一个独立 SID / 美国住宅 IP
source cf-mail/env.sh
ACCT=$(awk -F, 'NR==2{print $1}' /Users/zxc/Downloads/data.csv)
PW=$(awk -F, 'NR==2{print $2}' /Users/zxc/Downloads/data.csv)
.venv/bin/python /Users/zxc/.dsh/skills/1024proxy/scripts/chain_pool.py 18130 40 "$ACCT" "$PW" US &
.venv/bin/python -u batch_1024_v4.py 20 6 4 18130 40

# 参数：目标 / signup 线程 / verify 线程 / 起始端口 / 端口数
# v4 = signup 同浏览器直接建 key + verify 独立线程池
# 产出：th_keys_1024v4_nt_<时间戳>.txt / .jsonl（test=untested，绝不伪造 200）
```

```bash
# ===== 方案 C：duckip 住宅农场（备选，2026-10 实测最稳）=====
# 前置：duckip 住宅代理（SOCKS5 网关 + IP 白名单）+ 本地 relay
source cf-mail/env.sh
python duckip_relay.py &              # 127.0.0.1:18080 → mihomo → proxy.duckip.net:1000

python duckip_farm.py 20 12 300        # 目标 20，12 线程，尝试上限 300（含 key 测通）
python duckip_farm_notest.py 100 12 300  # 同上但**跳过 key 复验**（用户说"不用测试key"时用）

# 第 3 个参数是「总尝试上限」，不是倍数 —— 给太小会提前收工
#   实测：上限 40 → 只出 24 个（60% 命中）；上限 300 → 正常跑满
# 产出：th_keys_duckip_nt_<时间戳>.txt / .jsonl（test=untested 如实标记，不伪造 200）

# ===== 方案 B2：出口预筛 Tor 农场（Tor 可用时的改良版）=====
python tor_screened_farm.py 100 16 6000 20
# 8 线程/16 线程均可；烧伤记忆 20 分钟内跳过被限流网段，干净出口榨干再换

# ===== 通用批量包装（任意 N 个，自动落独立文件）=====
source cf-mail/env.sh && python batch_run.py 50 16 60   # 产出 th_keys_batchN_<时间戳>.txt
python batch_run_notest.py 100 16 60                    # 同上，跳过 key 测通
```

#### 2026-11 实测：v4 流水线是当前首选

| 方案 | 出口 | 20 个号实测 | 速度 |
|---|---|---|---|
| **1024proxy + v4** ⭐ | 美国住宅，每 SID 独立 IP | **20/20（v4 主批 18 + 补 2）** | **~2.9 分钟**，6 signup + 4 verify |
| 1024proxy + v3 | 美国住宅 | 22/20（4.5 分钟） | 每号端到端 59s，verify 阻塞 signup |
| duckip + duckip_farm_notest | 全球住宅 | 110/193（57%） | 12 线程，两批约 15 分钟 |
| Tor 并行 | Tor exit | 2.7-5.6% | 机场切 Tor TLS 时归零 |

**v4 的三个关键改动**：① 注册成功后同一浏览器直接 `fetch('/api/keys')`，不再二次登录（signup+key 从 ~22s → 13.5s）；② verify 拆成独立线程池，signup 线程不等邮件；③ 1024 连接层失败（`EXC Error` / `net::` / `TimeoutError`）自动换一个新端口/SID 重试 1 次。剩下唯一大瓶颈是邮箱等待 ~29s/号。

#### 旧方案对比（A/B/C，v4 之前的口径）

| | `sweep_nodes.py`（机场节点） | `tor_parallel_farm.py`（Tor 并行） | `duckip_farm.py`（住宅代理）⭐ |
|---|---|---|---|
| 换出口 | 切节点，串行独占（3 秒/个） | 流隔离，可并发（2.2 秒拿到 8 个） | **每条连接自动换住宅 IP** |
| 出口池 | 节点列表里的固定节点 | 全球上千 exit，近乎无限 | 住宅 IP 池（按量提取，量大） |
| 成功率 | 高（专线节点基本一次成） | ~2.7-5.6%（深夜/烧池后趋近 0） | **~57%（2026-10-18 实测 110/193）** |
| 速度 | 单账号约 1 分钟 | 22-26 秒/账号（32 线程） | **12 线程约 2 个/分钟** |
| 地区封锁 | 香港/澳门被拒 | 不拒，但风控更严 | 随机国家，命中 CN/HK/MO 自动重试 |
| 前置条件 | 机场订阅可用 | 机场不封 Tor TLS | duckip 住宅套餐 + 白名单 |
| 适合 | 要 1-3 个号 | 机场友好时批量 | **要 10 个以上，首选** |

> 2026-10-17/18 实测：Tor 路线被机场出口整段切断 TLS（`TLS eof` 刷屏、卡 Bootstrapped 50%），
> 机房代理池被 Cloudflare 整段拉黑，**住宅代理是唯一稳定出号通道**。
> 2026-10-18 用 duckip 跑 110 个号：193 次尝试出 110 个有效（57%），失败几乎全是出口短窗口限流。

各方案产出分别写进 `th_keys_1024v4_nt_*` / `th_keys.txt` / `th_keys_batch*` / `th_keys_duckip_*`；
多批合并用 `th_keys_20261112_final.txt`（按 key 去重）。

### 6. 验证

```bash
# 测试单个 key（换成你的 key）
curl https://tokenharbor.ai/v1/chat/completions \
  -H "Authorization: Bearer thk_live_..." \
  -d '{"model":"mimo-v2.5:free","messages":[{"role":"user","content":"hi"}]}'

# 或通过 9Router（注入后）
curl http://127.0.0.1:20128/v1/chat/completions \
  -H "Authorization: Bearer <9ROUTER_KEY>" \
  -d '{"model":"th/deepseek-v4-flash:free","messages":[{"role":"user","content":"hi"}]}'
```

> ⚠️ **验证时出口 IP 必须在支持的地区** —— tokenharbor 对 `/v1` 接口也做地区封锁，从香港/澳门/中国大陆出口访问会返回 `403 region_blocked`，那不是 key 失效。

---

## 📥 导入 sub2api（ss2a.top）

注册出来的号可以直接灌进 sub2api 面板，脚本会把「分组 / 代理 / 限流规则 / 模型映射」一次配完。

### 1. 凭据（一次性）

面板登录接口带 Cloudflare Turnstile，脚本登不进去。正确姿势：从浏览器 localStorage 取一次
`auth_token` + `refresh_token`，写进 `.ss2a_creds.json`（已 gitignore，权限 600）。
之后 `ss2a_client.py` 自动续期，401 自动刷新重试。

```bash
python3 ss2a_client.py status     # 看剩余有效期 + 连通性
python3 ss2a_client.py refresh    # 强制刷新（refresh_token 会轮换，自动写回）
```

> 用的是 **user_id 423 的普通用户 token**，`/api/v1/admin/*` 一律 403，只能用
> `/api/v1/accounts*`、`/api/v1/proxies`、`/api/v1/keys`、`/api/v1/usage`。

### 2. 一条龙导入

```bash
# 干跑：只打印会做什么，不写数据
python3 import_duckip_batch.py --files 'th_keys_duckip_nt_*.txt' --dry-run

# 真导：导入 + 绑分组 + 绑代理 + 复核
python3 import_duckip_batch.py --files 'th_keys_duckip_nt_*.txt'
```

脚本做的事：读 key 文件 → 去重 → **按 email 定位** → 从历史最大序号往后编名
（`tokenhour-384` 起）→ `POST /api/v1/accounts/data` → `PUT` 绑分组 + 绑代理 → 逐项复核。

### 3. 导入口径（老规矩，与现有 tokenhour 账号一致）

| 字段 | 取值 |
|---|---|
| name | `tokenhour-<序号>\|<email>` |
| platform / type | `openai` / `apikey` |
| base_url | `https://tokenharbor.ai`（**不带 `/v1`**，sub2api 自动补） |
| model_mapping | `deepseek-v4.1-flash` → `deepseek-v4.1-flash:free`、`mimo-v2.6-flash` → `mimo-v2.6-flash:free` |
| temp_unschedulable | `429` + `free_tier_limit_reached` → 停 **10080 分钟（7 天）**，`scope: account` |
| concurrency / priority | **`1` / `50`**（tokenhour 账号统一并发 1；整数，不能传小数） |
| 分组 | `tokenhour`（id **4687**），`confirm_mixed_channel_risk: true` |
| 代理 | 公共池按 延迟↑ 占用↑ 排序取，**排除 CN/HK/MO**（tokenharbor 对这三地 403） |
| extra | websockets_v2 `off`、长上下文计费 `false`、`provider: tokenharbor` |

### 4. 两个必踩的坑

- **`GET /api/v1/accounts` 会偶发返回空** —— 2026-10-18 就因为首次查询返回 0 条，序号从 1 重编，
  和历史 `tokenhour-1..110` 撞名。**永远用 email 定位账号，不要依赖序号**；改名修正见
  `fix_new110.py`。
- **`PUT /api/v1/accounts/{id}` 的 `credentials` 是替换语义** —— 只传部分字段会把没传的清空
  （实测丢过 `base_url` 和 `model_mapping`）。**每次写全**：`api_key` + `base_url` + `model_mapping` + 规则。
  另外读取接口不回显 `api_key`，只有 `credentials_status.has_api_key`，改之前要从本地 key 文件找回明文。
  但**只改 `concurrency` / `priority` 时单发一个字段是安全的** —— 实测 493 个号刷并发，
  credentials / 代理 / 分组全保留。

### 4.4 【铁律】改任何字段前先导快照

批量改字段一旦出错（清空 key、覆盖代理、撞名），没有快照就修不回来。

```bash
# ① 改之前：按分组前缀全量导出（条数不足 total 会拒绝出快照）
.venv/bin/python ss2a_snapshot.py --prefix tokenhour --label before_rule

# ② 干跑 → 真改 → 复核
.venv/bin/python add_capacity_rule.py --dry-run
.venv/bin/python add_capacity_rule.py --duration 1

# ③ 改坏了回退
.venv/bin/python ss2a_rollback.py --snapshot runs/snapshots/xxx.json --dry-run
.venv/bin/python ss2a_rollback.py --snapshot runs/snapshots/xxx.json
#   只退某几个字段：--fields proxy_id group_ids
```

快照落在 `runs/snapshots/accounts_<prefix>_<label>_<时间戳>.json`，
含 `credentials / proxy_id / group_ids / concurrency / priority / extra` 等全部可回退字段。

> `api_key` 不在快照里（读取接口不回显）。回退默认走「省略 api_key」模式——实测
> `PUT` 不传该字段系统会保留原值。**绝对不要传 `api_key: None`，那会真的清空 key。**

### 4.5 批量改并发

```bash
# 幂等：只动还不是 1 的，自带复核 + 完整性抽检
.venv/bin/python set_concurrency.py --prefix tokenhour --to 1

# 先看范围
.venv/bin/python set_concurrency.py --dry-run
```

493 个号跑完约 2 分钟（每 20 个打印进度）。ss2a.top 偶发 SSL 抖动会导致个别失败，
**再跑一次即可**（脚本只补不合规的）。

### 4.6 临时不可调度规则

| 规则 | 触发条件 | 冷却 |
|---|---|---|
| 免费额度用尽 | `429` + `free_tier_limit_reached` | **7 天**，`scope: account` |
| 模型容量满 | `429` + `at capacity for your account` | **60 秒**，`scope: model`（只冷却该模型） |

```bash
.venv/bin/python add_capacity_rule.py --duration 1     # 加容量规则，幂等
```

> **40 秒做不到**：sub2api 的 `duration_minutes` 是 int（分钟），且
> `DurationMinutes <= 0` 的规则会被直接丢弃。最小可用值 `1` = 60 秒。

### 4.7 代理失效 → 自动换一个（随机）

账号 `status=active` 却全部请求失败，报 `upstream transport error (proxy/network)`，
就是挂的代理已经不通了。

```bash
# 只看哪些账号要换
.venv/bin/python proxy_health_rotate.py --dry-run

# 真换：从「健康 + 承载最少」的 1/3 里随机挑，改前自动拍快照
.venv/bin/python proxy_health_rotate.py

# 常驻看护：每 5 分钟巡检
.venv/bin/python proxy_health_rotate.py --watch 300

# 顺手开原生兜底（挂了直连 / 挂了切固定备胎）
.venv/bin/python proxy_health_rotate.py --enable-fallback direct
```

**代理好坏看服务端字段**（别自己 TCP 探活，公共代理不暴露 host/port）：

| 字段 | 含义 |
|---|---|
| `latency_status` | `success` / `failed` ← failed = 真挂了 |
| `latency_message` | 失败原因 |
| `quality_status` | `healthy` / `failed` / `challenge` |
| `quality_grade` | `A` / `B` / `D` / `F` |
| `account_count` | **平台全局**承载，不是我们的账号数 |

> sub2api 原生只有 `fallback_mode: none|proxy|direct` + 固定 `backup_proxy_id`，
> **不能随机轮换**，所以随机换由这个脚本主动巡检完成。

### 5. 验证

```bash
# 单个账号跑一次真实请求（SSE 流，固定 gpt-5.4）
curl -N -X POST "https://ss2a.top/api/v1/accounts/<id>/test" \
  -H "Authorization: Bearer <access_token>"

# 端到端：拿网关 key 打一条，再查 usage 确认落在预期账号
curl https://ss2a.top/v1/chat/completions -H "Authorization: Bearer <ss2a_key>" \
  -d '{"model":"deepseek-v4.1-flash","messages":[{"role":"user","content":"hi"}]}'
```

> 若该账号白名单里没有 `gpt-5.4`，test 接口会返回上游 404 —— 那是**链路通、模型名不对**，
> 往 `model_mapping` 补一条 `gpt-5.4` 映射即可让测试变绿。

---

## 🤖 从 Agent 调用（Claude Code / opencode / Hermes）

把文件放进 agent 项目，然后指示：

```
用 batch_1024_v4.py 注册 N 个 TokenHarbor 账号（跳过 key 复验，test=untested）。
前置：source cf-mail/env.sh；起 1024proxy chain_pool（US，40 端口，独立 SID）。
流程：真浏览器注册（同会话建 key）→ verify 线程池收信 → 开通免费模型 → 导出 → 导入 sub2api。
「老规矩」= 分组 tokenhour(4687) / 并发 1 / 优先级 50 / 429 免费额度停 7 天 / 排除 CN/HK/MO 代理。
报告：有效账号数 / 总数、文件路径、导入成功失败数。
```

> 用户说「不用测试 key」时：**停止一切 key 复验**，只做文件层面操作；`test` 字段如实写
> `untested`，绝不伪造 `200`。

脚本可直接调用：
```bash
# 首选：1024proxy + v4 流水线（20 个约 3 分钟）
source cf-mail/env.sh
ACCT=$(awk -F, 'NR==2{print $1}' /Users/zxc/Downloads/data.csv)
PW=$(awk -F, 'NR==2{print $2}' /Users/zxc/Downloads/data.csv)
.venv/bin/python /Users/zxc/.dsh/skills/1024proxy/scripts/chain_pool.py 18130 40 "$ACCT" "$PW" US &
.venv/bin/python -u batch_1024_v4.py 20 6 4 18130 40

python /path/to/tor_parallel_farm.py 20      # 备选：Tor 批量
python /path/to/duckip_farm_notest.py 20 12 300  # 备选：duckip
python /path/to/sweep_nodes.py 1             # 少量：代理节点
python /path/to/th_auto_register.py single   # 单号流程
```

---

## 🧪 实测结果（2026 年）

> **本仓库经过真实验证** —— 不是空口宣称：

- **账号池 100 个，全部 `verified=True / test=200`**（`th_keys.txt`，零异常行）——
  邮箱已验证 ✅、免费模型已开通 ✅、key 实测 200 ✅
- **每账号 3 个免费模型**：`mimo-v2.5:free`、`deepseek-v4-flash:free`、`qwen3.8-27b:free` —— 全部 200 OK
- **自建收信实测**：Cloudflare Email Routing + Worker + KV，验证邮件 **1-60 秒**到达，
  注册 → 收信 → 点链接 → key 可用，全链路跑通（`cf-mail/`）
- **Tor 并行吞吐实测**（16 线程 + 自建邮箱 + 优选上游）：

  | 配置 | 尝试/账号 | 成功率 | 产出 |
  |---|---|---|---|
  | 16线程 + 有 `GET /login` | 25 | 4.0% | 3.81 个/分钟 |
  | 16线程 + 跳过 `GET /login` | **18** | **5.6%** | 3.57 个/分钟 |

  **成功率随时段波动 2.7% ~ 5.6%（2 倍）**，这个波动比所有技术优化的收益加起来还大 ——
  挑低峰时段跑比调参数管用。
- **代理节点方案**：单账号约 1 分钟，专线节点成功率高
- **1024proxy + 浏览器 v4 流水线（2026-11-12 实测，20 个号）**：
  - **25 次浏览器尝试 → 20 个有效（80%）**；5 次失败全是 1024 网关 `Error`（2-22s 快速失败；v4 已加「换端口重试 1 次」）
  - **总墙钟约 2 分 52 秒**（主批 18 个 1m49s + 补 2 个 1m03s），对比 v3 的 22 个/4.5 分钟，**吞吐 +44%**
  - signup+key 同会话：avg **13.5s**（中位 13s）；端到端 avg **43.6s**（中位 45s）
  - 新域名 `cosxmaxwell.dpdns.org`（Cloudflare catch-all）：`/health` 200，验证信正常投递
  - 剩余瓶颈：verify 等邮件约 **29s/号**（66%），但已被独立 verify 线程池吸收，不再阻塞 signup
- **住宅代理方案（2026-10-17 实测）**：
  - 主批 20 个：8 线程约 10 分钟 → 17 个有效 + 补跑 3 个 = **20/20**
  - 优化后单批：**33 次尝试出 11 个有效（3 次尝试/账号）**，建 key 重试把流失从 45% 压到 0~8%
  - **8 线程是甜点**（12 线程会挤爆 duckip 网关，注册成功率腰斩）
- **住宅代理方案（2026-10-18 实测，110 个号）**：
  - **193 次尝试 → 110 个有效（57%）**，12 线程，两批跑完约 15 分钟
  - 失败原因几乎全是 `You're doing that a bit fast`（出口短窗口限流），换住宅 IP 即恢复
  - **跳过 key 复验**版本同样产出 110/110 `verified=True`，只是不做那次真实 chat 请求
  - 导入 sub2api：`account_created 110 / account_failed 0`，分组 / 代理 / 429 规则全部复核通过
- **Tor 路线 2026-10-17/18 全面失效**：机场节点在出口侧切断 Tor 中继 TLS（日志刷 `TLS eof`，
  卡 `Bootstrapped 50%`），换节点无效；机场 IP / 机房代理 IP 亦被 tokenharbor 限流
- **站点重部署会轮换 Server Action**：2026-10-18 build（`dpl_E8S2…`）signUp id 从 `60d54904…`
  换成 `60554b12…`，旧值表现为**全量 `✗ ?` 空错误**。`ACTION_KEY` 那次没变。详见
  skill 的 `references/protocol-repair.md`
- 免费档宽限期：**每账号 7 天**（从开通算起）—— 批量 farm = 分层库存

---

## 📁 文件结构

```
tokenharbor-farmer/
├── batch_1024_v4.py        # 🏠 1024proxy 住宅 + 真浏览器 v4 流水线（2026-11 首选）
├── batch_1024_v3.py        # 🏠 1024proxy v3（同会话建 key，旧版）
├── tor_parallel_farm.py    # 🚀 Tor 并行农场（流隔离 + Tor 自动启停）
├── tor_screened_farm.py    # 🛡️ 出口预筛农场（烧伤记忆 + NEWNYM + 榨干模式）
├── batch_run.py            # 📦 批量包装器（N 个 → 独立 th_keys_batchN_*.txt）
├── batch_run_notest.py     # 📦 同上，跳过 key 测通（test=untested 如实标记）
├── duckip_farm.py          # 🏠 住宅代理农场（duckip SOCKS5 隧道）
├── duckip_farm_notest.py   # 🏠 住宅农场「跳过测 key」版（批量出号首选）
├── duckip_relay.py         # 🔌 本地转发（18080 → mihomo → duckip 网关）
├── import_duckip_batch.py  # 📥 注册结果 → sub2api 导入（分组/代理/规则一条龙）
├── fix_new110.py           # 🔧 修正撞名账号（续号 + 重绑分组代理，按 id 精确改）
├── ss2a_client.py          # 🔑 sub2api 凭据自动续期 + api() 通用调用
├── th_to_sub2api.py        # 📥 注册结果 → sub2api 导入文件（离线生成器）
├── sub2api_bind_proxies.py # 🌐 代理一对一分配（生成映射 + 可写回）
├── proxy_register.py       # 🌐 外部代理池注册器（每代理一个出口）
├── sweep_nodes.py          # 🔄 代理节点轮换注册（批量/流水线并行）
├── th_tui.py               # 🎨 Rich TUI 菜单（点击选择）
├── th_preflight.py         # ✅ 预检（依赖、Tor、数据库、state）
├── th_export.py            # 📤 导出账号 → accounts_full.txt + api_keys.txt
├── th_farm_multi.py        # ⚠️ 旧版多 Tor 并行农场（已过时）
├── th_tor_farm.py          # ⚠️ 旧版单 Tor 批量农场（已过时）
├── th_auto_register.py     # 核心库：注册/验证/key/注入 + CLI(single/loop)
├── farm_rotate_nodes.py    # 节点轮换注册（另一入口）
├── register_direct_v2.py   # 直连注册（调试用，多编码变体探测）
├── db_path.py              # 9router 数据库路径探测
├── inject_th_kv.py         # 注入 kv customModels 到 9Router
├── fix_th_model_locks.py   # 给所有 th 连接加 modelLock（qwen 等）
├── fix_th_provider.py      # 修正 provider 连接（合并到正确的 TokenHarbor 节点）
├── WORKFLOW.md             # 📈 完整工作流（可视化）
├── TUI-PREVIEW.txt         # 📟 菜单界面预览
├── STEPBYSTEP.md           # 完整技术流程（接口、action ID、错误处理）
├── README.md               # 本文件
└── .gitignore              # 排除 state、key、env
```

**运行时生成的文件**（已加入 `.gitignore`，不会提交）：

| 文件 | 内容 |
|---|---|
| `th_keys.txt` | 账号清单：`邮箱 <TAB> 密码 <TAB> key <TAB> verified <TAB> test` |
| `th_keys_1024v4_nt_*.txt` | 1024proxy v4 流水线批次产出，`test=untested`（2026-11 首选） |
| `th_keys_20261112_final.txt` | 多批合并后的唯一 key 总池（按 key 去重） |
| `th_keys_duckip_nt_*.txt` | duckip 住宅农场（跳过测 key）批次产出，`test=untested` |
| `th_keys_batch*_*.txt` | 批量包装器每批独立产出（实时落盘，中途掐了不丢） |
| `th_direct_results.jsonl` | 完整记录（含节点、出口 IP 等） |
| `node_state.json` | 节点冷却缓存（限额/验证码/不通/封锁） |
| `yyds_bad_domains.json` | 收不到验证邮件的邮箱域名黑名单 |
| `.ss2a_creds.json` | sub2api 凭据（JWT + refresh_token，权限 600） |
| `runs/import_duckip_*.json` | sub2api 导入包（可直接后台「导入数据」） |
| `runs/new110_ids.json` | 本批账号 id ↔ email 映射（撞名修正用，按 email 定位的凭据） |

---

## 📖 技术细节

阅读 **[STEPBYSTEP.md](STEPBYSTEP.md)** 了解：
- 注册接口 + multipart body（Next.js Server Action）
- 所需的 Action ID 与请求头
- 错误处理（`Too many`、`couldn't create`、`human check`）
- Tor 配置 + NEWNYM 轮换
- 9Router 注入数据结构

### 关键实现要点

**注册前置检查** —— 提交 signUp 前必须先调 `GET /api/auth/signup-precheck?fp=<device_fingerprint>` 登记指纹，否则服务端直接返回 HTTP 500。

**邮箱域名决定成败** —— tokenharbor 的发信方会**静默丢弃**某些域名（注册成功、key 正常发放，但验证邮件永远不到，导致 key 一直 403）。实测 `lowdn.com` 全部失败、`nexushint.com`/`pointnexus.org`/`barconic.org` 全部成功。脚本会自动跳过已知坏域名。

**临时邮箱有三档，按优先级自动选择**：Cloudflare 自建收信 → YYDS Mail → tempmail.lol。

```bash
# ① Cloudflare 自建收信（推荐批量时用）—— 见 cf-mail/README.md
#    catch-all：地址本地生成，建箱不需要 API 调用，所以没有建箱配额限制
#    2026-11 当前实例（已写入 cf-mail/env.sh）：
CF_MAIL_BASE=https://mail.cosxmaxwell.dpdns.org
CF_MAIL_TOKEN=<worker 的 MAIL_TOKEN>
CF_MAIL_DOMAIN=cosxmaxwell.dpdns.org

# 换域名时只需改 env.sh 的 CF_MAIL_BASE / CF_MAIL_DOMAIN；
# 先在 Cloudflare 把自定义域 mail.<domain> 接到 cf-mail Worker，
# 再把该 zone 的 Email Routing catch-all 指向 cf-mail，最后 curl /health 确认 200。

# ② YYDS Mail（默认，无需额外配置）
YYDS_API_KEY        # YYDS Mail 的 API key
YYDS_DOMAINS        # 候选域名，逗号分隔
YYDS_LOCAL          # 固定邮箱地址（单账号用）
YYDS_LOCAL_PREFIX   # 前缀 + 随机后缀（批量推荐，保证唯一）
```

> **为什么需要 Cloudflare 那档**：YYDS 和 tempmail.lol 的建箱接口都有配额。批量跑时每 200+ 次尝试
> 就会撞上 `Too many account creation requests`，回退的 tempmail 也在限流，整个流程被卡住。
> Cloudflare catch-all 没有"建箱"这个概念，天然绕开这个瓶颈。

**没有"不建号就能探测节点"的便宜信号** —— 参数校验、重复邮箱校验都排在限额判定之前，所以唯一办法就是真发一次 signup。脚本因此把结果记进 `node_state.json`，下次直接跳过还在冷却期的节点。

**Tor 的瓶颈是延迟不是算力** —— 实测 Tor 三跳一次请求 2.8-4.0s（直连 mihomo 只要 0.53s），
并发 32 线程时单次尝试膨胀到约 40s，而 Tor/Python 的 CPU 占用都不到 11%。
所以**并发降到 16 反而更快**（95 次/分钟 vs 32 线程的 50 次/分钟），加线程没有收益。
想提速应该走"双出口池并行"（Tor + mihomo 节点同时跑）而不是加线程。

**多开 Tor 实例是负优化** —— 实测 8 线程 × 2 个 Tor 实例 vs 16 线程 × 1 个：
尝试速率只涨 16%（95 → 110 次/分钟），但成功率腰斩（4.0% → 1.9%），
净产出反而降 46%（3.81 → 2.07 个/分钟）。原因：两个实例共享同一个上游 mihomo 代理，
而且共用一个 Tor 出口池，把 exit 更快烧掉。**上游代理才是真瓶颈**，不是 Tor 的进程数。

**剩下的真正瓶颈是成功率（2.7-5.6%，随时间段波动）** —— 96% 的尝试白费。而且**这个波动幅度（2 倍）比所有技术优化的收益加起来还大**：
同样的配置凌晨跑能到 5.6%，晚上只有 2.7%。所以**挑低峰时段跑比调参数更管用**。
成功率的决定因素是 tokenharbor 的反欺诈判定（按出口 IP），不是我们代码能控制的。

**obfs4 网桥在本机不可用**（实测结论，别再折腾）：

| 路径 | 结果 |
|---|---|
| 网桥直连 | ❌ 网络层不通 —— 两个网桥 IP 都连不上（`87.121.52.247:9216`、`185.177.207.18:30196`） |
| 网桥 + 上游代理 | ❌ 卡在 `Bootstrapped 50%`，obfs4/TLS 握手反复崩（`unexpected eof`、`SSL state error in HANDSHAKE`） |

本机网络是**部分可达**：`example.com:443` 和 `8.8.8.8:53` 直连通，但 `1.1.1.1:443`、Tor 中继、网桥 IP 全不通。所以 Tor 只能经上游代理出网。

`TOR_BRIDGES` 的代码保留着（`UseBridges 1` + `ClientTransportPlugin`），换个网络环境可以直接用。踩到的坑已修：**Tor 用 `exec` 启动 PT 时不走 PATH**，写 `obfs4proxy` 会让它反复以 status 1 退出、Tor 永远卡在 2%，必须给绝对路径（脚本已用 `shutil.which` 自动解析）。

---

## ⚠️ 故障排查

| 问题 | 解决方案 |
|---|---|
| `Too many sign-ups from this network` | 出口 IP 额度用尽 —— 换节点或等 1 小时。脚本会自动记录并跳过 |
| `We couldn't create your account` | 出口被风控标记 —— Tor 方案会自动换出口 |
| `Please complete the human check` | 触发 Cloudflare Turnstile —— 该出口被标记，换一个 |
| `Your IP or email provider is not supported` | 出口国家被封锁（香港/澳门/中国大陆），换非封锁地区节点 |
| Chat 403 `region_blocked` | **不是 key 问题** —— 当前出口在被封锁地区，换个节点再测 |
| Chat 403 `Verify your email address` | 邮箱未验证 —— 检查是否用了坏域名（见上方说明） |
| `Tor 未运行，且自动启动已关闭（默认）` | 默认不会自动启 Tor —— 手动起一个（`tor -f <torrc>`），或加 `TOR_AUTOSTART=1` 让脚本自己启停 |
| Tor 卡在 `Bootstrapped 5%` | 直连 Tor 网络不通 —— 确认 `TOR_UPSTREAM_PROXY`（默认 `127.0.0.1:7890`）指向的代理是活的 |
| Tor 卡 10%，日志刷 `TLS eof / SSL state error in HANDSHAKE` | **机场节点切断 Tor 中继 TLS** —— 换节点无效，改走住宅代理方案 |
| 注册全量 `✗ ?`，响应 `Server action not found.` | 站点重新部署，ACTION_ID 过期 —— 按 skill 的 protocol-repair 重新提取 |
| 注册全量 `✗ ?`（连续 60 次，空错误） | **同上，最典型的重部署症状** —— 先抓 `?mode=signup` 页面的 `$ACTION_1:0` 和 JS chunk 里的 `createServerReference(...,"signUp")` 交叉验证，别猜 |
| 注册报 `Email and password are required.` | 表单字段前缀过时（当前 `_1_` 前缀 + `invite_code` + `0` 字段） |
| 1024proxy 返回 `403 Proxy-Authenticate: Basic` | 不要直连 white API 的 `IP:port`；用 `1024proxy` skill 的 `chain_pool.py` 经海外出口 + 账密网关 |
| 1024proxy 返回 `forbidden ip=39.x not supported` | 客户端是中国大陆 IP；chain_pool 必须先经 mihomo SOCKS5 海外出口 |
| 1024proxy `EXC Error`（2-22s） | chain 连接断；v4 会自动换一个新端口/SID 重试 1 次，仍失败才放弃 |
| 注册页 `no-form` / `Vercel Security Checkpoint` | 该住宅 IP 已被目标站风控；放弃该端口，换下一个 SID |
| v4 跑得慢、verify 拖住 | 确认用的是 `batch_1024_v4.py`（verify 线程池），不是 v3；CF mail 轮询已降到 2s |
| `key-403 challenge` | 不要退回 clean browser login；检查是否在 1024 住宅 IP 上、注册会话是否还活着 |
| duckip 代理返回 `403 ip forbidden` | 客户端 IP 不在白名单，且白名单禁中国大陆 IP —— 用 `duckip_relay.py` 经机场出口连接 |
| duckip 网关 HTTP 代理无响应 | **网关是 SOCKS5**（1000 端口），用 `socks5h://user:pass@host:1000` |
| duckip 只出少量号就 `DONE` | 第 3 个参数是**总尝试上限**（不是倍数），给太小会提前收工；`40` 只够出 24 个 |
| 导入后账号撞名（`tokenhour-1..110` 重号） | `GET /api/v1/accounts` 偶发返回空 → 序号从 1 重编。**按 email 定位**并改名续号，见 `fix_new110.py` |
| 改 sub2api 账号后 `base_url` / `model_mapping` 变空 | `PUT` 的 `credentials` 是**替换语义** —— 必须写全 `api_key` + `base_url` + `model_mapping` + 规则 |
| `import` 报 `api() got an unexpected keyword argument 'json'` | `ss2a_client.api()` 的 body 参数叫 `body=`，不是 `json=` |
| sub2api test 接口返回上游 404 | 该账号 `model_mapping` 白名单里没有 `gpt-5.4` —— 链路通，补一条映射即可 |
| Tor 起不来（`TOR_AUTOSTART=1`） | 看报错里附带的 Tor stderr；确认已安装 `brew install tor`，或设置 `TOR_BIN` 指向正确路径 |
| `端口 9050 仍被非 tor 进程占用` | 有别的程序占了 9050 —— 换端口：`TOR_SOCKS_PORT=9150 TOR_CONTROL_PORT=9151` |
| 跑完发现 Tor 还在 | 默认模式脚本不碰 Tor；`TOR_AUTOSTART=1` 模式正常会关掉（除非设了 `TOR_KEEP_ALIVE=1`）。手动清理：`pkill -f "tor -f"` |
| 注入失败 | 用 `NINE_ROUTER_DB` 指定正确的数据库路径 |

---

## ⚠️ 说明与责任

- **适用场景**：自己的账号与工作流、自动化测试。
- **1 账号 = 7 天免费**（从开通算起）—— 之后免费档结束，需要新账号。
- **不适用于**：滥用第三方服务、盗版或任何非法活动。
- 使用产生的责任由使用者自负。

---

## 📜 License

MIT —— 自由使用、修改、分发。署名可选。

© 2026 TokenHarbor Farmer
