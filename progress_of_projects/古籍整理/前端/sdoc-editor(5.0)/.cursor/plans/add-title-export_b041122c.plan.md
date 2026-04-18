---
name: add-title-export
overview: 在 `edit.html` 增加右侧“属性类型”中的橙色“标题”按钮，并让该类型标注卡片的 `annotation-char` 显示“题”；同时在 `js/common.js` 的 XML 导出中，根据每页是否存在“标题”标注，为该页及后续 `<page>` 自动生成以 `<title name note>` 为边界的包裹与闭合逻辑。
todos:
  - id: ui-add-title-btn
    content: 在 `edit.html` 的 `boxAttrTypeSelector` 增加“标题”按钮（橙色、data-type="标题"）。
    status: completed
  - id: ui-wire-title
    content: 在 `edit.html` 中把“标题”加入 `colorMap`（syncAttrTypeSelector/selectAttrType），扩展删除模式匹配（getContainedAnnotationsByMode 的 mode==='删除' 包含 标题），并在 `updateAnnotationList()` 里让 `annotation-char` 显示“题”。
    status: completed
  - id: xml-title-wrapping
    content: 在 `js/common.js` 的 `XMLGenerator.generate()` 中：按页检测 `attrType==='标题'`，在 `<page>` 输出前后插入 `<title name note>` 开闭标签，实现 A..后续包裹、遇到下一“标题页”先 `</title>` 再 `<title>`、末尾补 `</title>`。同页多个标题时取第一条忽略其余，并保证 name/note 为空时导出空字符串。
    status: completed
  - id: manual-verify
    content: 用导出预览手工验证 3 个场景（仅A、A+B、C+A）以及 name/note 为空时的导出结果。
    status: completed
isProject: false
---

## 目标

1. `edit.html`：右侧属性类型选择器新增 `标题`（橙色），框选后在右侧标注栏新增卡片；卡片中 `annotation-char` 显示 `题`。
2. XML 导出（`js/common.js`）：当第 N 页存在“标题”标注时，在 `<content>` 内把该页及其后续的 `<page>` 按规则用一组 `<title name="..." note="...">...</title>` 包裹；若后续又出现新的“标题”页，则在新页 `<page>` 外层先插入 `</title>` 再插入新的 `<title>`，以保证闭合/再开合。
3. `<title>` 的 `name` 和 `note` 来源于 `edit.html` 中每条“标题”标注对应的：简体字输入框(`simplifiedChar`)、注释(`note`)；若留空则导出空字符串 `""`。

## 关键实现细节（与现有代码对齐）

- `edit.html` 已有“字/词/句子”三类：按钮/颜色选择/标注卡片展示都依赖 `currentAttrType/currentColor/defaultNewBoxAttr` 与 `updateAnnotationList()` 的显示映射。
- 当前 `js/common.js` 的 `XMLGenerator.generate()` 只导出 `字/词/句子` 的 `<textfield>`，并且直接逐页输出 `<page>`，没有 `<title>` 包裹逻辑；因此新增“标题”需要在 `<page>` 输出前后插入 `<title>` 开闭标签。

## 实施步骤

1. `[edit.html](edit.html)`：右侧属性类型选择器新增按钮,四个按钮采用“田”字分布（即分为上下两行，每行两个，四个按钮长宽一致且对其）
  - 在 `boxAttrTypeSelector`（当前仅包含 `字/词/句子`）中添加一项：
    - `data-type="标题"`
    - 橙色指示条：`style="background: var(--mark-orange);"`
    - 文本：`标题`
2. `[edit.html](edit.html)`：把“标题”纳入现有类型逻辑
  - 在 `syncAttrTypeSelector()` 与 `selectAttrType()` 的 `colorMap` 中加入：`'标题': '#D67B3C'`。
  - 在 `getContainedAnnotationsByMode(rect, mode)` 中扩展 `mode === '删除'` 的匹配范围：把 `ann.attrType === '标题'` 也纳入，这样框选删除不会漏掉标题标注。
  - 在 `updateAnnotationList()` 内部根据 `ann.attrType` 选择 `displayChar`：
    - 若 `ann.attrType === '标题'`，则 `displayChar = '题'`（与 `句子/词` 的“固定显示字符”方式一致）。
3. `[js/common.js](js/common.js)`：在 `XMLGenerator.generate()` 中实现 `<title>` 包裹/闭合
  - 在生成 `<content>` 后、每次输出一个 `<page>` 之前：
    - 计算该页是否存在“标题”标注：`pageAnnotations` 中筛选 `attrType === '标题'` 且 `!parentId`（无父子关系时 parentId 为 null）。
    - 取该页“标题”标注的 `name/note`：
      - `name = (ann.simplifiedChar || '').trim()`
      - `note = (ann.note || '').trim()`
      - 若空则导出空字符串（`escapeXml('')` 结果仍为 `''`）。
    - 包裹规则（按页面从前到后遍历）：
      - 遇到“标题页”时：
        - 若当前已有打开的 `<title>`（前一个“标题页”尚未闭合），先输出 `</title>`。
        - 再输出新的 `<title name="..." note="...">`。
      - 输出该页 `<page>` 内容后继续；遇到下一次“标题页”再闭合并再开合。
      - 遍历结束后：若仍有未闭合的 `<title>`，补一个 `</title>`。
  - `onlyone` 约定：若同一页存在多个“标题”标注，导出时取其中第一条（按 `pageAnnotations` 顺序/annotations 数组顺序），其余忽略。
4. 验证（导出预览/手工检查）
  - 场景 1：只有 A 页有“标题” -> `<title>` 从 A 页外层开始一直包到最后一个 `<page>`，末尾自动补 `</title>`。
  - 场景 2：A 页有“标题”，B 页也有“标题” -> 在 B 页 `<page>` 外层出现 `</title>`（闭合 A 的 title）紧接着 `<title>`（开启 B 的 title），且 A 与 B title 之间不嵌套。
  - 场景 3：C 页先有“标题”，A 页后又有“标题” -> 在 A 页 `<title>` 之前会先插入 `</title>`（闭合 C 的 title）。
  - 同时检查 `<title name note>`：当输入框为空时属性值导出为 `name="" note=""`。

## 涉及文件

- `[edit.html](edit.html)`：新增按钮、颜色映射、删除模式纳入标题、卡片显示字符。
- `[js/common.js](js/common.js)`：修改 `XMLGenerator.generate()` 的 `<content>/<page>` 输出结构，加入 `<title>` 包裹与闭合逻辑。

