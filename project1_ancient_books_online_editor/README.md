# SDOC Editor（古籍在线编辑器）

## 1. 项目说明

本项目是一个古籍整理系统，包含前端静态页面与 Node.js 后端服务：

- 前端：图片/PDF 导入、标注编辑、造字、XML 导出、项目管理。
- 后端：用户鉴权、项目快照存储、造字与导出记录、OCR 接口转发、多人协同（WebSocket）。

## 2. 项目文件结构（目录树）

以下为源码与配置的主要结构（`backend/node_modules` 为 `npm install` 后生成，未逐层展开）：

```text
project1/
├── README.md
├── main.py
├── index.html
├── login.html
├── upload.html
├── edit.html
├── createChar.html
├── exportXml.html
├── project.html
├── css/
│   ├── common.css
│   ├── surface-pages.css
│   └── project-members.css
├── js/
│   ├── config.js          # 后端 API 基址等前端配置
│   └── common.js          # 状态、鉴权、XML 生成、项目导入导出、OCR 调用等
└── backend/
    ├── package.json
    ├── package-lock.json
    ├── .env                 # 本地环境变量（勿提交密钥）
    ├── .env.example
    ├── server.js            # Express 入口：REST、WebSocket、路由挂载
    ├── db.js                # MySQL 连接池
    ├── sessionService.js    # 会话
    ├── collabRooms.js       # 协同房间
    ├── middleware/
    │   └── projectAuth.js
    ├── routes/
    │   └── projectManagement.js
    ├── migrations/
    │   └── 001_project_management.sql
    ├── utils/
    │   ├── dataUrl.js
    │   └── activityLogger.js
    └── node_modules/        # 依赖目录（安装后生成）
```

## 3. 《XML-V0.1》规范与当前实现方式

作业说明文档《XML-V0.1》定义了以 `article` 为根的 XML 结构（`head` / `content` / `view` / `sources` 等）。本仓库中的**导出实现**主要在前端 `js/common.js` 的 `XMLGenerator` 中拼接字符串；导出页为 `exportXml.html`，下载时可顺带调用后端 `POST /api/exports` 将 XML 落库。

### 3.1 根元素 `article`

| 规范要点 | 实现方式 |
|----------|----------|
| 属性 `id`、`type`（1 古文 / 2 现代文）、`version` | `generate()` 中输出 `id`（缺省为 `article_1`）、`type="1"`、`version="1.0"` |

### 3.2 `head`（文章元信息）

| 规范要点 | 实现方式 |
|----------|----------|
| `title`、`subtitle`、`authors`/`author`、`book`（含 `relation` 等）、`date`（`publish_date` / `writing_date`） | 当前实现：**`title`**（`name` 属性承载题目文本）、**`authors`/`author`**（单作者，`type="0"`）、**`book`**（`name`、`volume`、`pages` 为页数）、**`date`**（仅使用 `dynasty` 属性）。**未**按规范完整输出 `subtitle`、`relation`、`publish_date`/`writing_date` 等全部子元素与属性。 |

### 3.3 `content` 与正文层级

| 规范要点 | 实现方式 |
|----------|----------|
| `page_mode`：0 文本 / 1 单页 / 2 混合（三选一） | 固定输出 **`<content page_mode="1">`**，即**单页模式**。 |
| 文本模式下的 `section` → `subsection` → `paragraph` → `sentence` → `word` → `char` 等层级 | **未**按该层级生成。 |
| 单页模式下的 `page` → `panel` → `textfield` / `img` / `table` 等 | 按页生成 **`<page>`、`<panel>`**，其内按标注类型组织为 **`<singleCharacter>` / `<singleWord>` / `<sentence>`** 等区块，条目使用 **`<textfield>`**（带 `id`、`simplified`、`note`、`attrType` 等），子级通过嵌套 `textfield` 表达。**与规范中的标签命名与树形（如 `img`、`table` 独立节点）并不完全一致**，属于面向当前编辑器标注模型的简化/映射实现。 |
| 遇到「标题」类标注时对多页包裹 | 若某页存在顶层 **「标题」** 标注，会从该页起用 **`<title name="" note="">`** 包裹后续页，直至下一个标题；与规范中的 `title`/`subtitle` 元素含义部分对应。 |

### 3.4 `view`（SVG 呈现层）

