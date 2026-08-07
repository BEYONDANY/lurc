# 会话：角色与人员多对多

日期：2026-08-07

## 目标

用户 ↔ 角色改为多对多；「角色与权限」拆成按用户 / 按角色两视图；部门负责人与角色解耦。

## 变更

1. `user_roles` 表 + 从 `users.role` 迁移；`sync_primary_role` 回写主角色
2. `row_user` 多角色菜单/按钮并集；门禁改 `user_has_role`
3. API：`/api/admin/role-users`、`users/<id>/roles`（GET/PUT/POST/DELETE）
4. 设负责人 / LeOrg 回填只写部门属性，不自动绑 `dept_owner`
5. 前端：按用户（多选角色 + 组织选人添加）| 按角色（名单 + 解除本角色）

## 验证

1. 一人绑 `finance`+`system_owner` → 登录侧栏/按钮为并集
2. 设部门负责人后 `user_roles` 不变；`find_approver` 仍读 `owner_user_id`
3. 普通员工不在「按用户」列表；组织筛选添加后再绑角色即出现
