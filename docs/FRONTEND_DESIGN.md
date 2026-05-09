# EasyPlan Frontend Design Document

## 1. 概述
本文档定义了 EasyPlan 前端系统的架构设计。我们致力于在“极致极简”与“大众易用性”之间取得平衡，通过主次分明的界面、渐进式的引导和具有仪式感的动效，打造一个既专业又亲和的意图驱动应用。

## 2. 技术规格 (Technical Specifications)

### 2.1 核心准则
- **OpenAPI 严守**: 严格遵循后端接口契约。
- **TypeScript 核心**: 强制类型安全，特别是针对 SSE 流式数据和 Store 状态。
- **Monorepo 结构**: 独立的前端依赖管理 (`frontend/` 目录)。
- **时区与安全**: 全局 ISO 8601 时区同步，环境变量管理敏感配置。

### 2.2 状态管理 (Zustand & Snapshot)
- **状态对齐**: SSE 重连时通过快照请求恢复 UI。
- **动态输入控制**: Zustand 驱动 `DynamicInput` 的 Placeholder、图标及提交逻辑的动态切换。

## 3. 设计哲学 (Design Philosophy - Balanced Minimalism)

### 3.1 主次分明 (Centrality)
- **核心组件**: 全应用以 `DynamicInput` 为灵魂。它处于视觉中心，既是意图的入口，也是微调反馈的接收站。

### 3.2 渐进式引导 (Progressive Guidance)
- **幽灵设计 (Ghost Design)**: `Confirm` 和 `Cancel` 按钮采用半透明或仅边框的“幽灵按钮”风格，减少视觉侵入。
- **快捷键标注**: 在按钮旁以微细文字标注快捷键（如 `⌘↵`），在照顾普通用户的同时引导其向高效用户进阶。
- **平滑显隐**: 按钮仅在必要阶段（如 `PENDING` 态）随任务树同步出现，非活跃状态下完全隐匿。

### 3.3 仪式感动效 (Fluid Motion)
- **生长式动画**: 任务树和按钮的出现模拟“植物生长”过程，利用 `stagger`（交错）效果和 `cubic-bezier` 曲线，实现丝滑、自然的伸展。
- **状态过渡**: 避免生硬的弹出（Pop），优先使用 `opacity`、`blur` 和 `transform: scale/translate` 的组合动效。

## 4. 组件树结构 (Component Tree)

```text
App
└── Layout (全局布局)
    ├── Header (Logo & Integration Modal Toggle)
    └── Main (核心交互区域：The Void)
        ├── DynamicInput (全应用唯一输入框)
        │   └── SuggestionOverlay (微细提示语)
        └── StageContainer (基于状态的舞台容器)
            ├── ReasoningStream (流式思考轨迹)
            ├── TaskTreeVisualizer (生长式任务树)
            └── ActionLayer (幽灵按钮层 - 带快捷键提示)
```

## 5. UI 状态机 (Balanced State Machine)

| 状态 (State) | 输入框角色 | 动作/引导 (Action) | 动效特征 |
| :--- | :--- | :--- | :--- |
| **INITIAL** | 捕获意图 | 极简预测提示 | 聚焦动效 |
| **THINKING** | 状态展示 | 幽灵式 [取消] (Esc) | 呼吸感加载 |
| **PENDING** | 意图微调 | 幽灵式 [确认] (⌘↵) | 树状节点交错生长 |
| **SYNCING** | 进度反馈 | 无操作，仅保留进度感知 | 节点内部平滑填充 |
| **SUCCESS** | 开启新循环 | 成功徽章 + 外部链接 | 整体淡出/新意图聚焦 |

## 6. 未来展望 (v1.2.0 Roadmap)

随着战略向原生闭环转移，前端将在下一版本重点发力以下架构扩展：

### 6.1 原生任务看板 (Native Task Board)
- **UI 增补**: 当状态机流转至 `SUCCESS` 时，不再只是简单的成功提示，而是平滑转场至功能完整的任务面板。
- **视图支持**: 引入“我的一天”、“计划中”等核心视图。

### 6.2 深度沉浸体验 (Deep Immersion)
- **阅后即焚**: 完善 `ReasoningStream` 的动画逻辑，在树状图渲染完毕后自动将其高度折叠为 0 并淡出，确保界面的极简呼吸感不受破坏。

### 6.3 行内编辑能力 (Inline Editing)
- **交互逻辑**: 在现有的 `TaskTreeVisualizer` 组件基础上，支持双击节点直接修改任务标题与预估时间，补齐纯自然语言 Refine 的颗粒度控制短板。
