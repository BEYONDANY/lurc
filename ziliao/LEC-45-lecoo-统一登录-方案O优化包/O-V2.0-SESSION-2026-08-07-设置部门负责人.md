# 会话：我的组织 · 设置部门负责人

日期：2026-08-07

## 目标
在「我的组织」中支持设置部门主负责人与额外负责人。

## 实现要点
- 按钮能力：`org_set_owner`（人事 / 超管 / 部门负责人默认具备）
- API：`POST /api/org/departments/<dept_id>/owner`，body：`owner_user_id`、`extra_owner_ids`
- 写库：`departments.owner_user_id` + `dept_extra_owners`；普通员工升为 `dept_owner`
- 前端：选中部门展示负责人卡片 +「设置负责人」弹窗（主负责人下拉、额外多选、筛选）

## 验证
- 超管登录设置「产品研发」主负责人朱国用、额外牟宗山 → 成功
- 清空额外负责人 → 成功

## 手测路径
1. 用 `admin` / 人事账号登录 → 我的组织
2. 选中部门 →「设置负责人」→ 保存
3. 刷新后树/卡片显示负责人姓名
