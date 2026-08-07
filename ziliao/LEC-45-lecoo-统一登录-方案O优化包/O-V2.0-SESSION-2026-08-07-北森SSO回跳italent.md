# 会话：北森 SSO 回跳 italent.cn

- 日期：2026-08-07
- 范围：`leuc-proto`

## 需求

- 北森门户地址：[https://www.italent.cn/](https://www.italent.cn/)
- 从 `http://127.0.0.1:5055/demo/home` 点「北森」走真实 SSO

## 改动

1. `.env` / `.env.example`：`BEISEN_SSO_RETURN_URL=https://www.italent.cn/`
2. `beisen_sso.py`：默认 `return_url` 同上；AuthCenter 仍为 `oapi.italent.cn`
3. `/api/demo/portal-systems`：返回 `beisen_portal_url` / `beisen_sso_go`
4. `home.html` / `sso.html`：跳转时带上 return_url，落地 iTalent 门户

## 验证

- `maning`（beisen_user_id=6307024）登录后可签发：
  - AuthCenter：`https://oapi.italent.cn/SSO/AuthCenter?id_token=...&return_url=https%3A%2F%2Fwww.italent.cn%2F`
  - `/beisen/sso/go` → 302 同上
- 附带修复：`row_user` 漏传 `beisen_user_id` 导致无法 SSO
