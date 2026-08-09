# 会话：多音姓拼音校正与账号/通讯录权限详情

## 目标

1. 对照通讯录姓氏读音，修复多音姓导致的错误拼音用户名（曾/解/翟/查等）
2. 账号延期展示本人全部账号与权限，详情支持「全项详情」
3. 通讯录人员列后增加「详情」，展示各系统权限（含敏感标识）
4. 「按角色分配」仅 `role_assign` / 超管可见

## 改动

- 新增 `surname_pinyin.py`；`db.name_to_pinyin` 与启动时软修用户名
- `expand_account_permissions` / `my_systems` / 延期表单增强
- `static/index.html`：延期全项、通讯录详情、分配按钮鉴权收紧

## 说明

- 示例「黎洋（liyang）」姓名与拼音本身正确；同拼还有「李杨」冲突项
- 已校正如 `ceng*`→`zeng*`、`jie*`→`xie*`、`dihai*`→`zhai*`、`cha*`→`zha*`
