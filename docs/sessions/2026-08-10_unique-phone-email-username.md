# 手机/邮箱/用户名唯一

## 需求

本系统手机号、邮箱、用户名不可重复。通讯录建用户遇冲突时：用户名加数字唯一标识，冲突的手机/邮箱置空忽略，保证新建成功。

## 改动

- `db.py`：`normalize_phone/email`、占用查询、`ensure_contact_available`（手工拒绝）、`resolve_contacts_for_create/update`（同步忽略冲突字段）；补丁 `users_unique_phone_email_v1` 去重后建唯一索引
- `app.py`：LeOrg 同步创建/更新、确认落库、导入、OA 新建、外部人员创建走上述策略；组织编辑/安全改绑拒绝重复

## 验证

- `python3 -c` 语法解析 `db.py`/`app.py` 通过
- 运行时需在有 `psycopg` 的环境对 migrate 与同步路径手测
