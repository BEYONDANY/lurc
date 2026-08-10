# 个人中心：业务系统导航 + 系统管理图标/链接

## 需求
把 `/demo/home` 业务系统导航落到登录后首页；系统管理可配链接与图标。

## 实现
- `systems` 增加 `nav_icon` / `home_url` / `show_in_nav`
- 个人中心首页「业务系统导航」卡片；可打开完整 `/demo/home`
- 业务系统管理表单可编辑图标、首页链接、是否显示在导航
- `/api/home` 返回 `nav_systems`；门户接口同步返回图标与 launch_url
