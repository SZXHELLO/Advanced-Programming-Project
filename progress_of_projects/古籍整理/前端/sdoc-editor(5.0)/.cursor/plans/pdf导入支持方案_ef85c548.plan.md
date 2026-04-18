---
name: PDF导入支持方案
overview: 在现有图片导入流程上增加 PDF 解析与页码范围导入能力，保持与当前 `addPage`/缩略图/标注流程兼容。方案采用 `pdf.js`，并在导入时提供页码范围输入与校验。
todos:
  - id: update-accept
    content: 更新 importImageInput 的 accept 为图片+PDF 类型
    status: completed
  - id: add-pdfjs
    content: 引入并初始化 pdf.js 与 workerSrc
    status: completed
  - id: range-parser
    content: 实现页码范围解析与校验函数
    status: completed
  - id: pdf-import-flow
    content: 在 handleImageImport 中新增 PDF 分流与逐页渲染导入
    status: completed
  - id: ux-and-errors
    content: 补充范围输入、进度提示和错误提示
    status: completed
  - id: regression-check
    content: 验证图片导入回归和 PDF 场景测试
    status: completed
isProject: false
---

# PDF 导入支持方案

## 目标

- 在 `edit.html` 的现有导入入口上，支持 `PDF` 文件导入。
- 用户可输入页码范围（例如 `1,3,5-8`），只导入指定页。
- 导入结果继续复用现有 `addPage()` 与页面渲染流程，避免改动标注主逻辑。

## 关键改动点

- 更新文件选择器类型：将 `accept` 从 `image/*` 扩展为图片 + PDF（位于 [C:/Users/25283/Desktop/古籍整理/前端/sdoc-editor(4.4)_ready_for_ai - 副本/edit.html](C:/Users/25283/Desktop/古籍整理/前端/sdoc-editor(4.4)_ready_for_ai - 副本/edit.html)）。
- 在 [C:/Users/25283/Desktop/古籍整理/前端/sdoc-editor(4.4)_ready_for_ai - 副本/edit.html](C:/Users/25283/Desktop/古籍整理/前端/sdoc-editor(4.4)_ready_for_ai - 副本/edit.html) 的 `handleImageImport(event)` 中按 MIME 分流：
  - 图片文件：保持当前逻辑不变。
  - PDF 文件：走 `pdf.js` 渲染流程（每页转 canvas，再转 dataURL，最后 `addPage`）。
- 新增“页码范围”交互（最小可行版本可用 `prompt`，后续可升级为 modal）。

## 实现设计

- 依赖加载
  - 引入 `pdf.js` 与 `pdf.worker.js`（CDN 或本地静态资源二选一）。
  - 在页面初始化时设置 `pdfjsLib.GlobalWorkerOptions.workerSrc`。
- 页码范围解析
  - 新增 `parsePageRange(input, totalPages)`：支持 `1,3,5-8`、去重、排序、越界校验。
  - 输入为空时采用默认策略：导入全部页。
- PDF 渲染
  - 新增 `importPdfFile(file)`：
    1. `arrayBuffer = await file.arrayBuffer()`
    2. `pdf = await pdfjsLib.getDocument({ data: arrayBuffer }).promise`
    3. 获取页码范围（用户输入 + `parsePageRange`），在输入框下方浅色小子显示导入的PDF的总页码数
    4. 循环 `pdf.getPage(pageNo)` 渲染到离屏 canvas
    5. `canvas.toDataURL('image/png')` 后调用 `addPage({ imageSrc, width, height, ... })`
- 性能与稳定性
  - 页数较多时串行导入并显示进度文本（防止主线程卡顿过久）。
  - 导入失败时按页容错并提示失败页码，不影响其它页继续导入。

## 验收与测试

- 功能测试
  - 单页 PDF：可导入并正常显示。
  - 多页 PDF + 范围 `2-4,7`：只导入对应页。
  - 非法范围 `0,999,a-b`：有明确错误提示且不崩溃。
- 回归测试
  - 原图片导入（jpg/png/webp）行为不变。
  - 导入后缩略图、翻页、标注保存流程保持正常。

## 风险与备选

- 风险：CDN 不可用导致 PDF 无法解析。
- 备选：将 `pdf.js` 打包为本地静态文件并固定版本。

