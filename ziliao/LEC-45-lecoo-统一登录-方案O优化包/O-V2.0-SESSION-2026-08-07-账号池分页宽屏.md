# O-V2.0 会话 · 账号池分页与宽屏（2026-08-07）

## 需求

1. 子系统账号池加分页（北森同步后数据量大）
2. 整体改宽屏风格（参考 [awesome-design-md](https://github.com/VoltAgent/awesome-design-md) Linear / Intercom 轻产品台）

## 改动

1. `GET /api/sys-accounts/catalog` 支持 `page` / `page_size`，返回 `pagination`
2. 前端：首页/上一页/下一页/末页、每页 20/50/100；筛选重置页码
3. 布局：`--stage-max:1680px`，侧栏加宽、主区留白加大；账号表粘性表头 + 横向滚动

## 文件

- `leuc-proto/app.py`
- `leuc-proto/static/index.html`
