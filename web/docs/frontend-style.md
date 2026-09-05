# 前端唯一风格规则

本文件是 RH Workflow Desk 前端风格的唯一规则来源。规则适用于 `web/static/` 下的任务提交、提示词工坊、成片、内容对比和工作流库页面。

发生冲突时按以下顺序处理：本文件与 `app.css` 的公共令牌/组件规则优先；页面 CSS 只负责页面布局和页面专属内容；旧页面中残留的局部写法不构成新规则。新增或修改 UI 必须同步通过 `test_frontend_style.py` 的检查。

## 适用范围

主要页面和样式文件如下：

- `static/index.html`、`static/app.js`、`static/app.css`：任务提交页。
- `static/prompt.html`、`static/prompt.js`、`static/prompt.css`：提示词工坊。
- `static/outputs.html`、`static/outputs.js`、`static/outputs.css`：成片/产物浏览。
- `static/compare.html`、`static/compare.js`、`static/compare.css`：成片内容对比。
- `static/workflows.html`、`static/workflows.js`、`static/workflows.css`：本地工作流库。
- `static/motion.js`：页面切换、弹窗和媒体预览的共享动效运行时。

新增页面必须沿用上述结构：页面自己的布局放在页面 CSS 中，跨页面的颜色、控件、导航、弹窗和可访问性规则放在 `app.css` 中。

## 统一令牌与组件规则

- `app.css` 的 `:root` 是公共令牌的唯一定义位置；浅色主题只在同一文件的 `html[data-theme="light"]` 中覆盖令牌。
- 页面 CSS 不得新增公共颜色的固定十六进制值、公共弹窗阴影或公共控件几何。页面专属色必须先定义成页面语义变量，并在日夜主题下各自映射。
- 公共按钮只使用 `.primary-button`、`.secondary-button`、`.button-compact`；不要用祖先选择器重新覆盖公共按钮的高度、字号、内边距或圆角。图标、胶囊标签、拖放区等专用小控件可以保留自己的布局尺寸。
- 每个 `role="dialog"` 都必须是 `.modal-backdrop` 的直接子元素，并带有 `.dialog-panel`。弹窗公共宽度、内边距、边框、背景、阴影和进出场动效由 `app.css` 提供；页面 CSS 只能声明弹窗的内容尺寸或内部布局。
- 所有动态生成的固定状态必须使用 CSS 类；禁止在 HTML/JavaScript 中写固定颜色、固定高度等 inline style。列表入场的动态 `animation-delay` 是唯一例外。

## 视觉令牌

公共令牌位于 `app.css` 的 `:root`：

| 用途 | 令牌 |
| --- | --- |
| 主文字 | `--ink` |
| 次级文字 | `--muted` |
| 辅助文字 | `--subtle` |
| 页面背景 | `--canvas`、`--canvas-deep` |
| 面板背景 | `--panel`、`--panel-raised` |
| 边框 | `--line`、`--line-soft` |
| 强调色 | `--accent`、`--accent-deep` |
| 暖色提示 | `--warm` |
| 错误状态 | `--danger` |
| 控件 | `--control-font-size`、`--control-line-height`、`--control-height` |
| 紧凑控件 | `--control-compact-font-size`、`--control-compact-height` |
| 面板/控件表面 | `--surface-input`、`--surface-control`、`--surface-control-muted`、`--surface-control-hover`、`--surface-modal`、`--surface-media` |
| 控件边框 | `--border-control`、`--border-control-hover`、`--border-drop`、`--border-empty`、`--border-dialog` |
| 特殊语义色 | `--reference-accent`、`--type-accent`、`--disabled-ink`、`--grip-ink` |

浅色模式通过 `html[data-theme="light"]` 覆盖同一组语义令牌。组件不要直接写一套独立的日间颜色，否则会破坏主题切换。

## 字体与控件

