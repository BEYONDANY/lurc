# 会话记录 · 角色权限树勾选（2026-08-07）

## 目标

「角色与权限」配置区由平铺复选框改为树形勾选，区分菜单与按钮，勾菜单联动下属按钮。

## 改动摘要

- `leuc-proto/static/index.html`：`renderAdminRoles` 改为分组→菜单→按钮树；`onRoleMenuToggle` / `onRoleBtnToggle` 联动；样式 `.role-perm-tree`
- 文档：`O-V2.0-UPDATE-RECORD.md`、`V2.0/用户中心功能模块整理-O-V2.1.md`

## 手测

1. 超管进入「系统设置 → 角色与权限」
2. 勾选「我的组织」菜单 → 下属按钮应全部勾上
3. 取消某菜单 → 下属按钮应全部取消
4. 单独勾某按钮 → 父菜单自动勾上
5. 保存后刷新，勾选状态与角色人数统计一致
6. 勾选框与名称紧贴单行显示（如「个人中心首页 菜单」），无大间距/换行

## 布局修正

- 权限树移出 `.field`，避免全局 `.field label{display:block}` 把勾选行撑乱
- `white-space:nowrap` + label `flex:0 1 auto`，checkbox 与名称间距 6px
