# Minibot Web Frontend

现代化的 Minibot Web 控制面板，提供对话管理和 Agent 管理功能。

## ✨ 特性

- 🎨 **美观的 UI**：基于 TailwindCSS 的现代化设计
- 💬 **实时对话**：WebSocket 支持流式回复
- 📚 **会话管理**：查看、切换、删除历史会话
- 🤖 **Agent 管理**：创建、编辑、删除持久化子 Agent
- ⚙️ **模型配置**：灵活配置 API Key、Base URL、Model

## 🏗️ 架构说明

**方案选择：方案 A（本地 BFF）**
- 前端：React 18 + TypeScript + Vite + TailwindCSS
- 后端：Node.js Express（仅绑定 127.0.0.1）
- 通信：WebSocket（对话）+ REST API（配置/会话/Agent管理）

**会话策略：策略 2（简版）**
- 侧栏会话列表为已完成对话的归档视图（只读）
- 新对话始终创建新 WebSocket 连接
- 会话文件由 minibot 后端自动生成和管理

## 🚀 快速开始

### 前置要求

1. **安装 Node.js**（建议 v18+）
2. **启动 minibot gateway**
   ```bash
   minibot gateway
   ```

3. **启用 Web 对话用的 WebSocket 通道（必做）**

   `minibot gateway` 会启动 QQ、微信等**已启用**的通道，但 **Web 前端连的是单独的 `channels.websocket` 服务**，默认是关闭的；未启用时界面会一直显示「未连接」，与 gateway 是否在跑无关。

   在 **`%USERPROFILE%\.minibot\config.json`**（或你的 `MINIBOT_` 配置路径）里加入或修改（与 minibot 仓库 `docs/WEBSOCKET.md` 一致）：

   ```json
   "channels": {
     "websocket": {
       "enabled": true,
       "host": "127.0.0.1",
       "port": 8765,
       "path": "/",
       "websocketRequiresToken": false,
       "allowFrom": ["*"],
       "streaming": true
     }
   }
   ```

   - **`enabled`: true** 才会在日志里出现：`WebSocket server listening on ws://127.0.0.1:8765/`  
   - 默认 **`websocketRequiresToken` 为 true** 且未配置 **`token`** 时，浏览器无 token 连接会被 **401 拒绝**。本地开发请设为 **`false`**，或按文档配置静态 token 并在连接 URL 带 `?token=...`。

   修改后请 **重启** `minibot gateway`。

### 联调命令摘要

```bash
# 终端 1：BFF
cd minibot-web/backend && npm run dev

# 终端 2：前端
cd minibot-web/frontend && npm run dev

# 终端 3：minibot（修改 config 并启用 websocket 后）
minibot gateway
```
