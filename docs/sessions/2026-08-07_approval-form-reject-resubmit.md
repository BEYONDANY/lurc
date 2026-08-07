# 会话：审批详情表单 + 驳回改单重提

## 目标

1. 流程详情展示申请表单全部内容  
2. 驳回支持选择目标节点；驳回后可编辑并再次提交  

## 实现

- `build_apply_form_fields`：把 `todos.meta` 转可读字段  
- 驳回目标增加 `0=申请人（修改后重提）`  
- `POST /api/todo/<id>/resubmit`：改单后直达 `reject_from_step`  
- 详情页展示申请内容；驳回待办可编辑并「修改并再次提交」  

## 验证

- 代发起账号延期 → 详情可见表单字段  
- 驳回至申请人 → `applications.status=returned`  
- 申请人改 `days=60` 重提 → 回到原驳回人待审  
