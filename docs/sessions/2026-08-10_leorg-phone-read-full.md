# 会话：LeOrg 手机号明文同步

## 结论
- 本地 users 几乎无手机号，因 LeOrg 默认返回脱敏号（如 `136****1644`），`extract_leorg_phone` 会跳过
- LeOrg OpenAPI 提供 scope `emp:read_full`（手机号不脱敏），但当前 OAuth client 未开通；请求该 scope 会被静默降级为 `org:read emp:read`

## 改动
- `leorg_client.py`：默认 scope 含 `emp:read_full`；status 探测实际 token scope 并给出 `phone_warning`
- `.env` / `.env.example`：`LEORG_SCOPE=org:read emp:read emp:read_full`
- 部门和人员同步区：展示手机号未同步警告

## 待运维
在 LeOrg 管理端为 LEUC 的 client 开通 `emp:read_full`，再全量同步即可回填明文手机号。
