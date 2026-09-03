# RH Workflow Desk

纯本地 RunningHub API 工作流提交 Web 应用。运行状态和缓存保存在本目录下的 `data/`；基础积木源文件保存在 VideoMake 参考资源目录：

- `data/keys.json`：本地 API Key（权限 600）
- `data/accounts.json`：本地托管账号的名称、站点和签到状态（权限 600，不保存密码或 token）
- `data/tasks.sqlite3`：任务历史、状态、taskId、阶段日志和脱敏后的 `error_detail`
- `data/tasks.sqlite3` 中的 `usage_records`：独立用量台账，供仪表盘统计；删除任务不会删除该记录
- `data/workflows/`：API 工作流副本和提交期间的临时工作流文件
- `data/workflow-registry.json`：工作流库中明确保存的工作流名称、所属账号、workflowId 等本地资料索引；只有这里登记的内容会显示在“工作流”页面
- `data/pasted-inputs/`：从剪贴板粘贴的图片输入副本，供任务提交时使用
- `data/outputs/`：默认输出目录下的任务产物
- `VideoMake/ref/prompt/library.md`：提示词基础积木 Markdown 源文件；`data/prompt/state.json`、`data/prompt/groups.json` 保存当前组装顺序和组状态库。基础积木每个 `####` 条目包含 `id`、`tags` 和以 `>` 开头的提示词正文，旧版 JSON 仅保留为兼容备份
- `data/prompt/actions.json`：从 `VideoMake/ref/pose/pose.md` 生成的动作索引；这是可重建缓存，不要手工编辑。动作原图和深度图仍从原目录读取，不会复制到本项目
- `web/docs/translation.md`：提示词工坊自由文本的阿里云翻译配置和英文导出规则
- `web/docs/telegram.md`：任务完成后的 Telegram Bot 成片推送配置和投递规则

仪表盘入口为 `/dashboard`，支持 `1D`、`7D`、`30D` 时间范围，并可在“消耗概况”中按账号筛选或查看全部账号；日消耗、提交次数和处理时长来自 SQLite 中独立的 `usage_records`，不会因为删除历史任务而消失。余额按账号去重，每个账号只取最近一次成功查询的一个 API Key，避免同一账号的多个 Key 重复累加。

## 启动

在仓库根目录运行：

```bash
./web/start.sh
```

默认地址为 `http://127.0.0.1:8766`。也可以不自动打开浏览器：

```bash
./web/start.sh --no-browser
```

macOS Electron 开发版：

```bash
cd web
npm install
npm run electron
```

Electron 版本会自动启动本地 Python 服务，并让拖入文件同时获得本机绝对路径和图片预览。关闭窗口时会停止本地服务。

### macOS 正式安装包

