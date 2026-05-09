# 前端开发任务指令集 (For Gemini - v1.2.0 Sprint)

## 1. 任务背景
你需要构建 EasyPlan v1.2.0 的前端界面。核心目标是完成从“AI 玩具”到“原生生产力看板”的跨越。你将构建一个沉浸式的原生任务管理面板，并实现复杂的任务树交互。

## 2. 核心技术栈要求
- **框架：** React (Vite) + TypeScript。
- **样式：** Tailwind CSS (延续 Parchment Zen Mode 护眼美学)。
- **状态管理：** Zustand (需新增 TaskStore 用于管理本地持久化任务列表)。
- **动效：** Framer Motion (必须保持植物生长般的丝滑感)。

## 3. 设计原则 (GenUI & Deep Immersion)
- **沉浸式生成 (Burn After Reading)：** 当任务树生成完毕进入 `PENDING` 或 `SUCCESS` 态时，必须自动折叠并平滑淡出 `ReasoningStream` (AI 思考日志)，只留下纯净的计划。
- **原生看板视图：** 系统状态到达 `SUCCESS` 后，UI 应平滑转场至包含“我的一天 / My Day”和“计划中”侧边栏的专属任务看板。
- **直觉交互：** 减少弹窗，尽量在原位进行组件的生长与收缩。

## 4. 关键交互流程 (v1.2.0 增量)
1. **行内编辑 (Inline Edit):** 任务树在 `PENDING` 和 `SUCCESS` 状态下，必须支持双击节点直接修改任务标题与预估时间。
2. **状态流转补全:** 监听真实的 `done` 和业务 `error` 事件，决不能做假交互。
3. **任务勾选闭环:** 在看板视图中，用户勾选主任务或子任务时，应有划线和淡出动效。

## 5. 交付要求
- 极致响应式的布局（移动端下看板应支持抽屉式折叠）。
- 优雅的动画过渡（使用 CSS Transitions/Animations）。
- 严格遵循后端提供的 API 契约（参见 API Blueprint 文档）。
