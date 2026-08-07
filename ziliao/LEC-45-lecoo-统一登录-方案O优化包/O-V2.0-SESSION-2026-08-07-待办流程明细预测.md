# O-V2.0 SESSION · 申请/待办：当前审核人 · 流程明细 · 流程预测

- 日期：2026-08-07
- 原型：`leuc-proto/`

## 目标

在申请与待办中展示当前审核人、完整审批时间线，以及后续未走步骤的流程预测；复用 `applications` / `application_steps`，不新建审批表。

## 已实现

### 后端（`app.py`）

- `build_application_flow` / `build_todo_flow`：拼装时间线（`done|current|forecast`）、当前审核人、进度
- 增强 `serialize_todo`：列表附带 `current_approver`、`progress`、`progress_label`、`forecast_count`
- `GET /api/todo/<id>/flow`：详情完整流程（经办人 / 发起人 / 申请人 / 超管）
- `POST /api/apply/preview-flow`：提交前预测（调用 `materialize_approval_chain`，不落库）

### 前端（`static/index.html`）

- 待办列表：当前审核人、进度列；「详情」弹窗时间线 + 流程预测
- 自助申请：账号延期卡片内预览审批链
- 账号/权限申请弹窗：选系统 / 敏感后刷新「预计审批链」
- 账号关闭弹窗：勾选关闭项后预览审批链

## 验证

- `preview-flow`：延期单步；含敏感账号申请多级链正常
- 多级申请落库后：列表 `1/3 · 直属领导`，`/flow` 返回 timeline + forecast
- 无 `application_id` 的单步待办：assignee 作为当前审核人

## 说明

- 流程预测为建单时已解析步骤，不实时重算组织变更（与现逻辑一致）
