# 会话：个人中心固定展示本系统/北森/飞书

## 改动
1. `my_systems`：确保本系统账号行存在；始终补齐「本系统 / 北森 / 飞书」展示（无绑定时用通讯录 ID）
2. `users.feishu_user_id`：迁移加列；LeOrg 同步写入；`row_user` 透出
3. 个人中心卡片展示账号标签 + ID（`<code>`）

## 文件
- `leuc-proto/app.py`
- `leuc-proto/db.py`
- `leuc-proto/static/index.html`

## 补充
- 飞书用户ID：**可为空**（空显示 `—`，不标「不可登录」）
- 北森用户ID：**必然有**（空仍标「未同步 / 应有值」）
