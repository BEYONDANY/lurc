# 会话：Docker 本地启动

- 日期：2026-08-07
- 范围：`leuc-proto` Docker 构建与启动

## 做了什么

1. 用 `docker compose` 构建并启动 `leuc-proto`
2. Docker Hub 不可达时，经 `docker.m.daocloud.io` 拉取 `python:3.12-slim` 并本地打标签
3. 修复 Windows CRLF 导致 `docker-entrypoint.sh` 无法 exec 的问题（Dockerfile 构建时 `sed` 去 `\r`）

## 结果

- 容器 `leuc-proto` 监听 `0.0.0.0:5055`
- `http://127.0.0.1:5055` 返回 200
