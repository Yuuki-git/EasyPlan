# 前端开发任务指令集 (For Gemini)

## 1. 任务背景
你需要构建 EasyPlan 的前端界面。这是一个极致简洁、意图驱动的 SaaS 应用。你的目标是消除用户的认知负担，提供流畅的“人机协作”体验。

## 2. 核心技术栈要求
- **框架：** React (Next.js 或 Vite) + TypeScript。
- **样式：** Vanilla CSS (追求极致加载速度与自定义美感)。
- **状态管理：** 轻量级方案 (Zustand 或 React Context)。

## 3. 设计原则 (GenUI)
- **Spotlight 风格输入：** 首页应以一个巨大的、居中的自然语言输入框为核心。
- **推理流展示：** 当后端正在拆解任务时，不要只显示 Spinner，要展示“AI 思考的轨迹”（例如：流式输出“正在识别核心动作...”、“正在寻找两分钟切入点...”）。
- **任务树渲染：** 采用非线性的树状结构或分级列表展示拆解结果，强调任务间的依赖关系。
- **无压力交互：** 减少弹窗，尽量在原位进行组件的生长与收缩。

## 4. 关键交互流程
1. **Submit Intent:** 用户回车发送数据。
2. **Listen for Events:** 建立 SSE 或 WebSocket 连接，监听后端推送到 `agent_status`。
3. **Render & Confirm:** 
   - 渲染后端生成的 `TaskTree`。
   - 提供一个具有“仪式感”的确认按钮 (例如快捷键 `Cmd/Ctrl + Enter`)。
4. **Final Save:** 发送 POST 请求确认任务注入，后端图状态结束，前端展示成功态。

## 5. 交付要求
- 极致响应式的布局（移动端适配同样重要）。
- 优雅的动画过渡（使用 CSS Transitions/Animations）。
- 严格遵循后端提供的 API 契约（参见 API Blueprint 文档）。
