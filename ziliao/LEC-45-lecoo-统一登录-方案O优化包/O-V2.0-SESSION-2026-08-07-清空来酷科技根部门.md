# 会话记录 · 2026-08-07 · 清空「来酷科技」根部门

## 需求

清空组织时连根部门「来酷科技」一并清除，不再重建空根。

## 改动

- `_clear_my_organization`：删除全部部门后不插入「来酷科技」；保留账号 `dept_id=NULL`
- `ensure_db`：空部门树 + 仍有 admin/超管时不整库重种
- `seed`：空部门树播种；跳过依赖 `dept_id` 的花名册；修复 systems 先删后插；待办/账号池适配仅超管

## 验证

- `init_db(force=True)` 成功：departments=0，无「来酷科技」
- `ensure_db` 后仍为空树
- 插入假根再调清空 → departments=[]，保留 5 个管理账号
