# 会话：手机端可访问 LEUC 原型

## 目标
让同网手机可通过电脑局域网 IP 打开 LEUC 原型（含 OIDC 回调）。

## 改动
- `app.py`：监听 `0.0.0.0`（可用 `LEUC_HOST`/`LEUC_PORT`）；`issuer`/`portal_redirect` 按请求 Host 生成；loopback 登记的 redirect_uri 允许当前 Host 替换
- `static/home.html` / `callback.html` / `index.html`：回调默认用 `location.origin`
- `README.md` / `run.sh`：补充手机访问说明

## 验证
- 监听 `0.0.0.0:5055`，本机与容器 IP 均 200
- Host `192.168.1.8:5055` 时 `portal_redirect` / `issuer` 为该 Host
- redirect 放宽：LAN Host 允许，evil.com 拒绝
