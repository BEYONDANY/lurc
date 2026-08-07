# 会话：审批详情申请内容按表单行展示

## 目标

流程审核「申请内容」看不懂 → 改成与申请表单一致的按行/明细表展示。

## 改动

- `build_apply_form_view`：按延期/申请/关闭组装 `section_title` + 摘要行 + 明细表
- 前端 `renderApplyFormViewHtml`：标签行 + 表格，去掉 flow_code 等技术字段
- 延期详情含：申请人、当前有效期、延期天数、敏感说明、关联账号表

## 验证

- 账号延期待办详情：`section_title=延期明细`，行字段可读
