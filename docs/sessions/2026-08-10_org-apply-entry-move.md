# 会话：部门和人员入口调整

## 改动
1. 「部门和人员」去掉：账号、权限申请；代人申请；新建外部人员；直接绑定
2. 「自助申请」增加：代人申请账号、权限；新建外部人员（按按钮权限显隐）
3. `ALL_BUTTONS`：`org_add` / `proxy_apply` 挂到 `apply`；移除 `direct_bind` 按钮定义与默认角色 caps

## 文件
- `leuc-proto/static/index.html`
- `leuc-proto/db.py`
