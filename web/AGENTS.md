# Web / Electron 开发规则

本目录是 RH_CLI 的纯本地 RunningHub 工作流桌面应用。它同时提供浏览器 Web 版和 macOS Electron 开发版：前端负责编辑和展示，本地 Python 服务负责文件路径解析、任务调度、远程 RunningHub 请求、轮询和本地持久化。

本文件适用于 `web/` 及其子目录；仓库根目录的 `AGENTS.md` 仍然有效。本文件用于补充 Web/Electron 特有的实现和交付规则。

## 项目结构

- `server.py`：本地 HTTP 服务、静态文件服务和 JSON API。
- `app.py`：`LocalStore`、`TaskManager`、任务历史、并发调度、产物和重启恢复。
- `prompt_store.py`、`action_store.py`、`reference_store.py`：提示词、动作和参考资源索引。
- `static/index.html`、`app.js`、`app.css`：任务提交页。
- `static/prompt.*`：提示词工坊。
- `static/outputs.*`：成片/产物浏览页。
- `static/workflows.*`：本地工作流库管理页。
- `static/motion.js`：页面切换、弹窗和媒体预览的共享动效。
- `electron/main.cjs`、`preload.cjs`：Electron 主进程、本地服务启动和原生文件/目录选择器。
- `data/`：本地 API Key、账号、任务 SQLite、工作流副本、任务产物和提示词状态。
- `docs/`：面向后续开发的架构、样式和验证说明；先阅读 [docs/README.md](./docs/README.md)。

## 核心数据边界

- 普通拖入或选择的输入文件只保存本机绝对路径，不复制到项目目录；真正的远程上传只在提交任务时发生。
- 剪贴板图片按产品约定保存到 `data/pasted-inputs/`，这是为了让浏览器剪贴板内容获得可复用的本地路径。
- 工作流库只包含用户明确导入并保存的工作流，不得把历史任务或临时快照自动登记进工作流库。
- 每个任务的 API 工作流快照、输入配置、阶段日志、错误详情和产物路径必须与任务历史保持可恢复关系。
- API Key、账号凭证、token、代理密码和带 token 的 URL 不得出现在日志、截图、测试输出、文档或 git 中。
- 不要通过删除整个 `data/`、SQLite 数据库或用户原始文件来修复单个任务；先定位具体记录并保留可恢复性。

## 前端实现规则

- 优先复用 `app.css` 中的颜色、圆角、阴影、动效和控件令牌；页面专属布局放在对应页面 CSS 中。
- 任务提交页的 `input`、`select`、`textarea` 使用公共控件字号令牌；路径、节点 ID、workflowId 和 JSON 片段可使用等宽字体，但不要单独改变字号基线。
- 在没有明确需求时，不要在按钮或选项框附近用小字、hint 或 note 解释功能；使用清晰的字段标签、控件状态和必要的无障碍名称表达含义。只有需求明确要求时，才添加这类可见辅助说明。
- 前端交互优先局部刷新受影响的组件、列表或状态，避免无必要的整页或全局刷新；局部刷新必须保留当前筛选、滚动位置、媒体播放和键盘焦点等用户上下文。
- 把文件、提示词或参考资源写入任务草稿/任务节点的按钮统一命名为“导入任务”；只有把 API JSON 保存到工作流库时才使用“导入工作流”。
- 桌面端使用左侧固定导航，移动端使用顶部紧凑导航。四个页面的导航结构和顺序保持一致。
- `.top-nav` 必须位于页面 `header` 外部，并保留独立的 `page-nav` View Transition 层，避免切换页面时出现两层导航重叠。
- 图标按钮必须有 `aria-label`，必要时补充 `title`；交互元素要覆盖悬停、键盘聚焦、按下、禁用和加载状态。
- 日夜模式使用现有语义颜色令牌和 `html[data-theme="light"]` 覆盖，不要给组件硬编码另一套颜色。
- 新增动效必须尊重 `prefers-reduced-motion`，不得用持续刷新或大范围位移制造干扰，也不要使用 `scrollIntoView` 强行改变页面滚动位置。
- 修改前端交互后，至少运行相关 JavaScript 的 `node --check` 和对应 Web 测试；涉及视觉变化时，同时检查桌面和窄屏布局。

## 后端和任务规则

- 文件路径、预览和远程上传是三个不同阶段，不能把本地预览成功误认为远程上传成功。
- 本地任务队列必须遵守当前账号、API Key 归属和并发上限；个人 API Key 默认最多 3 个并发，企业、共享和 Wallet API Key 按当前项目规则最多 100 个并发。
- 远程提交失败时，保留脱敏后的 `error_detail` 和阶段日志，便于从任务队列复制错误信息并定位节点。
- 应用重启后先检查任务输出目录，再决定恢复完成、继续轮询或标记中断；不要只依赖内存中的轮询状态。
- 工作流导出必须保留用户对文件输入、提示词、RandomNoise、尺寸和旁路节点的修改；发送给 RunningHub 前剥离本地元数据。
- 新增 API 字段时，同时检查前端草稿、`server.py`、`app.py`、SQLite 迁移和任务加载逻辑，避免只改一侧。

## 本地运行和验证

从仓库根目录运行 Web 版：

```bash
./web/start.sh --no-browser
```

运行 macOS Electron 开发版：

```bash
cd web
npm run electron
```

推荐验证：

```bash
.venv/bin/python -m pytest -q web/tests
node --check web/static/app.js
node --check web/static/prompt.js
node --check web/static/outputs.js
node --check web/static/workflows.js
node --check web/static/motion.js
git diff --check
```

测试默认不得创建真实远程任务，不得产生 RH 币或费用。完整测试出现失败时，要区分本次改动引入的问题和本机参考资源导致的既有基线差异。

## 新功能分支、提交和合并流程

原则：每个新功能从最新 `main` 创建独立分支，功能完成后形成清晰 commit，推送到 GitHub；合并前把功能分支 rebase 到最新 `main`，再合并回 `main`。

开始新功能前先检查工作区：

```bash
git status --short
git switch main
git pull --ff-only origin main
git switch -c feat/web-<short-name>
```

如果工作区有未完成改动，不能擅自覆盖、丢弃或混入新功能；应先确认这些改动归属，必要时由用户决定如何处理。

功能完成后，只提交本功能相关文件：

```bash
git status --short
git add web/<related-files>
git diff --cached --check
git commit -m "feat(web): <简短描述>"
git push -u origin feat/web-<short-name>
```

合并前同步 `main` 并 rebase：

```bash
git switch main
git pull --ff-only origin main
git switch feat/web-<short-name>
git rebase main
git push --force-with-lease origin feat/web-<short-name>
```

确认测试和差异后合并回 `main` 并推送：

```bash
git switch main
git merge --ff-only feat/web-<short-name>
git push origin main
```

rebase 遇到冲突时，只解决当前功能与最新 `main` 的冲突；解决后运行 `git add` 和 `git rebase --continue`，不要使用破坏性重置命令。`--force-with-lease` 只用于 rebase 后更新功能分支，避免覆盖远端其他人的提交。

完成合并后应核对：

- `git status --short` 没有意外文件。
- `git log --oneline --decorate -n 5` 显示预期 commit。
- 相关测试、脚本语法检查和 `git diff --check` 通过。
- GitHub 远端分支和 `main` 的 SHA 与本地预期一致。

除非用户明确要求，不要自动删除功能分支、修改其他分支、同步上游或提交与当前功能无关的脏改动。
