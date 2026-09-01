# RunningHub workflowId 输入与 API 导出设计

## 目标

在本地 Web 工作流提交台中增加 RunningHub 远程 `workflowId` 的输入、保存、提交和导出能力，解决本地工作流 ID 与 RunningHub 远程工作流 ID 混用导致的提交失败问题。

## 范围

本次只涉及 `web/` 本地 Web 应用及相关测试：

- 工作流页面增加 RunningHub `workflowId` 输入框。
- 提交任务时将远程 `workflowId` 传给 RunningHub。
- 任务历史记录保存本次使用的远程 `workflowId`。
- 导出当前 API 工作流时一并导出远程 ID。
- 重新导入带有该元数据的 API 文件时自动回填输入框。
- 不复制用户输入文件；文件输入仍只保存和提交本机路径。

不改变本地工作流文件 ID（`wf_...`）的用途，也不改变 CLI 的现有参数行为。

## 方案

### 两种 ID 的职责

| 字段 | 示例 | 用途 |
| --- | --- | --- |
| `workflow_id` | `wf_a1b2c3d4e5f6` | 本地数据库和 `web/data/workflows` 中定位工作流文件 |
| `remote_workflow_id` | `123456789` | RunningHub API 提交时的远程工作流 ID |

前端状态使用明确的 `remoteWorkflowId`，避免继续把本地 `workflow_id` 当作远程 ID。后端任务记录使用 `remote_workflow_id`，旧任务没有该字段时按空值兼容。

### 工作流页面

在工作流导入和分析结果区域增加一组字段：

- 标签：`RunningHub workflowId`
- 占位提示：`输入 RunningHub 工作流 ID`
- 提示文字：`这是远程工作流 ID，不是本地 wf_... ID`

导入工作流后，如果 JSON 中包含 `__rh_meta__.workflowId`，自动填入该值；用户也可以手动修改。提交前校验该字段非空，避免再次发出缺少远程 ID 的请求。

沿用当前项目的卡片、输入框、日间/夜间主题和移动端布局，不新增独立视觉组件。

### API 导出格式

导出文件保留原有 ComfyUI API 节点结构，并在顶层增加项目保留元数据：

```json
{
  "__rh_meta__": {
    "workflowId": "123456789"
  },
  "1": {
    "class_type": "LoadImage",
    "inputs": {}
  }
}
```

约定如下：

- `__rh_meta__` 是 RH Workflow Desk 的保留键，不参与节点校验和节点输入修改。
- 没有填写远程 ID 时，导出文件不写入空的 `workflowId`。
- 导入时读取该保留键并从节点分析中排除，不影响已有 API 工作流。
- 提交给 RunningHub 前剥离 `__rh_meta__`，远程请求中的 `workflow` 仍是纯 ComfyUI API 节点图。
- 用户重新导出时，以当前输入框内容覆盖元数据中的 ID。

使用元数据而不是把 `workflowId` 伪装成节点，是为了避免 ComfyUI API 校验器和其他工作流工具把它当作无效节点；提交前剥离则保证远程接口不接收到本地应用字段。

### 提交数据流

```text
导入 JSON
  -> 保存本地 workflow_id
  -> 读取 __rh_meta__.workflowId（如有）
  -> 用户填写/修改 remoteWorkflowId
  -> POST /api/tasks { workflow_id, remote_workflow_id, ... }
  -> 本地任务保存 remote_workflow_id
  -> 调度时读取并传入 _submit(..., workflow_id=remote_workflow_id, workflow=...)
  -> 提交前移除 __rh_meta__
```

文件输入处理保持现状：拖放、文件选择和路径输入只记录本机绝对路径；不会复制输入文件到项目目录。

## 后端接口与数据兼容

`POST /api/workflows/analyze` 的响应新增 `remote_workflow_id`（可为空），由导入的 `__rh_meta__` 解析得到。

`POST /api/tasks` 新增可选字段 `remote_workflow_id`。服务端要求其非空后才创建任务，错误码使用 `MISSING_WORKFLOW_ID`，错误信息明确指出需要填写 RunningHub workflowId。

本地 SQLite 的任务表新增 `remote_workflow_id TEXT NOT NULL DEFAULT ''`，启动时执行兼容迁移。旧任务正常展示，远程 ID 为空时显示“未记录”。

任务提交时：

- `workflow_id` 继续用于读取本地工作流。
- `remote_workflow_id` 传给 RunningHub 的 `workflowId` 参数。
- 提交的 workflow JSON 先移除 `__rh_meta__`。

## 错误处理

- 没有导入工作流：提示先导入 API 工作流。
- 没有填写远程 ID：前端阻止提交并定位到输入框；后端再次校验。
- 远程 ID 不是纯数字时不在前端强制转换，保留字符串传递，以兼容接口允许的 ID 表示形式；空白值视为空。
- 导出不因远程 ID 为空失败，仍可导出节点配置，但不写入空元数据。
- 保留已有阶段日志和 `error_detail`，并在提交阶段记录是否使用了远程 workflowId（不记录 API Key）。

## 验证

后端测试覆盖：

- API 工作流带 `__rh_meta__.workflowId` 时能正确分析并回传。
- 保留元数据不被当作节点，也不影响文件/提示词节点识别。
- 创建任务时保存 `remote_workflow_id`。
- 提交调用使用远程 ID，并在请求 workflow 中移除 `__rh_meta__`。
- 缺少远程 ID 时拒绝创建任务，旧任务记录兼容读取。

前端验证覆盖：

- 输入框显示、编辑和导入自动回填。
- 提交请求包含 `remote_workflow_id`，为空时不发请求。
- 导出 JSON 包含当前输入框中的 `__rh_meta__.workflowId`，不包含空值。
- 日间/夜间主题、已有文件预览和任务队列不回归。

运行现有 Python 测试、JavaScript 语法检查、`git diff --check`，并启动本地 Electron 开发版验证健康检查与基本路由；不发起真实远程任务。

## 实现文件

- `web/app.py`：任务字段迁移、远程 ID 校验、提交参数和元数据剥离。
- `web/server.py`：分析响应和任务请求字段。
- `web/static/index.html`：workflowId 输入框。
- `web/static/app.js`：状态、导入回填、提交和导出逻辑。
- `web/static/app.css`：沿用现有主题的字段布局和提示样式。
- `web/tests/test_app.py`：后端兼容、提交参数和元数据行为测试。
- `web/README.md`：补充 workflowId 使用和导出格式说明。
