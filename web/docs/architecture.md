# 应用架构与本地数据

## 运行边界

这是一个纯本地 Web/Electron 应用。浏览器页面只负责编辑草稿、展示状态和调用本机 HTTP 服务；远程 RunningHub 请求由本地 Python 任务管理器发起。

```text
Electron main.cjs
        │ 启动本地 Python 服务
        ▼
server.py  ── HTTP API / 静态文件
        │
        ├── app.py            LocalStore + TaskManager
        ├── prompt_store.py   提示词积木状态
        ├── action_store.py   动作库索引
        └── reference_store.py 人物/音频/背景/服装资源索引
```

## 前端页面职责

| 页面 | 入口 | 主要职责 |
| --- | --- | --- |
| 任务提交 | `index.html` | 导入 API 工作流、识别输入节点、保存路径配置、提交和查看任务队列 |
| 提示词工坊 | `prompt.html` | 管理固定/自由文本积木，组合提示词，导入动作和深度图到任务草稿 |
| 成片 | `outputs.html` | 手动浏览本地产物；点击任务下的工作流名称可恢复该任务并跳转任务提交页，也可把产物导入任务节点或批量清理一星产物 |
| 工作流 | `workflows.html` | 明确导入、保存、编辑、绑定账号/workflowId、导出和删除工作流库副本 |
| 仪表盘 | `dashboard.html` | 按 1D/7D/30D 和账号筛选查看 RH 币消耗、提交次数、处理时长、当前余额和结果快照 |

历史任务产生的工作流临时副本不会自动进入“工作流”页面；工作流库只显示用户明确导入并保存的记录。

## 后端职责

- `server.py` 提供静态文件和本地 JSON API，例如状态、任务、产物、工作流、设置、文件预览和原生文件/目录选择器。
- `app.py` 中的 `LocalStore` 负责 SQLite 任务历史、独立用量记录、工作流资料和本地持久化；`TaskManager` 负责本地等待队列、并发槽位、提交、轮询、产物下载和重启恢复。仪表盘只读取 `usage_records`，首次启动时从已有任务回填，删除任务不会删除用量记录。
- `prompt_store.py`、`action_store.py`、`reference_store.py` 分别管理提示词、动作和参考资源的本地索引；动作/参考图片继续从配置的原目录读取。动作 Markdown 的 `###` 标题作为动作一级分类，条目 `tags:` 作为二级标签，解析时会去掉重复的一级分类标签。
- API Key、账号和 token 等敏感信息只能脱敏后进入页面或日志，不能写入调试输出、文档或 git。

## 数据目录

默认数据根目录是 `web/data/`；设置 `RH_WORKFLOW_DESK_DATA_ROOT` 后可以切换到其他本地数据目录。

- `data/keys.json`：本地 API Key；仪表盘余额按 `account_id` 每个账号只选最近一次成功查询的一个 API Key，同一账号的其他 Key 不参与余额累加，无法识别账号的旧 Key 按站点归并。
- `data/accounts.json`：账号名称、站点和签到状态，不保存密码或 token。
- `data/tasks.sqlite3`：任务历史、状态、远程 taskId、阶段日志、消耗和脱敏错误详情。
- `data/tasks.sqlite3` 中的 `usage_records`：独立用量台账，保存提交次数、RH 币消耗、处理时长和产物数量；任务删除后仍保留。
- `data/workflows/`：工作流库副本和提交期间的工作流快照。
- `data/workflow-registry.json`：工作流名称、所属账号和 workflowId 等登记资料。
- `data/pasted-inputs/`：剪贴板图片副本；普通拖入/选择文件只保存本机绝对路径，不复制原始输入。
- `data/outputs/`：默认产物目录；用户可以在设置中指定外部绝对路径。
- `data/prompt/`：提示词工坊状态和分组状态。

成片库的批量一星清理只更新任务中的产物列表并删除对应本地文件，不删除任务历史；独立用量台账中的原始产物数量也不会因清理而回写。

每个任务的产物目录会保存当前 API 工作流快照，通常为 `<输出目录>/<task_id>/workflow_api.json`，用于重启恢复和再次加载。

## 工作流与账号关系

工作流库记录所属账号和 RunningHub `workflowId`。提交时只能从当前账号的 API Key 中选择与工作流绑定关系允许的 API Key；通用模式才允许使用所有已绑定 API Key。提交前会从导出的 JSON 中剥离本地元数据，不把 `__rh_meta__` 发送给远端。

## 任务生命周期

```text
导入/加载草稿
      ▼
本地等待队列 ── 有并发槽位 ──▶ 远程提交
      ▲                          │
      │                          ▼
      └──── 槽位释放 ◀── 轮询状态/下载产物
                                 │
                       已完成 / 失败 / 取消 / 中断
```

应用重启后先检查任务输出目录：存在产物则恢复为已完成；没有产物但保留远程 `taskId` 则继续轮询；没有远程 `taskId` 的任务才标记为中断。
