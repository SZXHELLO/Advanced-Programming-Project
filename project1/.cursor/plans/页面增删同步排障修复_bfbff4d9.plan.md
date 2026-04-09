---
name: 页面增删同步排障修复
overview: 定位到页面同步失败的主因是 pages_replace 缺少版本保护与多事件并发乱序覆盖。计划通过引入页面版本号、统一事件语义和接收端幂等校验来稳定页面增删同步。
todos:
  - id: add-pages-replace-version
    content: 为 pages_replace 前后端增加 version 字段并接入发送/透传
    status: completed
  - id: guard-pages-replace-order
    content: 前端增加 collabLastPagesReplaceVersion 防止旧消息覆盖新状态
    status: completed
  - id: harden-page-sync-idempotency
    content: 补强 applyRemotePagesReplace 的空态、索引、重复消息幂等处理
    status: completed
  - id: optional-debug-logs
    content: 增加可控调试日志定位页面同步链路
    status: completed
  - id: regression-verify-page-sync
    content: 执行新增/删除/并发/重连四类回归验证
    status: completed
isProject: false
---

# 页面同步增删排障与修复方案

## 排查结论
- 页面增删事件链路本身已存在：前端会发 `page_add/page_delete/pages_replace`，后端会透传，接收端也有处理逻辑（`handleCollabMessage` 与 `applyRemotePage*`）。
- 主要缺陷在 [C:\Users\25283\Desktop\古籍整理\前端\sdoc-editor(5.0) - 副本\edit.html](C:\Users\25283\Desktop\古籍整理\前端\sdoc-editor(5.0) - 副本\edit.html)：
  - `applyRemotePagesReplace(rawPages)` 没有版本/时间戳保护；
  - 当前每次本地增删会连续发送 `page_add/page_delete` + `pages_replace`，网络乱序时旧 `pages_replace` 可能覆盖新状态。
- 对比同文件里的 `annotations_replace`，它已有 `version` 防回退，页面同步缺少同级保护。

## 修复目标
- 页面新增/删除在多人场景下最终一致，不被乱序包回滚。
- 页面列表同步与标注过滤保持幂等，重复事件不影响结果。

## 实施步骤
1. **给 pages_replace 增加版本号并全链路透传**
- 前端 `collabEmitPagesReplace` 增加 `version: Date.now()`。
- 后端 `server.js` 的 `pages_replace` 广播体增加 `version` 字段。
- 前端 `applyRemotePagesReplace` 新增 `collabLastPagesReplaceVersion`，仅应用 `version >= lastVersion` 的消息。

2. **统一页面变更主事件，降低乱序覆盖概率**
- 方案A（推荐）：保留 `pages_replace` 作为权威同步；`page_add/page_delete` 仅做可选即时优化，最终仍由 `pages_replace(version)` 收敛。
- 方案B：直接停止发送 `page_add/page_delete`，增删后只发一次 `pages_replace(version)`。
- 本次建议默认用方案A（兼容你现有代码结构，改动小）。

3. **接收端幂等与边界补强**
- `applyRemotePagesReplace` 在替换前后统一校验：
  - 空数组时正确清空页面/标注/选中态；
  - 非空时修正 `currentPageIndex` 并仅加载有效页；
  - 避免重复触发导致 UI 闪动（必要时增加“内容相同则跳过”快速比较）。

4. **排障可观测性（临时）**
- 在前端页面事件处理处增加可控调试日志（事件类型、version、pages长度、lastVersion）。
- 在后端 `pages_replace` 广播前打印一次精简日志（projectId/fromSessionId/version/pagesCount）。
- 验证通过后可保留为 debug 开关或移除。

5. **回归验证清单**
- A 连续新增 3 页，B 全量同步一致。
- A 删除中间页，B 页码与当前页回退逻辑一致。
- A/B 同时进行增删操作，最终两端页面顺序一致且不回退。
- 异常重连后再次收到旧包时，不覆盖最新页面状态。

## 重点修改文件
- [C:\Users\25283\Desktop\古籍整理\前端\sdoc-editor(5.0) - 副本\edit.html](C:\Users\25283\Desktop\古籍整理\前端\sdoc-editor(5.0) - 副本\edit.html)
- [C:\Users\25283\Desktop\古籍整理\前端\sdoc-editor(5.0) - 副本\backend\server.js](C:\Users\25283\Desktop\古籍整理\前端\sdoc-editor(5.0) - 副本\backend\server.js)