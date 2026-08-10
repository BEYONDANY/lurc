# 修复新建外部人员 403

## 现象
自助申请可见「新建外部人员」，`POST /api/dept/members` 返回 403。

## 原因
- 前端仅按 `org_add` 显示按钮；后端需 `org_add` 或部门管理权
- `dept_owner` 库内曾缺 `org_add`；自定义角色若被同步冲掉也会丢权限
- `hasBtn` 对空数组 `buttons=[]` 判断有坑

## 改动
- `user` 增加 `can_add_external`；前后端统一用该能力显隐/校验
- 补丁 `role_cap_org_add_v1`：给 super_admin / hr_specialist / dept_owner 补回 `org_add`
- 修正 `hasBtn` / `canAddExternal`

## 验证
- admin / maning（补权后）可创建
- 无 `org_add` 的账号返回「无「新建外部人员」权限」
