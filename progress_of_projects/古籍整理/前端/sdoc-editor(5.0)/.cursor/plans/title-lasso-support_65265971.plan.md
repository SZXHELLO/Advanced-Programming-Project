---
name: title-lasso-support
overview: 让 `edit.html` 里“套索”面板的“标题”按钮在框选时把框内完全包含的 `字/词/句子` 标注合成为一个“标题”标注，并确保该标注出现在右侧标注列表及 XML 导出 `<title>` 包裹中。
todos:
  - id: title-lasso-candidates
    content: 在 `edit.html` 的 `getContainedAnnotationsByMode(rect, mode)` 增加 `mode==='标题'` 分支，返回完全包含且 `parentId===null` 的 `attrType` 为 `字/词/句子` 的标注。
    status: completed
  - id: title-lasso-apply
    content: 在 `edit.html` 的 `applyLassoAction(rect)`：放宽 `标题` 模式的最少数量限制为 >=1，并为 `colorMap` 增加 `标题` 的橙色映射 `#D67B3C`，确保新标注 `attrType` 为 `标题` 且具备正确颜色。
    status: completed
  - id: title-lasso-xml-check
    content: 只做手工验证：使用导出预览检查“标题套索”生成的标注能否触发 `js/common.js` 中 `<title name note>` 的开闭与闭合顺序（A..B..C 场景）。
    status: completed
isProject: false
---

## 修改内容概览

在 `edit.html` 的“套索”逻辑里，目前 `defaultLassoAction` 支持 `词/句子/删除`，但缺少对 `标题` 的合并候选收集与颜色映射，因此拉框不会生成“标题”框。本次补齐：

- 让 `getContainedAnnotationsByMode(rect, mode)` 在 `mode==='标题'` 时返回被框选完全包含的 `字/词/句子`（且仍只处理父层级 `parentId===null` 的根标注）。
- 让 `applyLassoAction(rect)` 在 `mode==='标题'` 时不再要求 `targets.length>=2`（允许 >=1），并为新“标题”标注赋予橙色。

## 具体实现点（文件/函数）

1. `edit.html`：`getContainedAnnotationsByMode(rect, mode)`
  - 现状：只处理 `词/句子/删除(含标题删除)`，没有 `mode==='标题'` 分支。
  - 修改：新增分支：
    - `if (mode === '标题') return ann.attrType === '字' || ann.attrType === '词' || ann.attrType === '句子';`
  - 保持现有过滤不变：
    - `ann.type === 'box' && ann.rect`
    - `ann.parentId` 必须为假（只合并根标注，符合“rootOnly”）。
2. `edit.html`：`applyLassoAction(rect)`
  - 现状：对所有非删除模式都有 `targets.length < 2 return;`，以及 `colorMap` 只包含 `词/句子`。
  - 修改：
    - 将长度限制改为：
      - `if (mode !== '删除' && mode !== '标题' && targets.length < 2) return;`
    - 在颜色映射中加入：
      - `colorMap = { '词': '#2B5F8E', '句子': '#5B8C5A', '标题': '#D67B3C' }`
  - 其余逻辑复用现有“词/句子”合并：
    - `simplifiedChar = buildMergedText(ordered)`
    - `attrType: mode`（因此标题会进入 XML 的 `<title>` 包裹逻辑）
    - `childIds` / `parentId` 关系更新
3. XML 导出一致性
  - 本次不需要改 `js/common.js`：因为 XML 已在 `XMLGenerator.generate()` 中根据 `pageAnnotations` 是否存在 `attrType==='标题' && !parentId` 来开闭 `<title>`。
  - 只要“标题”套索能生成 `attrType:'标题'` 的根标注，就会自动触发并与“框选后标题框选”的 XML 行为一致。

## 验证建议（手工）

1. 选择“套索”->“标题”，在某页 A 内拉框，包含多个“字/词/句子”（完全包含），导出 XML：从 A 开始到后续页面出现 `<title name="..." note="...">` 并在末尾补 `</title>`。
2. 在 A 后续某页 B 再用“标题”套索：确认在 B 页面 `<page>` 外层先出现 `</title>` 再出现新的 `<title ...>`。
3. 验证 name/note 为空时：留空输入后导出应为 `name="" note=""`。

