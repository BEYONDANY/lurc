# 会话：刷新保持当前页

## 改动
- 侧栏切换与程序跳转时把 `view` 写入 URL（`?view=xxx`）
- 登录进入后仍按 URL 恢复页面（原逻辑已有，此前未写回 URL）
- 北森 SSO 清参时保留 `view`

## 文件
- `leuc-proto/static/index.html`
