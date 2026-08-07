# 会话：同步后人员挂正确部门（先部门后人）

日期：2026-08-07

## 问题

`gaojia` / `wuhongliang` / `changmingming` / `liyang` 清空部门后保留账号 `dept_id=null`；
同步时按工号/邮箱匹配不上（本地用户名是拼音），花名册确认又新建 `gaojia1` 等，且常误挂根部门。

## 修复

1. **顺序**：同步始终先 `_sync_leorg_organizations`，再人员；apply 内也先落部门再 remap 人员；apply 后 `_realign`。
2. **匹配**：`_find_user_for_leorg_emp` 增加拼音用户名 / 唯一同名，优先挂回保留账号。
3. **建人**：`hr_sync_init` 先挂已有账号，不再盲目新建 `xxx1`。
4. **数据**：已把四人挂到 LeOrg 部门（BTIT / 财法管理 / 招聘管理），关闭重复的 `*1` 账号。

## 手测

全量同步或刷新「部门和人员」：上述账号应显示正确部门，不再为 null。
