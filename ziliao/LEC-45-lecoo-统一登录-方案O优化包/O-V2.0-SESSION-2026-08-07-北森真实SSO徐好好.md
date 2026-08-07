# 会话：北森真实 SSO（徐好好 → iTalent）

日期：2026-08-07

## 目标

用徐好好完成北森真实 SSO：登录成功进入 https://www.italent.cn/

## 结果

已打通。AuthCenter 接受 LEUC 签发的 RS256 JWT，北森重签后落到门户首页。

### 测试账号

| 项 | 值 |
|----|-----|
| username | xuhaohao |
| 密码 | 123456 |
| display_name | 徐好好 |
| beisen_user_id | 630702408 |
| email | xuhh2@lenovo.com |
| uty | id（sub=630702408） |

### 自动化验证（curl 跟随跳转）

1. `POST /api/login` → xuhaohao
2. `POST /api/beisen/sso/launch`，`return_url=https://www.italent.cn/`
3. 打开 `redirect_url`（AuthCenter）并跟随重定向

最终落到：

`https://www.italent.cn/portal/iTalentHome/?quark_s=...`（HTTP 200）

中间链：

1. `oapi.italent.cn/SSO/AuthCenter?id_token=...&return_url=https://www.italent.cn/`
2. `www.italent.cn/v2/sso/oidc?id_token=...`（北森重签，payload 含 `sub=630702408`、`email=xuhh2@lenovo.com`）
3. `www.italent.cn/?quark_s=...`
4. `www.italent.cn/portal/iTalentHome/?quark_s=...`

### 浏览器手测

1. 启动：`cd leuc-proto && python3 app.py`（已关 reloader，避免 5055 Connection refused）
2. 打开 http://127.0.0.1:5055/demo/home
3. 登录 xuhaohao / 123456（若未登录会先走 `/sso?next=beisen_sso`）
4. 点「北森」→ 应进入 iTalent 门户（已登录态）

或直接访问（已登录 Cookie）：

`/beisen/sso/go?return_url=https%3A%2F%2Fwww.italent.cn%2F`

## 代码改动

- `leuc-proto/app.py`：`app.run(..., use_reloader=False)`，避免 debug 热重载把联调进程弄挂

## 相关文件

- `leuc-proto/beisen_sso.py`
- `leuc-proto/.env`（`BEISEN_SSO_*`，`RETURN_URL=https://www.italent.cn/`）
- `leuc-proto/static/home.html`（北森入口）
