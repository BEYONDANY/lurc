# 会话：北森/飞书同步 ID 自动绑定可登录

## 改动
1. 新增 `profile_sso_id`：读通讯录 `beisen_user_id` / `feishu_user_id`
2. `_beisen_sso_diagnose`：无账号池开通时用通讯录北森 ID（`source=profile`）
3. `home_nav_systems`：有资料 ID 则 `can_enter`，文案「同步自动开通」；北森直达 `/beisen/sso/go`
4. `my_systems`：有 ID 文案「同步自动绑定」
5. 清理组织列表 / 账号管理 / demo 导航中「通讯录不能登录」误导文案

## 文件
- `leuc-proto/app.py`
- `leuc-proto/static/index.html`
- `leuc-proto/static/home.html`

## 验证
- `liyang`：SSO diagnose `ok` + `source=profile`；导航北森/飞书可进入
- `admin`：无北森 ID，SSO 失败并提示同步
