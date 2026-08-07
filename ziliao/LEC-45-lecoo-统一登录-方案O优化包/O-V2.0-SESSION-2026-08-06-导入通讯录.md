# 会话：导入来酷科技通讯录到我的组织

日期：2026-08-06

## 做了什么

- 从 `ziliao/组织人员/来酷科技通讯录.xlsx` 生成 `leuc-proto/data/roster_org.json`
- `seed()` 导入部门树 + 人员；保留原演示账号（挂 BTIT / 根）
- 再生脚本：`leuc-proto/scripts/gen_roster_org.py`

## 数据量

- 部门约 223，人员约 610 + 14 演示账号
- 通讯录登录：`e`+工号 / `123456`（如徐好好 `e00001853`）
