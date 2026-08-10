# 登录页默认账号改为 admin

## 背景
演示登录表单原先预填 `zhangsan` / `lisi`，改为默认 `admin`。

## 改动
- `static/index.html`：登录账号默认 `admin`；页脚提示以 admin 为默认
- `static/sso.html`：SSO 登录默认 `admin`，去掉 zhangsan/lisi 演示说明
- `static/home.html`：业务系统导航建议改为默认 admin

## 验证
- `docker compose up --build -d leuc`
- 打开 http://127.0.0.1:5055 确认账号框为 admin
