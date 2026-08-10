# 新建外部人员走敏感同款审批链

## 需求
新建外部人员审核流程与含敏感权限一致。

## 实现
- `POST /api/dept/members` 不再立即建人，发起 `external_create` 申请
- 审批链：`materialize_approval_chain(sensitive)` + 申请人确认（直属→一级→财务→确认）
- 财务通过进入确认前 `provision_external_create` 落库并发信
- 无审批节点时自动创建；详情表单展示姓名/登录名等
- 前端按钮「提交审批」，提交后进「我发起的」