- 页面使用系统字体栈，中文优先使用苹方/微软雅黑等系统字体。
- 文件路径、节点 ID、workflowId、JSON 片段使用 `SFMono-Regular` 等等宽字体。
- 所有普通 `input` 和 `select` 统一使用 `--control-font-size: 11px`、`--control-line-height: 1.25` 和 `--control-height: 40px`；不要在页面选择器中重新定义普通控件字号或高度。
- `textarea` 保留较大的内容区和 `1.6` 行高，适合提示词和错误详情；字号沿用控件基线，JSON/日志专用区域可按内容需要使用等宽字号。
- 紧凑表单或工具栏控件必须显式使用 `--control-compact-height: 34px` 与 `--control-compact-font-size: 10px`；按钮使用 `.button-compact`，紧凑输入框需在页面语义类中引用这两个令牌。
- 路径输入可保留等宽字体，但不能改变字号基线；搜索框、排序框和普通表单默认使用 40px 控件高度。
- 原生 `select` 展开的选项列表可能由 macOS 绘制，页面内选中值仍应遵循公共控件字号。
- 会把文件、提示词或参考资源写入任务草稿/任务节点的按钮统一命名为“导入媒体”；把 API JSON 保存到工作流库的入口才使用“导入工作流”。

## 间距、圆角与容器

- 间距优先使用现有的 8px 左右节奏：常见值为 `8 / 10 / 12 / 14 / 16 / 20 / 24 / 28px`。
- 大面板使用 `--radius: 20px`，小控件使用 `--small-radius: 12px`；状态标签和筛选标签使用胶囊形圆角。
- 面板内部先保证内容边界和可读性，再处理阴影。阴影统一优先复用 `--shadow`。
- 文件路径、长标题、错误信息和 JSON 必须允许换行或省略，不能撑破卡片。

## 布局与导航

- 桌面端（`min-width: 981px`）使用左侧居中的垂直导航；主内容通过 `.app-shell` 的左侧空间避开导航。
- 移动端保留顶部横向导航和紧凑布局，不要把桌面侧栏强行压缩进内容区。
- 五个页面使用相同的 `.top-nav` 结构、图标和标签顺序：任务提交、提示词工坊、成片、工作流；内容对比页沿用成片入口的当前态。
- 导航位于页面 `header` 外部，并使用独立的 `page-nav` View Transition 层。不要把它重新放回 `.topbar`，否则页面切换时可能短暂出现两层导航卡片。
- 当前页使用 `.top-nav-link.active`，同时保留键盘 `:focus-visible` 状态。

## 主题与状态

- 当前主题保存于 `localStorage` 的 `rh-workflow-theme`，页面启动时先设置 `html[data-theme]`，再渲染内容，避免明显闪烁。
- 可交互元素至少覆盖默认、悬停、键盘聚焦、按下、禁用和加载状态。
- 只有图标的按钮必须提供 `aria-label`，必要时增加 `title`，不能只依赖图形表达含义。
- 错误信息应使用 `--danger`，成功/进行中状态使用 `--accent`，消耗或提醒使用 `--warm`。

## 动效边界

- 页面切换和弹窗动效统一通过 `motion.js` 与 `--motion-fast`、`--motion-interaction`、`--motion-indicator`、`--motion-expand`、`--motion-entry`、`--motion-settle`、`--motion-feedback`、`--motion-submit`、`--motion-page`、`--motion-modal` 控制。`transition` 和 `animation` 不得新增裸写的时长值；动态列表延迟可以使用 JavaScript 计算；`prefers-reduced-motion` 的 `0.01ms` 覆盖属于无障碍例外。
- 动效要服务于定位和状态反馈；避免会持续触发的刷新动画和大范围位移。
- 必须尊重 `prefers-reduced-motion`。
- 页面切换时不要使用 `scrollIntoView` 强行改变用户滚动位置；通过稳定的布局和目标高亮完成定位。需要聚焦时使用 `focus({ preventScroll: true })`。

## 公共入口与响应式边界

- 五个页面使用相同的 `.topbar`、`.top-nav` 结构、图标和标签顺序：工作流、提示词工坊、任务提交、成片；设置入口统一为 `/?openSettings=1` 链接。只有任务提交页实际渲染设置弹窗，其他页面通过该链接回到任务提交页打开设置。
- 公共导航断点固定为桌面 `min-width: 981px` 和移动端 `max-width: 980px`；公共紧凑断点为 `max-width: 650px`。
- 页面内容可因自身布局需要使用额外断点：提示词工坊 `820/560`、成片 `650`、工作流库 `760/520`、内容对比 `820/560`。这些断点只能调整本页排版，不能复制或改写公共导航、按钮、弹窗规则。
