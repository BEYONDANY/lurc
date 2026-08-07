# 会话：LeOrg 同步部门负责人

日期：2026-08-07

## 核对（接口正确）
- 徐好好 `org_id=241` BTIT，`manager_emp_id=591` → 马宁
- 上级 产品营销 `manager_emp_id=60` → 吴锦志
- 直属/一级解析与预期一致

## 实现
- `departments.manager_leorg_emp_id`：存 LeOrg `manager_emp_id`
- `_sync_leorg_organizations` 写入该字段
- `_resolve_dept_owners_from_leorg`：映射到 `users.leorg_emp_id` → `owner_user_id`，员工升 `dept_owner`
- 触发点：`/api/hr/sync-pull` 结束后；花名册确认建人后

## 验证
- 全量组织 upsert 后：`resolved=270, pending=0`
- BTIT 负责人马宁；产品营销（徐好好上级）吴锦志
- 徐好好直属=马宁，一级=吴锦志
