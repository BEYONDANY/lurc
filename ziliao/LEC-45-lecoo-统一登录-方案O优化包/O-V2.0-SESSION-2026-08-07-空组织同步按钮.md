# 会话记录 · 2026-08-07 · 空组织仍显示同步部门

## 问题

清空部门后「增量/全量同步」区域消失。

## 原因

`can_manage = bool(manage_ids)`；空树时 `manage_ids` 为空，前端用 `canManage && showSync` 隐藏同步区。

## 改动

- `org_overview`：超管/人事/`manage_all_org` 在空树时仍 `can_manage=true`
- 前端：`canManage = data.can_manage || canAllOrg`