| 规范要点 | 实现方式 |
|----------|----------|
| `view` → 多页 `svg`，内含 `g`、`text`、`image`、`path`、`rect`、`line` 等与 SVG 对齐的呈现信息 | `XMLGenerator.generate()` 内曾包含根据标注生成 **`<view>` / `<svg>` / `<rect>` / `<line>`** 的逻辑，**目前整段以注释形式保留，默认导出中不包含 `view`**。编辑页仍可在画布上对框线、下划线、高亮、删除线、斜线等做标注，但**不会写入当前导出的 XML**。若需符合规范的 `view` 层，需取消注释并按规范补全 `tspan`/`text` 与 `content` 中字/图 id 的对应关系。 |

### 3.5 `sources`（原始影像来源）

| 规范要点 | 实现方式 |
|----------|----------|
| 根上 `type`：1 图片 / 2 PDF；每页 `source` 的 `src`、`pageno` | 输出 **`<sources type="…">`**：`type` 由项目字段 `sourcesType`/`sourceType` 决定（**2** 为 PDF，否则为 **1** 图片）。每页 **`<source src="…" pageno="…"/>`**：`src` 优先使用页面保存的相对路径字段，否则回退为形如 `images/page_{页码}.png`；`data:`/`http(s):`/`blob:` 等不当作可落盘的相对路径导出。 |

### 3.6 与 `view`/`content` 的 id 对应（规范中的关联）

规范要求 `char`/`img` 等与 `view` 内图形元素的 `id` 一致。当前导出以 **`textfield` 的 `id`** 为主键之一；**在 `view` 未启用的情况下，未形成完整的「content 与 view 双向 id 对齐」闭环**。

---

## 4. 前后端连接方式

### 4.1 API 基址（前端 → 后端）

在 `js/config.js` 中设置：

```js
window.SDOC_API_BASE = "http://172.20.10.5:8000";
```

各页使用：

```js
const API_BASE = window.SDOC_API_BASE || "http://localhost:8000";
```

### 4.2 HTTP（REST）

前端使用 `fetch`，后端为 Express JSON API。典型流程：登录 → 携带 `Authorization: Bearer <sessionId>` 调用项目、造字、导出等接口。

### 4.3 多人协同（WebSocket）

- 路径：`/ws/collab`
- 连接示例：`ws://<host>:<port>/ws/collab?sessionId=<sid>&projectId=<pid>`
- 同步事件包括标注增删改、页面替换、在线成员等（见 `backend/collabRooms.js` 与前端协同逻辑）。

### 4.4 OCR

前端将当前页图片以 `FormData` 提交至 `POST /api/recognize-text`、`POST /api/segment-regions`，后端转发百度 OCR（密钥配置在 `.env`）。

## 5. 快速启动

### 5.1 启动后端

在 `backend` 目录：

```bash
npm install
npm run dev
```

默认端口 **8000**（可在 `backend/.env` 中修改 `PORT`）。

### 5.2 数据库与 OCR

复制 `backend/.env.example` 为 `backend/.env`，配置 MySQL 与 `BAIDU_OCR_*` 等变量。

### 5.3 启动前端（静态资源）

勿直接双击打开 HTML，请在项目根目录用静态服务器，例如：

```bash
python -m http.server 8080
```

访问示例：

- 登录页：`http://127.0.0.1:8080/login.html`
- 健康检查：`http://127.0.0.1:8000/api/health`

局域网调试时，将 `js/config.js` 中的 `SDOC_API_BASE` 改为可访问的后端地址。

## 6. 常见联调检查

1. 后端是否启动：`GET /api/health` 返回 `{"ok": true}`
2. 前端 `API_BASE` 是否指向正确的 IP 与端口
3. MySQL 连接与迁移是否就绪
4. 协同编辑时 WebSocket 是否连上 `/ws/collab`
5. OCR 失败时检查百度 OCR Key/Secret

## 7. 关键接口总览

- 认证：`/api/auth/register`、`/api/auth/login`、`/api/auth/me`、`/api/auth/logout`
- 项目：`/api/projects`、`/api/projects/:projectId/latest`、`/api/projects/:projectId/download` 等（详见 `server.js` 与 `routes/projectManagement.js`）
- 造字：`/api/custom-chars`、`/api/custom-chars/next-unicode`
- 导出：`POST /api/exports`、`GET /api/exports/:exportId`
- AI：`/api/recognize-text`、`/api/segment-regions`
- 协同：`/api/collab/status`、`/ws/collab`
