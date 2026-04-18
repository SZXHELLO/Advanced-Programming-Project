| 需求条目 | 采用技术 | 代码实现位置 | 结论 |
|---|---|---|---|
| （1）扫描页按页码形成多张图片 | HTML5 File API：读取本地图片和 PDF 文件；拖拽上传交互：通过 dragover/drop 提升批量导入效率；PDF.js：将 PDF 每一页渲染到 Canvas，再转成图片数据；Canvas toDataURL：把页面固化为可编辑图片；前端状态管理（AppState + localStorage）：保存 pages 数组与 pageNo，实现“按页码多图”持久化。 | 上传入口与文件类型限制在 [upload.html](upload.html#L544)、上传处理在 [upload.html](upload.html#L674)，图片处理在 [upload.html](upload.html#L785)，PDF 拆页在 [upload.html](upload.html#L813)，入库到页面状态在 [upload.html](upload.html#L866)；编辑页也支持导入在 [edit.html](edit.html#L2473)、[edit.html](edit.html#L2621) | 已实现 |
| （2）在线字/句/段选定，标记方式（颜色、画框、划线），右侧属性输入简体字 | Canvas 分层架构：主图层(mainCanvas)与标注层(annotationLayer)分离，便于重绘与交互；工具状态机：通过 setTool 在“选择/框选/套索”等模式切换；结构化标注模型：每条标注包含 type、rect、color、attrType、simplifiedChar、note 等字段；属性面板双向绑定：输入框和颜色面板实时写回当前标注；文本输入依赖浏览器输入法：在 input/textarea 中直接支持简体输入；颜色与类型映射策略：字/词/句子对应不同视觉编码，保证整理结果可读。 | 工具切换在 [edit.html](edit.html#L1760)，属性类型在 [edit.html](edit.html#L1820)，简体字输入框在 [edit.html](edit.html#L906)，简体字写回标注在 [edit.html](edit.html#L2398)，颜色面板在 [edit.html](edit.html#L913)，字/词/句子按钮在 [edit.html](edit.html#L964)、[edit.html](edit.html#L968)、[edit.html](edit.html#L972) | 主体已实现；“划线”相关按钮代码存在但当前在注释块内，见 [edit.html](edit.html#L780) |
| （3）无简体对应字的造字页，按 Unicode 分配编码并建立字样图片与编码关系 | 自定义字形编辑器（Canvas 绘制）：画笔/橡皮擦直接生成字形像素；Unicode 私有区分配策略：以后端查询 max(unicode)+1 方式生成下一个编码，避免前端冲突；图像序列化：字形缩放到标准尺寸并转 DataURL，便于传输和预览；REST 持久化：将 unicode、name、image_mime、image_blob 存入 MySQL；字符资产管理：支持查询、删除、回显，形成可复用造字库。 | 造字画布与保存按钮在 [createChar.html](createChar.html#L395)、[createChar.html](createChar.html#L463)，请求下一个 Unicode 在 [createChar.html](createChar.html#L769)，保存造字在 [createChar.html](createChar.html#L776)，后端 Unicode 分配接口在 [backend/server.js](backend/server.js#L664)，后端保存接口在 [backend/server.js](backend/server.js#L678)，表结构在 [backend/db.js](backend/db.js#L61) | 已实现 |
| （4）整理结果按 XML 输出 | XMLGenerator 模板化生成：按项目元数据、页面结构、标注数据拼装 XML 文档；XML 转义机制：escapeXml 防止特殊字符破坏结构；可视化预览：导出前在页面实时显示 xmlPreview，便于检查；下载导出：Blob + downloadFile 生成本地 xml 文件；导出留档：通过 /api/exports 把 XML 原文保存到数据库，支持追溯与复取。 | XML 生成器在 [js/common.js](js/common.js#L377)、转义在 [js/common.js](js/common.js#L553)；导出页生成与预览在 [exportXml.html](exportXml.html#L547)、[exportXml.html](exportXml.html#L478)；下载与落库在 [exportXml.html](exportXml.html#L567)、[exportXml.html](exportXml.html#L577)；后端导出接口在 [backend/server.js](backend/server.js#L890)，导出表在 [backend/db.js](backend/db.js#L74) | 已实现 |
| 扩展（1）Node.js 服务器与前后端交互，造字/生成文件入库 | Node.js + Express：承载统一 API 网关；CORS：支持前后端分端口联调；Multer(memoryStorage)：处理图片上传（OCR/分辨率读取不落盘）；MySQL2 Promise 驱动：异步数据库访问；连接池机制：复用连接提升吞吐并减少连接开销；dotenv：环境变量管理数据库与 OCR 密钥；Axios：服务端调用第三方 OCR；REST 资源建模：projects/custom-chars/exports 分域接口；初始化建表策略：initDb 启动时自动补齐核心表结构。 | 服务启动与中间件在 [backend/server.js](backend/server.js#L1)，依赖在 [backend/package.json](backend/package.json#L1)；项目快照接口在 [backend/server.js](backend/server.js#L726)，造字接口在 [backend/server.js](backend/server.js#L641)、[backend/server.js](backend/server.js#L678)，导出接口在 [backend/server.js](backend/server.js#L890)；连接池与建表在 [backend/db.js](backend/db.js#L4)、[backend/db.js](backend/db.js#L31) | 已实现 |
| 扩展（2）多人协同编辑、编辑/审校流程功能 | WebSocket 实时通信：低延迟推送标注与页面变更；房间模型（projectRooms）：按项目隔离广播域；Presence 在线态：维护在线成员列表与人数；增量事件同步：annotation_add/update/delete、pages_replace 等事件传播；会话鉴权：sessionId + TTL 校验连接合法性；身份基础：用户登录、角色(role)、权限(permissions)字段为后续流程控制预留扩展点。 | 协同 WS 在 [backend/server.js](backend/server.js#L154)，广播在 [backend/server.js](backend/server.js#L141)，在线成员在 [backend/server.js](backend/server.js#L102)；前端连接在 [edit.html](edit.html#L1324)；登录与会话在 [backend/server.js](backend/server.js#L416)、[backend/server.js](backend/server.js#L47)、[backend/server.js](backend/server.js#L62) | 协同编辑已实现；“审校流程（如提交-审核-通过/驳回状态流）”未看到完整状态机与流程节点实现，当前偏基础协同+权限字段 |
| 扩展（3）引入 AI 识别字/图区域，自动划分或自动填充文字 | 百度 OCR 接入：服务端获取并缓存 access_token，再调用识别接口；上传即识别：前端把当前页图像以 FormData 提交；识别结果结构化：将 OCR 坐标映射为标准 annotation（x/y/width/height + text）；自动填充机制：识别文本写入 simplifiedChar，减少人工录入；区域划分接口：提供 text/image 分区能力，前端可视化描框；可演进架构：当前为规则示例，后续可替换为深度学习分割模型。 | OCR 接口与 token 缓存在 [backend/server.js](backend/server.js#L556)、[backend/server.js](backend/server.js#L520)；前端调用 OCR 在 [js/common.js](js/common.js#L647)，识别后自动生成标注在 [js/common.js](js/common.js#L709)；区域划分接口在 [backend/server.js](backend/server.js#L615)，前端调用在 [js/common.js](js/common.js#L684) | 部分实现：OCR 自动填充已做；区域划分当前是规则化示例分区，不是模型级智能分割 |

补充说明一处实现细节：数据库核心对象与连接池在 [backend/db.js](backend/db.js#L4) 和 [backend/db.js](backend/db.js#L17)，它支撑了项目快照、造字、导出、用户等全部持久化能力。



**1.书籍照片或PDF保存在哪里**

书本图片/PDF在你这个项目里默认不是单独存文件到磁盘，而是转成数据直接放在项目数据里。

结论分三种情况：

1. 前端本地保存  
- 导入普通图片时，用 FileReader.readAsDataURL 读成 base64 DataURL，写入 pages[].imageSrc，见 edit.html 和 edit.html。  
- 导入 PDF 时，不保存原 PDF；会把每一页渲染成 PNG DataURL，写入 pages[].imageSrc，见 edit.html 和 edit.html。  
- 然后这些 pages 会被存到 localStorage（sdoc_pages）和 .sdocproj 文件里，见 common.js 与 common.js。

2. 保存到后端数据库时  
- 你点“保存项目”后，前端把 pages（包含 imageSrc）整体 POST 到后端，见 edit.html。  
- 后端把整份快照 JSON 原样写进 project_snapshots.snapshot_json（LONGTEXT），见 server.js 和 db.js。  
- 所以图片数据是在快照 JSON 里，不是单独图片文件表。

3. OCR/临时上传  
- 后端上传中间件是内存存储，不落盘，见 server.js。  
- 这类上传只用于处理，不会自动保存成磁盘图片。

所以直接回答你这句：  
- 图片：保存在 pages[].imageSrc（base64 DataURL）里，跟着 .sdocproj、localStorage、数据库快照走。  
- PDF：原文件不保存；只保存“转成图片后的每页”。



**2.为何能解析.sdocproj文件**

 .sdocproj 本质上就是 JSON 文本格式，只是用了自定义文件扩展名。

你的项目里，“读取 .sdocproj 并还原”是前端纯本地完成的，主链路很清晰：

1. 触发文件选择  
在 edit.html 定义了隐藏文件输入框，accept 支持 .json/.sdocproj，并在 change 时调用 edit.html。

2. 打开项目入口函数  
edit.html 取到用户选中的文件后，调用 ProjectManager.import（实际定义在 common.js），成功后执行 edit.html，并在有页面时调用 edit.html。

3. 读取并解析 .sdocproj  
核心在 common.js：  
- 用 FileReader.readAsText 读取文件  
- JSON.parse 解析内容  
- 直接把解析结果写入全局状态 AppState：  
  - project ← data.project  
  - pages ← data.pages || []  
  - annotations ← data.annotations || []  
  - customChars ← data.customChars || []  
  - currentPageIndex ← 0  
- 然后调用 AppState.save 把状态持久化到 localStorage

4. 用状态回填界面  
edit.html 会刷新缩略图、页码、标注列表、造字区、状态栏；如果有页面则走 edit.html。  
edit.html 根据 pages[index] 的 width/height 设置三个 canvas，随后把 page.imageSrc 画到主画布，再调用 edit.html 叠加标注。  
edit.html 通过 ann.pageId 与当前页 id 匹配，只绘制当前页的标注；并且会先走 edit.html 给 parentId/childIds 做兜底初始化。

5. 刷新后还能“继续还原”  
公共脚本在 DOMContentLoaded 时会执行 AppState.load 从 localStorage 恢复（project/pages/annotations/customChars），所以导入一次后，刷新页面仍能回到导入后的项目状态。编辑页自己的初始化在 edit.html。

补充两点实现特征：
- 你的 .sdocproj 结构（project/pages/annotations/customChars/exportedAt）与导入代码完全对得上，其中 exportedAt 目前仅用于导出记录，导入时不会参与逻辑。  
- 导入时几乎没有 schema 校验，只有“能否 JSON.parse”这一层；解析失败会报“无法解析项目文件”，读取失败会报“读取文件失败”（见 common.js 和 common.js）。

如果你愿意，我可以下一步帮你做一版“导入流程时序图 + 字段校验建议清单”，方便你后续加兼容和容错。





**3.`edit.html` 的框选是一个**三层 Canvas + 鼠标事件状态机**实现的，不依赖 Fabric。**

## 核心结构

- `mainCanvas`：底图（页面图像）
- `annotationLayer`：已落地标注（持久数据）
- `interactionLayer`：正在拖拽时的临时预览框（虚线框）

对应变量在 `edit.html` 里初始化：`mainCanvas/annotationCanvas/interactionCanvas`。  
并给 `interactionCanvas` 绑定 `mousedown/mousemove/mouseup/mouseleave` 事件。

## 框选流程（box tool）

1. **按下鼠标**：记录起点，进入绘制状态  
2. **移动鼠标**：更新 `currentRect.width/height`，在交互层画虚线预览  
3. **松开鼠标**：把负宽高矩形标准化（左上角 + 正宽高），超过最小阈值后创建标注  
4. **重绘标注层**：把新标注画到 `annotationLayer` 并保存到状态

关键代码链路：

```1907:1932:edit.html
function handleCanvasMouseDown(e) {
    ...
    const x = (e.clientX - rect.left) / zoom;
    const y = (e.clientY - rect.top) / zoom;
    ...
    isDrawing = true;
    startX = x;
    startY = y;
    currentRect = { x, y, width: 0, height: 0 };
}
```

```1935:1963:edit.html
function handleCanvasMouseMove(e) {
    ...
    currentRect.width = x - startX;
    currentRect.height = y - startY;
    drawCurrentRect();
}
```

```1968:1994:edit.html
function handleCanvasMouseUp(e) {
    ...
    } else if (currentRect && Math.abs(currentRect.width) > 5 && Math.abs(currentRect.height) > 5) {
        const normalizedRect = {
            x: currentRect.width > 0 ? currentRect.x : currentRect.x + currentRect.width,
            y: currentRect.height > 0 ? currentRect.y : currentRect.y + currentRect.height,
            width: Math.abs(currentRect.width),
            height: Math.abs(currentRect.height)
        };
        ...
        createAnnotation(normalizedRect);
    }
}
```

## 预览框怎么画

拖拽中的虚线框画在 `interactionLayer`，每次先清空再重画：

```2013:2031:edit.html
function renderInteractionLayer() {
    interactionCtx.clearRect(0, 0, interactionCanvas.width, interactionCanvas.height);
    ...
    interactionCtx.setLineDash([5, 5]);
    interactionCtx.strokeRect(currentRect.x, currentRect.y, currentRect.width, currentRect.height);
    interactionCtx.setLineDash([]);
}
```

## 最终标注怎么画

落地标注统一在 `redrawAnnotations()` 里按 `ann.type` 绘制，`box` 用 `strokeRect`：

```2327:2343:edit.html
function redrawAnnotations() {
    annotationCtx.clearRect(0, 0, annotationCanvas.width, annotationCanvas.height);
    ...
    case 'box':
        annotationCtx.lineWidth = isSelected ? 3 : 2;
        annotationCtx.strokeRect(ann.rect.x, ann.rect.y, ann.rect.width, ann.rect.height);
        break;
}
```

## 坐标为什么 `/ zoom`

鼠标坐标用的是屏幕像素，Canvas 存的是画布坐标；你这里用  
`(client - rect.left/top) / zoom`  
把交互点映射回“原始画布坐标”，所以缩放后框选仍准确。