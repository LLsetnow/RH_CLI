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
| 提示词工坊 | `prompt.html` | 管理固定/自由文本积木，组合提示词，导入动作、深度图和骨骼图资源 |
| 成片 | `outputs.html` | 手动浏览本地产物；点击任务下的工作流名称可恢复该任务并跳转任务提交页，也可把产物导入任务节点或批量清理一星产物 |
| 工作流 | `workflows.html` | 明确导入、保存、编辑、绑定账号/workflowId、导出和删除工作流库副本 |
| 仪表盘 | `dashboard.html` | 按 1D/7D/30D 和账号筛选查看 RH 币消耗、提交次数、处理时长、并发视频响应效率、当前余额、结果快照和注册工作流评分排行 |

历史任务产生的工作流临时副本不会自动进入“工作流”页面；工作流库只显示用户明确导入并保存的记录。

## 后端职责

- `server.py` 提供静态文件和本地 JSON API，例如状态、任务、产物、工作流、设置、文件预览和原生文件/目录选择器。
- `app.py` 中的 `LocalStore` 负责 SQLite 任务历史、独立用量记录、工作流资料和本地持久化；`TaskManager` 负责本地等待队列、并发槽位、提交、轮询、产物下载和重启恢复。仪表盘的消耗指标只读取 `usage_records`，首次启动时从已有任务回填，删除任务不会删除用量记录；注册工作流评分排行则读取成片库当前可见产物的评分，只保留工作流库中已登记的工作流。
- `prompt_store.py`、`action_store.py`、`reference_store.py` 分别管理提示词、动作和参考资源的 JSON 索引；动作/参考资源统一从设置的媒体库 ref 根目录读取固定目录中的 JSON 文件。每个条目保存稳定 `id`、分类、标签、文本和相对媒体路径，前端编辑、新建和删除后由对应 Store 原子重写 JSON；不再生成或读取 Markdown 及派生索引缓存。
- API Key、账号和 token 等敏感信息只能脱敏后进入页面或日志，不能写入调试输出、文档或 git。

## 数据目录

默认数据根目录是 `web/data/`；设置 `RH_WORKFLOW_DESK_DATA_ROOT` 后可以切换到其他本地数据目录。

- `data/keys.json`：本地 API Key 和 `api_key_strategy` 调度策略；仪表盘余额按 `account_id` 每个账号只选最近一次成功查询的一个 API Key，同一账号的其他 Key 不参与余额累加，无法识别账号的旧 Key 按站点归并。
- `data/accounts.json`：账号名称、站点和签到状态，不保存密码或 token。
- `data/tasks.sqlite3`：任务历史、状态、远程 taskId、注册工作流关联、任务级工作流/提示词组/manifest 快照路径、阶段日志、消耗和脱敏错误详情。
- `data/tasks.sqlite3` 中的 `usage_records`：独立用量台账，保存提交次数、RH 币消耗、处理时长和产物数量；任务删除后仍保留。
- `data/workflows/`：工作流库副本、每个工作流可选的 `*.prompt_group.json` 提示词组快照和提交期间的工作流快照。
- `data/workflow-registry.json`：工作流库轻量主索引，只保存本地工作流 ID 和详细记录文件路径。
- `data/workflow-registry/<workflow_id>.json`：每个工作流独立的详细登记资料，包括名称、所属账号、workflowId、输入配置和关联提示词组。
- `data/pasted-inputs/`：剪贴板图片副本；普通拖入/选择文件只保存本机绝对路径，不复制原始输入。
- `data/outputs/`：默认产物目录；用户可以在设置中指定外部绝对路径。每个任务文件夹保存 `workflow_api.json`、`prompt_group.json` 和 `manifest.json`。输入文件只保留原始路径，不复制到任务目录。
- `data/prompt/state.json`：提示词工坊当前组装顺序；`data/prompt/groups.json` 是提示词组主索引，完整组内容按组拆分保存在 `data/prompt/groups/<group-id>.json`。

成片库的批量一星清理只更新任务中的产物列表并删除对应本地文件，不删除任务历史；独立用量台账中的原始产物数量也不会因清理而回写。

任务调度策略由 `TaskManager._automatic_candidates()` 统一处理：任务提交页不再选择 API Key，所有普通提交按策略在个人/共享企业 Key 中选择可用槽位；个人优先模式只有在个人槽位全部占用时才使用共享/企业 Key，个人槽位释放后新任务会重新优先个人。后端仍保留 `key_id` 字段以兼容旧任务记录和内部调用。

本地队列按入库顺序先进先出，任务列表中的排队序号与实际调度顺序一致。调度占用会在 SQLite 事务中同时校验 API Key 并发上限和本地 worker 上限；远程鉴权失败或余额不足会自动将对应 Key 暂停在调度候选之外，重新检测成功后才恢复。远程队列返回 421 时会释放本地 Key 槽位，任务按退避时间重新进入本地队列，避免一个远程拥堵任务长期占住本地并发。

每个任务的产物目录会保存本次提交的 API 工作流快照、提示词组快照和 `manifest.json`，通常分别为 `<输出目录>/<task_id>/workflow_api.json`、`prompt_group.json` 和 `manifest.json`。manifest 只记录输入文件路径，不复制输入文件。普通提交保存当前组装台或请求显式提供的提示词组；Telegram 入站提交保存所选工作流包关联的提示词组。任务加载接口会返回任务自己的工作流快照、提示词组快照和路径字段；旧任务在快照文件仍存在时会自动补写路径清单。

工作流库保存的是工作流 JSON、独立登记 sidecar 与可选提示词组快照的组合：工作流登记主索引保存于 `workflow-registry.json`，每个工作流的详细元数据保存于 `data/workflow-registry/<workflow_id>.json`，提示词组保存为 `data/workflows/<workflow_id>.prompt_group.json`。从工作流页面加载时，工作流写入任务提交页草稿，提示词组写入提示词工坊的一次性恢复缓存；用户进入提示词工坊后会自动恢复该组装台。旧工作流没有快照时只加载工作流本身。

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
