# 会话：同系统多行申请开通多个账号

## 问题
申请明细同一系统两行（不同权限）时，开通弹窗仍只显示 1 行，只能开 1 个账号。

## 原因
开通目标按 `system_id` 去重，同系统多行被合并。

## 改动
- `get_provision_targets`：按明细行生成目标（`line_key`）
- `_match_provision` / `provision_account_apply_multi`：按行匹配，禁止池账号复用
- 前端开通弹窗按 `line_key` 选账号并提交

## 验证
todo#67（来酷ERP 两行）→ `provision_targets.length === 2`；分别选 `liuyi_erp` / `chener01` 开通成功。
