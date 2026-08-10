# 业务系统导航改为新标签打开

## 背景
`/demo/home` 点击系统原先在当前页跳转登录，导航页会被冲掉。

## 改动
- `leuc-proto/static/home.html`：点击系统用 `window.open(/demo/home?open=code)` 新标签进入
- 新标签内复用既有 `enterApp`（含 OAuth / 北森 SSO / 已登录工作台），并清理 URL 上的 `open` 参数

## 验证
- 打开 http://127.0.0.1:5055/demo/home ，点击任一系统应新开标签；原导航页保留
