# 会话：任务执行记录 / 审计日志 / 同步变化明细

## 目标

1. 任务「立即执行」写入执行记录  
2. 系统操作/审计日志  
3. 同步人员/部门的详细变化记录  

## 实现要点

- 新增表：`task_run_logs`、`audit_logs`、`sync_change_logs`（`leuc_ops.ensure_ops_tables`）
- 统一入口：`_execute_leorg_sync_job`（手动 / 定时）
- API：
  - `GET /api/admin/tasks/<code>/runs`
  - `GET /api/admin/task-runs/<id>/changes`
  - `GET /api/admin/audit-logs`
- 前端：任务页执行记录 + 变化明细弹窗；侧栏「审计日志」
- 菜单：`admin_audit` 赋给超管 / 人事

## 验证

- Docker 重建后登录 admin，调用立即执行成功（`run_id=1`）
- 执行记录与审计 `task.run` 已落库
- 无真实字段变化时 `change_count=0`（仅记录真实 diff）
