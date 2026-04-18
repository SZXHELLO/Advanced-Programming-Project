---
name: upload页面布局与预览修复
overview: 调整 upload 页面三大区域的布局比例与高度分配，并修复已上传缩略图在多页场景下的叠压显示问题。方案以最小侵入修改 `upload.html` 的结构样式为主，必要时对全局高度口径做一致化。
todos:
  - id: align-top-modules
    content: 收紧 upload 区尺寸与间距，使其与上传说明视觉平齐且同宽
    status: completed
  - id: expand-uploaded-list
    content: 调整 flex 高度分配与页面高度口径，确保已上传列表上移并填充剩余空间
    status: completed
  - id: fix-thumbnail-overlap
    content: 稳定 uploaded-grid 与 uploaded-item 尺寸策略，修复缩略图重叠并保证完整展示
    status: completed
  - id: regression-check
    content: 按空列表/多图/PDF多页/窗口缩放场景验证并记录结果
    status: completed
isProject: false
---

# upload.html 布局与缩略图修复计划

## 目标

- 让“点击或拖拽上传”模块上移并与“上传说明”在视觉上平齐，且两者宽度一致。
- 让“已上传列表”自动填充拖拽区收缩后释放的空间。
- 让缩略图按网格正常换行，避免重叠，保证每张图完整显示后下一张出现在下方。

## 变更范围

- 主文件：[C:\Users\25283\Desktop\古籍整理\前端\sdoc-editor(4.4)_ready_for_ai - 副本\upload.html](C:\Users\25283\Desktop\古籍整理\前端\sdoc-editor(4.4)_ready_for_ai - 副本\upload.html)
- 可能联动校准（仅当页面高度仍异常时）：[C:\Users\25283\Desktop\古籍整理\前端\sdoc-editor(4.4)_ready_for_ai - 副本\css\common.css](C:\Users\25283\Desktop\古籍整理\前端\sdoc-editor(4.4)_ready_for_ai - 副本\css\common.css)

## 实施步骤

1. 调整上传区与说明区的垂直节奏和尺寸比例

- 在 `upload.html` 的样式中收紧 `.tips-section` 与 `.upload-area` 的上下间距（`margin/padding/gap`）。
- 为 `.upload-area` 设置更紧凑但稳定的高度策略（优先使用 `clamp()`），使其高度接近黄金分割思路（高度约为同宽度区域的 0.62 倍视觉级别，而非超高占位）。
- 统一 `.tips-section` 与 `.upload-area` 的外框宽度语义（同容器宽度、同级盒模型），避免“看起来不齐”。

1. 让“已上传列表”上移并吃满剩余空间

- 保持 `.upload-page` 为纵向 flex 容器。
- 将 `.uploaded-list` 继续设为 `flex: 1; min-height: 0;`，并减少上传区占位后让列表自然上移。
- 校准 `upload.html` 中 `.upload-page` 高度计算与 `common.css` 的页面可视高度口径（顶部导航+底部状态栏）一致，消除空白区误差。

1. 修复缩略图重叠

- 在 `.uploaded-grid` 明确网格行高策略（例如 `grid-auto-rows` 或等价稳定方案），避免仅靠 `aspect-ratio` 导致行高不稳。
- 在 `.uploaded-item` 增加高度兜底（`min-height` 或固定响应式高度），并保持 `overflow: hidden`。
- 调整 `.uploaded-item img` 为“完整展示优先”策略（`object-fit: contain` + 居中背景）以满足“每张缩略图完整显示后再显示下一张”的效果诉求。
- 保留页码与删除按钮绝对定位，但校正其层级和可点击区域，防止视觉串层。

1. 回归验证

- 空列表、少量图片、多页 PDF（50+）分别验证。
- 验证不同窗口宽度下：上传区与说明区平齐、列表自适应填满、缩略图无重叠。
- 验证上传后跳转、删除单页/清空全部等交互无回归。

## 验收标准

- “上传说明”与“点击或拖拽上传”顶部对齐，宽度一致，上传区高度显著收紧。
- “已上传列表”在同一视口下明显上移并填充可用空间。
- 缩略图始终按网格顺序排布，无重叠；每张图完整可见，后续图片在下方出现。
- 交互功能（上传、删除、清空、跳转）保持正常。

