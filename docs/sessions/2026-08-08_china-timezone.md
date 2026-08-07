# 会话：业务时间改为中国时区

## 目标

列表/待办时间时区不对 → 统一为 Asia/Shanghai（中国）。

## 改动

- `now_cn` / `now_ts`：显式按中国时区生成时间戳
- Docker / compose / Postgres：`TZ=Asia/Shanghai`
- 待办消息触发器：`NOW() AT TIME ZONE 'Asia/Shanghai'`
- 依赖增加 `tzdata`（Windows ZoneInfo）

## 注意

需重建/重启容器后生效：`docker compose up -d --build`
