# EasyPlan Frontend Design Document

## 1. 概述
本文档旨在定义 EasyPlan 前端系统的架构设计、状态管理及交互逻辑，确保实现极致简洁、意图驱动的用户体验。

## 2. 组件树结构 (Component Tree)
采用组合优于继承的原则，确保组件职责单一。

```text
App
└── Layout (全局布局)
    ├── Header (Logo & User Profile)
    └── Main (核心交互区域)
        ├── IntentInput (Spotlight 风格输入框)
        ├── StarterTemplates (启动模板 - 仅 INITIAL 态)
        │   └── TemplateCard (场景化建议，如“写论文”、“健身计划”)
        └── StageContainer (基于状态的舞台容器)
            ├── ReasoningStream (思考轨迹展示 - 仅 THINKING 态)
            │   └── ReasoningStep (单条思考日志)
            ├── TaskTreeVisualizer (任务树可视化 - PENDING/SYNCING/SUCCESS/PARTIAL_ERROR 态)
            │   ├── TaskBranch (递归渲染的任务分支)
            │   └── TaskItem (包含成功/失败/重试状态的最小单元)
            ├── RefinementInput (二次优化输入框 - 仅 PENDING 态)
            ├── IntegrationSettings (OAuth 授权 & 集成管理)
            └── ActionBar (操作栏)
                ├── ConfirmButton (确认注入 Todoist)
                └── EditButtonGroup (重试/修改/取消)
```

### 核心组件说明：
- **StarterTemplates**: 解决“空白画布”问题。提供如“拆解一个复杂的项目”、“规划这周末的旅行”等一键填入模板。
- **RefinementInput**: 允许用户在任务树生成后通过自然语言进行微调（如：“太长了，帮我缩减一半”、“增加一些关于预算的步骤”）。
- **TaskTreeVisualizer**: 
    - **渐进式展示**: 默认仅展开核心路径。
    - **状态感知**: 在 `PARTIAL_ERROR` 态下，清晰标记哪些任务已成功同步到 Todoist，哪些失败。
- **TaskItem**: 针对失败节点提供独立的“一键重试”小按钮。

## 3. 状态管理方案 (State Management)

### 3.1 核心状态 Store
```typescript
interface AppStore {
  intent: string;
  appState: 'INITIAL' | 'THINKING' | 'PENDING' | 'SYNCING' | 'SUCCESS' | 'PARTIAL_ERROR' | 'ERROR';
  threadId: string | null;
  syncRequestId: string | null;
  reasoningLogs: string[];
  taskTree: TaskNode | null;
  isIntegrated: boolean;
  // Actions
  setIntent: (val: string) => void;
  refineIntent: (refinement: string) => void; // 提交二次微调
  retryTask: (taskId: string) => Promise<void>; // 针对单个节点的重试
  alignState: () => Promise<void>;
  reset: () => void;
}
```

### 3.2 SSE 与 交互逻辑
- **二次优化流**: 用户在 `RefinementInput` 提交后，状态回退至 `THINKING`，SSE 重新开始推送推理日志及更新后的任务树。
- **部分同步失败处理**: 监听 `event: sync_status`。若部分节点失败，状态切至 `PARTIAL_ERROR`，UI 保持当前树状视图，并高亮错误节点。

## 4. UI 状态机 (UI State Machine)

| 状态 (State) | 视觉特征 | 核心交互 | 触发条件 |
| :--- | :--- | :--- | :--- |
| **INITIAL** | 居中输入框 + **启动模板卡片** | 键入或点击模板 | 初始态 |
| **THINKING** | 上浮输入框，流式日志 | 允许取消 | 推理或**二次微调中** |
| **PENDING** | 任务树 + **二次微调输入框** | **行动指引** + 文本优化回复 | 收到 `plan_ready` |
| **SYNCING** | 节点静默 Loading | 禁止交互 | 点击确认 |
| **PARTIAL_ERROR** | 混合状态标识（红/绿） | **针对性重试**失败节点 | 部分任务同步失败 |
| **SUCCESS** | 成功动画，Todoist 链接 | 点击跳转外部查看 | 全部同步成功 |

## 5. 补充规范 (PM 审计 & 体验增强)

### 5.1 体验与性能
- **渐进式披露**: 超过 10 个任务时自动收起非叶子节点，保持视觉焦点。
- **时区协议**: Header 携带 `X-User-Timezone`，强制使用 ISO 8601 带时区格式。

### 5.2 稳健性 (Robustness)
- **局部重试**: `PARTIAL_ERROR` 态下，点击重试仅针对失败节点发送请求，复用同一个 `syncRequestId`（由后端处理子任务幂等）。
- **状态对齐**: 重连时通过快照请求恢复树的最新修改状态，确保“微调”后的结果不丢失。
