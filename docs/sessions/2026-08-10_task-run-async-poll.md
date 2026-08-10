# 任务管理：立即执行异步轮询

## 需求
点击「立即执行」后局部刷新列表；状态展示「执行中」；有执行中记录时异步轮询至终态。

## 改动
- `app.py`：`POST /api/admin/tasks/<code>/run` 改为后台线程执行，立即返回 `status=running`；`running` 记录提前 commit
- `static/index.html`：任务/执行记录局部刷新；状态文案（执行中/成功/失败/跳过）；有 `running` 时约 1.5s 轮询至终态

## 验证
- POST `/run` 约 0.25s 返回 `running`（`run_id=16`）
- 随后轮询到 `ok` 终态；容器已重建
