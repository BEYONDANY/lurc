# 修复外部人员登录名预览 403

## 背景
新建外部人员输入姓名时，`POST /api/username/preview` 返回 403，登录名无法按拼音生成。

## 原因
预览接口原先要求 `require_dept_manage`；自助申请入口仅需 `org_add`，有按钮但无部门管理权时预览被拒。

## 改动
- `app.py`：`/api/username/preview` 改为登录即可预览；创建 `/api/dept/members` 仍用 `can_add_external_member`（`org_add` 或部门管理权）
- `static/index.html`：预览防抖与提示；去掉重复的旧 `previewUser`/`submitAdd`

## 验证
- 重启服务后打开新建外部人员，输入姓名应返回 200 并自动填入拼音登录名
