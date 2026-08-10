# 邮箱后缀白名单 + 系统配置中心

## 需求

绑定/注册邮箱必须为指定后缀；配置落库，提供系统配置中心维护。覆盖个人中心改绑与默认注册。

## 改动

- `db.py`：`system_settings` 表；默认项 `email_allowed_suffixes`（`@lecoo.com,@lenovo-store.cn`）；`ensure_email_suffix_allowed`；菜单「系统配置」
- `app.py`：`GET/PUT /api/admin/settings`、`GET /api/settings/public`；改绑/编辑/外部创建强制校验；同步/导入不合规则忽略邮箱并报警
- `static/index.html`：系统配置页；改绑弹窗提示允许后缀

## 验证

- 语法解析通过；后缀解析单测通过
