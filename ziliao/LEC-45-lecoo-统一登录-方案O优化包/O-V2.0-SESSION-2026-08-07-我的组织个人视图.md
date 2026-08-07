# O-V2.0 SESSION · 我的组织：本人 + 所属部门全路径

- 日期：2026-08-07
- 原型：`leuc-proto/`

## 目标

个人中心「我的部门」默认只展示本人与所属部门全路径；有管理权限者另开「部门管理」页签保留全树能力。

## 已实现

### 后端

- `dept_ancestor_chain` / `dept_path_label`
- `GET /api/org/overview?scope=mine|manage`（默认 `mine`）
  - mine：路径链部门 + 仅本人
  - manage：原全树逻辑；无 `can_manage` 返回 403
- `/api/home`：`org_tree` / `dept_path` / `my_dept_id` 改为本人路径

### 前端

- 「我的」/「部门管理」页签（仅 `can_manage` 显示管理页签）
- 首页改为路径 + 本人，不再展示全公司架构
- 管理相关 overview 调用统一带 `scope=manage`

## 验证

- `xuhaohao`：path=`来酷科技有限公司 / 产品营销 / BTIT`，members 仅本人；manage 403
- `maning`：mine 有路径；manage 可看可管范围
- `admin`：mine 无部门；manage 275 部门
