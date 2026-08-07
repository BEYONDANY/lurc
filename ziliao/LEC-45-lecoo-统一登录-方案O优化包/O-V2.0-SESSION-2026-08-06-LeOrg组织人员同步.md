# 会话：LeOrg 用户与组织同步

日期：2026-08-06

## 做了什么

- 对接 LeOrg OAuth2 + `/v1/organizations`、`/v1/employees`
- 新增 `leuc-proto/leorg_client.py`、本地配置 `data/leorg.local.json`（gitignore）
- `POST /api/hr/sync-pull` 改为真实同步（组织 upsert + 人员花名册/更新用户）
- `GET /api/leorg/status`；前端「从 LeOrg 同步」
- `departments.leorg_id` / `hr_sync_roster.leorg_emp_id` + migrate

## 配置

```bash
cp leuc-proto/.env.example leuc-proto/.env
# 填 LEORG_CLIENT_ID / LEORG_CLIENT_SECRET
```

文档：https://leorg-ai.lecoosys.com/api/docs/

## 变更（配置）

- 密钥改为项目根 `.env`（不再使用 `data/leorg.local.json`）

## 变更（清空组织）

- `POST /api/hr/org-clear`：清空部门树与普通员工，保留管理账号，重建根「来酷科技」
- 前端「我的组织」增加「清空组织」按钮
- 已执行一次清空：部门 245→1，员工删除 608，保留 8 个管理账号

## 变更（幂等增量 + 启动不重种）

- 根因：`main()` 每次 `init_db(force=True)` 会抹掉同步数据 → 改为 `force=False`
- `ensure_db` 不再因人数不足/软迁移异常整库重种
- 种子改为空根组织 + 6 个管理账号（人员靠 LeOrg）
- 同步：`mode=auto|full|incr`；增量用变更水位；组织/人员 upsert 幂等
- 按钮：增量同步 / 全量同步 / 清空组织
