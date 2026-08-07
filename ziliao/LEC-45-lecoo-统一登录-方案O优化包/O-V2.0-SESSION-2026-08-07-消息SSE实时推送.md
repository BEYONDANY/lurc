# 会话：消息 SSE 实时推送（弹出 + 声音）

日期：2026-08-07

## 背景

原先消息靠约 2.5s 轮询 `/api/chat/poll`，不够「即时」。用户要求改成实时推送，并保留右下角弹出与提示音。

## 改动

1. **后端** `GET /api/chat/stream`：SSE，约 0.8s 扫库，有新消息立即 `event: message` 推送；心跳 `ping`；Flask `threaded=True`。
2. **前端** `startChatPoll`：优先 `EventSource` 连 SSE，失败回退轮询；收到消息走 `pushNotifyCard` + `playNotifySound`。
3. **音频解锁**：首次 `pointerdown`/`keydown` 调用 `unlockNotifyAudio`（浏览器手势策略）。
4. **登出**：关闭 `_chatEs`，避免残留连接。

## 手测

1. `./run.sh` 重启服务
2. 登录后强制刷新，Network 应见 `/api/chat/stream`（pending）
3. 点「演示系统消息」→ 约 1 秒内右下角弹出 + 提示音
