# 会话：审批处理后留在待处理

## 目标

待办审批通过/驳回后，不要跳到「已完成」，继续留在「待处理」。

## 改动

- `submitDecideTodo`：`todoTab` 固定为 `pending`
