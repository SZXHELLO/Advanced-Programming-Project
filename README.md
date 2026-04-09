# SDOC Editor (古籍在线编辑器)

## 1. 项目说明
本项目是一个古籍整理系统，包含前端静态页面与 Node.js 后端服务：
- 前端负责图片/PDF 导入、标注编辑、造字、XML 导出、项目管理。
- 后端负责用户鉴权、项目快照存储、造字存储、导出文件存储、OCR 接口转发、多人协同通信。

## 2. 前后端项目结构（树结构）

```text
sdoc-editor(6.0) -innerWebConnect/
├─ .cursor/
├─ answer.md
├─ handbook.md
├─ request.md
├─ main.py
├─ index.html
├─ login.html
├─ upload.html
├─ edit.html
├─ createChar.html
├─ exportXml.html
├─ project.html
├─ css/
│  └─ common.css
├─ js/
│  ├─ config.js
│  └─ common.js
└─ backend/
   ├─ .env
   ├─ .env.example
   ├─ package.json
   ├─ package-lock.json
   ├─ db.js
   ├─ server.js
   ├─ utils/
   │  └─ dataUrl.js
   └─ node_modules/  (依赖目录，安装后生成)
```

## 3. 前后端连接方式

### 3.1 API 基址配置（前端 -> 后端）
前端通过 js/config.js 中的全局变量定义后端地址：

```js
window.SDOC_API_BASE = "http://172.20.10.5:8000";
```

各页面脚本通过以下方式读取：

```js
const API_BASE = window.SDOC_API_BASE || "http://localhost:8000";
```

然后统一调用 REST API，例如：
- `${API_BASE}/api/auth/login`
- `${API_BASE}/api/projects`
- `${API_BASE}/api/custom-chars`
- `${API_BASE}/api/exports`

### 3.2 HTTP 通信（REST）
前端使用 fetch 发起请求，后端使用 Express 提供 JSON API。

典型流程：
1. 登录：POST /api/auth/login
2. 带 sessionId 调用业务接口
3. 保存项目：POST /api/projects
4. 导出 XML：POST /api/exports

后端已启用 CORS（origin: *），支持前后端分端口联调。

### 3.3 鉴权方式
登录成功后，前端会把 session 信息存入 localStorage。
后续请求使用 Bearer Token（sessionId）放在 Authorization 头中。

示例：

```http
Authorization: Bearer <sessionId>
```

### 3.4 多人协同连接（WebSocket）
协同编辑使用 WebSocket：
- 后端路径：/ws/collab
- 前端会把 API_BASE 转成 ws/wss，再拼接 projectId 与 sessionId

连接格式：

```text
ws://<backend-host>:<port>/ws/collab?sessionId=<sid>&projectId=<pid>
```

主要同步事件包括：
- annotation_add / annotation_update / annotation_delete
- pages_replace
- presence（在线人数与成员）

### 3.5 OCR 与图像能力连接
前端上传当前页图片（FormData）到后端：
- POST /api/recognize-text
- POST /api/segment-regions

后端再调用百度 OCR（通过 .env 中 API Key/Secret）。

## 4. 快速启动

### 4.1 启动后端
在 backend 目录执行：

```bash
npm install
npm run dev
```

默认端口：8000（可在 backend/.env 中修改 PORT）。

### 4.2 配置数据库与 OCR
参考 backend/.env.example 创建/修改 backend/.env：
- MYSQL_HOST
- MYSQL_PORT
- MYSQL_USER
- MYSQL_PASSWORD
- MYSQL_DATABASE
- BAIDU_OCR_API_KEY
- BAIDU_OCR_SECRET_KEY

### 4.3 启动前端（静态页面）
前端是纯静态页面，需用静态服务器打开（不要直接双击 html）。

可选方式：

```bash
# 在项目根目录
python -m http.server 8080
```

然后访问：
- 前端：http://127.0.0.1:8080/login.html
- 后端健康检查：http://127.0.0.1:8000/api/health

如果是局域网联调，请把 js/config.js 中的 SDOC_API_BASE 改成可访问的后端地址。

## 5. 常见联调检查

1. 后端是否启动：访问 /api/health 返回 {"ok": true}
2. 前端 API_BASE 是否指向正确 IP:PORT
3. MySQL 连接参数是否正确
4. 协同编辑时，WebSocket 是否成功连接到 /ws/collab
5. OCR 失败时，检查百度 OCR Key/Secret 是否配置

## 6. 关键接口总览
- 认证：/api/auth/register, /api/auth/login, /api/auth/me, /api/auth/logout
- 项目：/api/projects, /api/projects/:projectId/latest, /api/projects/:projectId/download
- 造字：/api/custom-chars, /api/custom-chars/next-unicode
- 导出：/api/exports, /api/exports/:exportId
- AI：/api/recognize-text, /api/segment-regions
- 协同：/api/collab/status, /ws/collab
