---
name: 套索隐藏与XML排版
overview: 在不改动页面交互框架的前提下，新增“套索整合关系”数据并改造渲染/命中与导出逻辑，实现被整合框隐藏（可卡片临时显示）及XML按单字/单词/句子平铺分序输出并带句子子节点。
todos:
  - id: merge-visibility-model
    content: 在标注模型中加入 parentId/childIds 并实现隐藏判定
    status: completed
  - id: lasso-relations-update
    content: 套索词/句子创建与删除时维护父子关系与回显逻辑
    status: completed
  - id: draw-hit-filtering
    content: 在绘制、点击命中、套索候选中过滤被整合隐藏标注
    status: completed
  - id: xml-panel-ordering
    content: 重构XML导出为单字/单词/句子平铺分序输出
    status: completed
  - id: xml-sentence-children
    content: 为句子追加子 textfield 并排除前序重复项
    status: completed
  - id: regression-validation
    content: 验证交互隐藏规则与XML样例输出一致
    status: completed
isProject: false
---

# 套索隐藏与XML排版改造计划

## 改造目标

- 套索生成“词/句子”后，被整合进新对象的红/蓝框在画布默认隐藏，且不参与后续套索命中。
- 被隐藏对象在右侧“标注”列表仍保留；点击其卡片时在画布临时显示，取消选中后恢复隐藏。
- 导出 XML 时每页 `<panel>` 仍使用平铺结构，但输出顺序为：未并入句子的单字 → 未并入句子的单词 → 句子。
- 句子节点下附带其直接子成员（字/词）`<textfield>` 子节点；被并入句子的字/词不再在前两类重复输出。

## 关键实现思路

- 在标注对象上引入可持久字段（写入 `AppState.annotations`）：
  - `parentId`: 当前标注被哪个词/句子整合（无则 `null`）
  - `childIds`: 当前词/句子包含的直接子标注 id 列表（无则空数组）
- 套索整合时建立父子关系：
  - 词模式：新词 `childIds = 被圈中的字id`，对应字 `parentId = 新词id`
  - 句子模式：新句子 `childIds = 被圈中的字/词id`，对应字/词 `parentId = 新句子id`
- 删除时级联回显：
  - 删除词/句子后，将其 `childIds` 对应对象的 `parentId` 清空，恢复可见与可再次套索。

## 文件与改动点

- [c:\Users\25283\Desktop\古籍整理\前端\sdoc-editor(4.3)_ready_for_ai - 副本\edit.html](c:\Users\25283\Desktop\古籍整理\前端\sdoc-editor(4.3)_ready_for_ai - 副本\edit.html)
  - `applyLassoAction()`：
    - 在生成词/句子时写入 `parentId/childIds` 关系。
    - 删除模式中加入“删父释放子”的关系清理逻辑。
  - 新增可见性判断函数（如 `isAnnotationHiddenByMerge(ann)`）：
    - 规则：`ann.parentId` 存在且不是当前 `selectedAnnotation.id` 时隐藏。
  - `redrawAnnotations()`：
    - 绘制前跳过“被整合且非当前选中”的标注，实现默认隐藏。
  - `findAnnotationAt()` 与 `getContainedAnnotationsByMode()`：
    - 命中/套索筛选时跳过隐藏对象，确保“无法被下一次套索选中”。
  - `selectAnnotation()/deselectAnnotation()` 保持现有链路：
    - 依赖 `redrawAnnotations()` 自动实现“卡片点中临时显现，取消后恢复隐藏”。
- [c:\Users\25283\Desktop\古籍整理\前端\sdoc-editor(4.3)_ready_for_ai - 副本\js\common.js](c:\Users\25283\Desktop\古籍整理\前端\sdoc-editor(4.3)_ready_for_ai - 副本\js\common.js)
  - `XMLGenerator.generate()` 重构每页导出分组与顺序（仍平铺在 `<panel>`）：
    - `characters`: `attrType='字'` 且 `parentId` 为空
    - `words`: `attrType='词'` 且 `parentId` 为空
    - `sentences`: `attrType='句子'`
  - 句子输出时追加其子节点：
    - `<textfield ... attrType="句子"> ...子 textfield... </textfield>`
    - 子节点来自句子 `childIds` 对应对象（仅直接子，不递归展开）。
  - 前两类不输出已被句子整合的字/词（通过 `parentId` 判定）。

## 兼容与边界处理

- 旧数据兼容：历史标注若无 `parentId/childIds`，按 `parentId=null`、`childIds=[]` 处理。
- 若句子的某个 `childId` 已被删除，导出时自动忽略失效引用。
- 若用户删除子节点本身：同步从父节点 `childIds` 移除，避免悬挂引用。

## 验证清单

- 交互验证：
  - 套索“词”整合后：字框隐藏、列表卡片仍在、卡片选中时临时显示。
  - 套索“句子”整合后：字/词框隐藏，且不可再被套索纳入。
  - 删除句子/词后：其子项恢复显示并可再次被套索。
- XML验证（单页）：
  - `<panel>` 内顺序为“单字→单词→句子”。
  - 被句子整合的字/词不在前两类重复出现。
  - 句子节点包含对应字/词子节点，结构与你示例一致。