Apple Silicon（arm64）用户可以直接从 [GitHub Releases](https://github.com/LLsetnow/RH_CLI/releases/latest) 下载 `.dmg` 安装包；发布页同时提供 `.zip` 和 `SHA256SUMS.txt`。当前安装包未使用 Apple Developer ID 签名，首次打开时如果 macOS 拦截，请在 Finder 中右键应用选择“打开”，或在“系统设置 → 隐私与安全性”中允许。

从源码重新构建 Apple Silicon 安装包：

```bash
cd web
npm install
npm run package:mac
```

构建会先用 PyInstaller 打包内置 Python 本地服务，再生成带当前版本号的 `web/dist/RH-Workflow-Desk-0.3.0-arm64.dmg`、对应 `.zip` 和校验文件；安装后的任务数据写入 macOS 用户数据目录。

## 说明

- 工作流必须是 ComfyUI API 格式：顶层为节点字典，每个节点包含 `inputs` 和 `class_type`。
- 文件输入会识别常见的 `LoadImage`、`LoadAudio`、`LoadVideo`、`VHS_LoadVideo` 等节点。
- 提示词会识别 `CLIPTextEncode`、`TextEncode*`、`Prompt` 等节点中的文本字段。
- 文件输入支持拖入文件、在输入卡片中按 `⌘V` / `Ctrl+V` 粘贴图片，或点击“预览”进行图片/视频预览；视频节点会使用本机流式预览并支持播放、暂停和进度拖动。剪贴板图片会保存到 `data/pasted-inputs/` 并以本机绝对路径提交。浏览器不会把拖入文件的原始路径交给网页，因此普通文件要提交任务请点击路径框旁的“选择文件”，由 macOS 原生文件选择器返回真实绝对路径，也可以手动填写本机绝对路径。普通拖入文件不会复制到项目目录，真正的远程上传只在执行任务时发生。
- 每个文件、提示词和 RandomNoise 输入卡都支持“旁路”：开启后，本次提交会从 API 工作流中移除该节点，并删除直接指向其输出的下游连线；当前输入不会上传或覆盖。当前填写内容会保留在页面和任务记录中，关闭旁路后可以继续使用。
- 拖动文件悬停在整个输入卡片上方时，卡片会显示绿色发光反馈；工作流输入区域顶部会列出文件和提示词节点标签，点击标签可快速定位到对应卡片。
- 任务队列中的“加载”可以把该任务保存的 API 工作流、文件路径、提示词、workflowId 和 RandomNoise 配置恢复到左侧面板；加载不会复制输入文件。
- 任务队列会先在本地按每个 API Key 的并发上限调度；个人 API Key 的第 4 个任务会保持在本地等待队列，当前任务释放槽位后自动提交。点击任务名称打开详情，“加载”用于恢复到左侧面板。
- 每次提交都会在任务产物目录的 `<task_id>/workflow_api.json` 保存当前 API 工作流快照；加载任务时优先读取快照，旧任务在原始工作流仍存在时会自动补写快照。
- 输入区域支持添加 `RandomNoise` 节点，默认写入 `noise_seed` 和 `mode` 两个参数，模式可选 `fixed` 或 `randomize`；导出或提交时会保留该节点。
- 工作流页面支持 `Ctrl + Enter` 快捷提交；任务完成后，任务队列和详情会显示本次消耗的 RH 币或金额。
- 设置中的默认产物目录支持点击“选择文件夹”通过 macOS/Electron 原生选择器填入绝对路径，仍需点击“保存路径”确认。
- 导入的工作流如果已经保存了本机绝对图片路径，应用会直接读取该路径生成内存预览；任务详情中的“阶段日志”和“错误详情”会持久化到本地数据库，API Key、token、密码等敏感字段会脱敏。
- 顶部“工作流”页面只显示用户在该页面明确导入并保存的 API JSON，并按账号分组；任务提交页和历史任务产生的临时工作流副本不会自动进入工作流库。页面支持搜索、导入、绑定账号和 workflowId、查看 JSON、导出和删除工作流库副本；选择工作流后可手动从导航进入任务提交页。删除工作流不会删除任务历史和产物。
- 导入工作流后需要填写 RunningHub 工作流页面对应的 `workflowId`，它不同于本地保存用的 `wf_...` ID；提交时会单独传给 RunningHub，任务历史也会保留该 ID。
- “导出当前 API”会把当前文件/提示词配置和已旁路后的节点图一起写入 JSON；非空的 `__rh_meta__.workflowId` 会一并保留。重新导入该文件时会自动回填；真正提交前会剥离这段本地元数据，不会发送给 RunningHub。
- 提示词页面的“积木库”可以切换“基础积木”和“动作库”。动作库默认以 `VideoMake/ref/pose/pose.md` 为唯一事实源，同时展示每个动作的原图、深度图和提示词；点击“原图 / 深度图”可切换，点击图片可放大。动作加入组装台后会按顺序保存到当前状态和组状态库。设置中可以改用其他 Markdown 文件，并点击“保存并扫描”立即重新读取。
- 提示词工坊的“自由文本”卡片可通过阿里云翻译为英文；原文修改后需要重新翻译。复制、导出 TXT 和导入任务始终只使用英文翻译结果，存在未翻译的非空自由文本时会阻止这三个操作。阿里云翻译可在任务提交页“设置”中配置，或使用 `ALIBABA_CLOUD_ACCESS_KEY_ID` / `ALIBABA_CLOUD_ACCESS_KEY_SECRET` 环境变量。
- 任务完成并保存本地产物后，可在任务提交页“设置”中启用 Telegram 成片推送；图片、视频、音频和其它文件分别使用对应的 Telegram Bot API 接口发送。推送失败不会影响任务状态，发送结果会写入任务阶段日志；Bot Token 只保存在本机 `web/data/keys.json`，也支持 `RH_TELEGRAM_BOT_TOKEN`、`RH_TELEGRAM_CHAT_ID` 和 `RH_TELEGRAM_ENABLED` 环境变量。
- 基础积木默认读取 `VideoMake/ref/prompt/library.md`。文件使用 `##` 分组、`####` 定义积木，随后写 `id:`、`tags:` 和 `>` 提示词正文；内容按 MiniMax H3 的模式对齐、参考定义、镜头动作、摄影机、声音、对白和音乐字段拆分，拖入工作台后按顺序拼接。设置中可以选择其他 Markdown 文件；修改后刷新提示词工坊即可重新抽取。编辑/新增/删除按钮也会回写该 Markdown 文件。
- 动作库中的“导入任务”会读取当前任务提交页草稿里的 `LoadImage` 节点；动作卡片导入对应深度图，人物/背景/服装等参考卡片导入原图。选择目标节点后，文件的本机路径会写回该节点并保存到本机草稿，之后打开任务提交页会自动恢复。没有任务草稿时，需要先在任务提交页导入工作流。
- 动作素材必须使用同一个 basename 配对：原图放在 `VideoMake/ref/pose/color/<name>.<ext>`，深度图统一直接放在 `VideoMake/ref/pose/depth/<name>_depth.png`，不再使用深度图分类子目录，并在 `pose.md` 的同一个 `####` 条目中同时写入两条图片链接。动作库会检查文件是否存在、basename 是否一致，并显示“原图 + 深度图”或具体缺失/错配原因。
- 扩展动作时只修改素材目录和 `pose.md`：新增原图、生成同名 `_depth.png`、在 pose 分类下添加一个条目和提示词；重新进入动作库或点击“重新扫描”即可更新。应用按 `pose.md` 的 SHA-256 自动失效并重建 `data/prompt/actions.json`，所以不要直接编辑缓存文件。
- 设置中的“个人 API Key 并发数”可配置为 1–3，默认 3，只影响普通个人 API Key；`apiType` 包含 `wallet`、`shared` 或 `enterprise` 时，本地调度器固定按 100 个并发槽处理。
- 设置中的“账号管理”支持分别添加 `runninghub.cn` 和 `runninghub.ai` 账号。首次添加会打开对应站点的 Electron 登录窗口；每个账号使用独立的持久化本地浏览器会话，关闭并重新启动 Electron 后仍可继续使用。点击“签到”会刷新会话并读取 RunningHub 网站返回的 `loginCoinTriggered` / `loginDailyCoin` 状态；如果网站要求重新认证，需要在账号窗口重新登录。网站未返回奖励时，界面会如实显示“未返回奖励”，不会把 100 RH 币当成已签到。
- 应用重启后，任务会先检查 `<输出目录>/<task_id>/`：如果已经存在产物，就重建本地产物记录并恢复为“已完成”；如果没有产物但保留了远程 `taskId`，则自动恢复轮询并继续下载；只有没有远程 `taskId` 的任务才会保留为“已中断”。
- 成片库中每个产物卡片的工作流名称可以直接点击；应用会读取该任务保存的 API 快照和输入配置，写入本地任务提交草稿，但不会自动跳转页面。
- 成片库右上角的“删除一星成片”只删除所有标记为 1 星的产物文件和产物记录，会保留任务记录及同一任务下其他星级的产物；执行前会二次确认。
