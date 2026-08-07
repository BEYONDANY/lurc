# 会话记录 · 2026-08-06 · 北森 SSO 适配

## 目标

按 `ziliao/sso` 手册与 Beisen.OIDC.SDK 协议，在 `leuc-proto` 自研一版北森真实 SSO（不依赖 Java SDK）。

## 改动文件

- `leuc-proto/beisen_sso.py` — JWT 签发 / AuthCenter URL
- `leuc-proto/app.py` — `/api/beisen/sso/*`、`/beisen/sso/go`、门户标记
- `leuc-proto/static/home.html` — 北森已配置则走真实 SSO
- `leuc-proto/static/sso.html` — `next=beisen_sso` 登录后跳转
- `leuc-proto/data/beisen_sso.local.example.json` — 配置模板
- `leuc-proto/.gitignore` — 忽略 `beisen_sso.local.json`
- `leuc-proto/requirements.txt` — 增加 `cryptography`
- `leuc-proto/README.md` — 使用说明

## 配置

- 租户 ID `aud`：`614218`
- AppID：`100`
- 登录标识：`uty=id`，`sub`=用户 `beisen_user_id`
- 密钥放 `.env` 的 `BEISEN_SSO_*`（与 LeOrg 同文件，不入库；见 `.env.example`）
- 「我的组织」展示/编辑北森用户ID；演示账号已回填示例 ID（如 huangwei=`6100006`）

## 验证

- `python beisen_sso.py`：enabled=true
- 自签 JWT 公私钥验签通过
- `import app` 正常

## 入口

- `/demo/home` 点北森（已配置时）
- `/beisen/sso/go`
- `POST /api/beisen/sso/launch`
