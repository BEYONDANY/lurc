# 会话记录 · 2026-08-07 · 清空部门人员与审批数据

## 需求

清除全部部门、人员与审批相关数据。

## 改动

- `_clear_my_organization`：除部门/员工外，全局清空 `todos` / `applications` / `application_steps` / `grant_applications` / `messages` / 同步草稿
- 保留角色：`super_admin` / `hr_specialist` / `finance` / `system_owner` + `admin`；重建根部门「来酷科技」

## 执行结果

- 部门 275 → 1（来酷科技）
- 员工删除 938，保留 5：admin、gaojia、wuhongliang、changmingming、liyang
- 待办 42、申请 13 已清零
