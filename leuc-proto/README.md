# LEUC 多角色交互原型（SQLite）

Lecoo 用户中心（LEUC）可点击原型，数据落在本地 SQLite。

## 启动

```bash
cd leuc-proto
pip install -r requirements.txt
python app.py
```

浏览器打开：http://127.0.0.1:5055

### Docker 本地部署（推荐）

```bash
cd leuc-proto

# 可选：配置 LeOrg / 北森
cp -n .env.example .env

# 构建并启动（前台看日志用 up；后台加 -d）
docker compose up --build -d

# 查看日志 / 停止
docker compose logs -f
docker compose down
```

打开：http://127.0.0.1:5055  
手机同网访问：`http://<电脑局域网IP>:5055`

说明：
- 数据持久化在 `./data`（SQLite）
- 端口可用环境变量覆盖：`LEUC_PORT=5055 docker compose up --build -d`
- 首次强制重建种子库（会清空）：`LEUC_FORCE_INIT=1 docker compose up --build -d`

重建种子数据（会删除库）：

```bash
python -c "from db import init_db; init_db(force=True)"
```

## 演示账号（密码均为 `123456`）

| 角色 | 账号 | 要点 |
|------|------|------|
| 普通员工 A | `zhangsan` | 可看「部门和人员」+ 发站内信 |
| 普通员工 B | `lisi` | ERP×3；授权登录需选账号 |
| 部门负责人 | `wangqiang` | 管研发中心及下级；审批延期/敏感第1步 |
| 系统负责人 | `zhaomin` | 系统和账号：我负责的系统 + 系统账号管理 |
| 超管 | `admin` | 全部；敏感一级领导示例 |
| 财务 | `zhangcai` | 敏感审批链·财务节点 |
| 人事专员 | `sunli` | 部门和人员·全部；同步初始化与直接绑定 |
| 研发二组员工 | `wujiu` | 多级部门示例 |

敏感权限：默认链「直属领导 → 一级领导 → 财务」可配；通过后自动开通。

账号绑定：一个 LEUC 可绑多个系统账号。超管/人事可直接绑定；部门负责人/员工申请绑定，系统负责人在「系统账号管理」按手机/邮箱/itcode/用户名/姓名匹配确认（可新建/导入/同步）。

重名示例：`zhangsan1`。登录验证码 `888888`；找回密码 `666666`。

## 业务系统导航（独立页）

打开 http://127.0.0.1:5055/demo/home

1. 导航页点击 **OA / BIP / 来酷ERP / 科技ERP / 北森**
2. 跳转 LEUC `/sso`：已登录则按绑定账号选号或自动回跳；未登录先登录
3. **无绑定/无权限**：提示「无法使用 LEUC 登录」（全员登录系统可自动开通）
4. 回跳 `/demo/home/callback?app=系统code` 换票进入该系统工作台

建议：`zhangsan`（来酷单账号）、`lisi`（来酷三账号 + OA/BIP）、`wangqiang`（来酷/科技/北森）

内置客户端（回调统一到导航页）：

| 系统 | client_id | 回调 |
|------|-----------|------|
| OA 办公 | `client_oa` | `/demo/home/callback?app=oa` |
| BIP | `client_bip` | `/demo/home/callback?app=bip` |
| 来酷ERP | `client_laiku_erp` | `/demo/home/callback?app=laiku_erp` |
| 科技ERP | `client_keji_erp` | `/demo/home/callback?app=keji_erp` |
| 北森 | `client_beisen` | `/demo/home/callback?app=beisen` |

安全能力（主流 OIDC 对齐演示）：Authorization Code、**PKCE S256**、redirect_uri 白名单、client_secret 换票、code 一次性、state/nonce。

发现文档：http://127.0.0.1:5055/.well-known/openid-configuration

## 北森真实 SSO（OIDC JWT）

参考 `ziliao/sso` 手册/SDK，在本原型内用 Python 自研签发（不依赖 Java SDK）。

1. 在 `.env` 配置（与 LeOrg 同一文件，**勿提交**）：

```bash
cp .env.example .env
# 填写 BEISEN_SSO_TENANT_ID / BEISEN_SSO_PUBLIC_KEY / BEISEN_SSO_PRIVATE_KEY
# PEM 可用 \n 表示换行，整段加双引号；uty 默认 email
```

2. 安装依赖后启动，检查状态：

```bash
pip install -r requirements.txt
curl -s http://127.0.0.1:5055/api/beisen/sso/status
```

3. 入口：

| 入口 | 说明 |
|------|------|
| `/demo/home` 点「北森」 | 已配置则走真实 SSO |
| `/beisen/sso/go` | 浏览器直达（未登录先 `/sso?next=beisen_sso`） |
| `POST /api/beisen/sso/launch` | 已登录签发，返回 `redirect_url` |

联调注意：`sub` 默认取当前用户的 **北森用户ID**（`BEISEN_SSO_UTY=id`）。LeOrg 同步会把员工字段 `beisen_id` 写入 `users.beisen_user_id` / 待建花名册（界面只读展示）。该 ID 须在北森租户内存在。

SSO 成功后默认进入 [iTalent 门户](https://www.italent.cn/)（`BEISEN_SSO_RETURN_URL`）。

**启动方式（推荐）**：Mac Apple Silicon 请用 `./run.sh`（强制 arm64 + `.venv`）。若用 Rosetta/`x86_64` Python 直接跑 `python3 app.py`，`cryptography` 可能架构不匹配，北森会降级成演示 OIDC，点击后停在 `/demo/home` 而不跳 italent。

## LeOrg 部门/人员同步

对接 [LeOrg API](https://leorg-ai.lecoosys.com/api/docs/)：`/v1/organizations`、`/v1/employees`（OAuth2 client_credentials）。

1. 配置环境变量（**勿提交** `.env`）：

```bash
cp .env.example .env
# 编辑填写 LEORG_CLIENT_ID / LEORG_CLIENT_SECRET
```

2. 检查状态：`GET /api/leorg/status`

3. 人事专员在「部门和人员」：
   - **清空部门**：删部门/员工，保留管理账号（不会被启动重种覆盖）
   - **增量同步** / **全量同步**：幂等 upsert；首次或清空后自动走全量
   - 增量基于 `/v1/employees/changes` + 水位 `last_change_id`
   - 确认用户名后「创建」走原 `/api/hr/sync-init`

也可：`POST /api/hr/sync-pull`，body `{"mode":"auto"|"full"|"incr"}`。

## 文件

- `app.py` — Flask API
- `db.py` — SQLite schema + 种子
- `data/leuc.db` — 运行后生成
- `static/index.html` — 前端原型
