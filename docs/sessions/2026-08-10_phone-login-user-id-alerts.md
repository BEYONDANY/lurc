# 手机号登录 + 数字用户ID + 手机必填报警

## 需求

- 默认登录：手机号 + 密码；保留手机/邮箱验证码登录与改绑
- 系统展示不可变数字用户 ID（`users.id`）
- 手机号必填且唯一；缺失/重复写日志并系统报警，跳过建用户

## 改动

- `leuc_ops.py`：`raise_system_alert` / `list_system_alerts`；发信文案改为登录手机+用户ID
- `db.py`：建用户手机缺失/重复标记；菜单「系统报警」；补丁写入角色菜单
- `app.py`：登录按手机查用户（admin 特殊通道）；`/api/login/otp/send`；同步/导入/外部/OA 建用户手机校验+报警；`/api/admin/alerts`
- `static/index.html` / `sso.html`：登录模式切换；组织/个人中心展示 `#id` 与手机

## 验证

- Python 语法解析通过
- 手测：admin 密码登录；手机验证码登录；缺手机同步应出现系统报警
