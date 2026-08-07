# 会话记录 · 2026-08-06 · 自助申请精简与业务系统管理

## 需求

- 自助申请只保留：账号延期；账号、权限申请；账号、权限关闭（仅本人）
- 申请/关闭：选系统账号、选权限；有敏感能力的系统可勾选敏感项
- 组织侧账号申请同步加权限选择；与自助仅「可选人员」不同
- 「我负责的系统」→「业务系统管理」：权限列表/树 + 外部登录 / 是否有敏感权限

## 改动

- `leuc-proto/db.py`：系统 `has_sensitive`；权限目录树（`parent_id` / `is_sensitive`）
- `leuc-proto/app.py`：`/api/bind/systems`、`/api/my-systems` 返回权限；`has-sensitive` 开关；`my-accounts` 附权限目录；ensure_db 检测权限树种子
- `leuc-proto/static/index.html`：自助三入口；绑定明细权限勾选；关闭弹窗选账号+权限；业务系统管理 UI
- 文档：`V2.0/用户中心功能模块整理-O-V2.1.md`、`O-V2.0-UPDATE-RECORD.md`

## 手测

- `zhangsan`：`/api/bind/systems` 来酷含 4 项权限；`my-accounts` 带 permissions
- `zhaomin`：`/api/my-systems` 返回 forbid / has_sensitive / permissions
- `wangqiang`：可拉系统列表（代人申请共用表单）

## 原型

http://127.0.0.1:5055 · 密码 `123456`
