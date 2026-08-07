# 会话：清空重导通讯录（多级组织）

日期：2026-08-06

## 做了什么

- 以 `ziliao/组织人员/来酷科技通讯录 (3).xlsx` 重新生成 `leuc-proto/data/roster_org.json`
- `init_db(force=True)` 清空 SQLite 后重种子
- 组织按「部门路径」拆分写入 `departments.parent_id`，支持无限级（本次最深 5 级）
- 演示账号 BTIT 挂载路径改为 `来酷科技/产品营销/BTIT`（兼容旧扁平路径）

## 数据量

- 部门 245，通讯录人员 610（同工号多行去重）+ 管理账号 6
- 登录：姓名拼音全拼 / `123456`（如徐好好 `xuhaohao`）；工号写入 itcode
- 同名冲突：拼音后自动加 2、3…

## 再生命令

```bash
cd leuc-proto
python3 scripts/gen_roster_org.py
python3 -c "from db import init_db; init_db(force=True)"
```
