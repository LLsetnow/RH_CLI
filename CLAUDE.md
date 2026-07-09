# CLAUDE.md

本仓库是 RunningHub 官方 CLI **RH_CLI** 的一个 fork，扩展了「运行原始 ComfyUI 工作流 JSON」的能力，并附带若干示例工作流和鸭鸭图解码器。本文件为在此仓库工作的 Claude 提供上下文。

## 这个仓库是什么

- 上游：`HM-RunningHub/RH_CLI`（官方 `rh` CLI，面向标准模型 + 已发布 AI 应用）。
- 本 fork：`LLsetnow/RH_CLI`。在官方基础上新增了 `rh workflow run` 命令。
- 本地 remote：`origin` = 你的 fork，`upstream` = 官方。同步上游：`git fetch upstream && git merge upstream/main`。

## 相比官方新增的能力

官方 `rh` 只能跑标准模型（`model run`）和已发布 AI 应用（`app run`，走 `/task/openapi/ai-app/run`，只能传 webappId + 节点参数）。本 fork 补上了**提交完整 ComfyUI 工作流图**这条路：

```bash
rh workflow run <工作流.json> -w <workflowId> -i <输入图> [--set nodeId:field=value] [-o 输出目录]
```

- 自动上传输入图并注入 `LoadImage` 节点；提交 `/task/openapi/create`；轮询 `/task/openapi/outputs`；下载结果。
- **`--set nodeId:field=value`**（可重复）覆盖任意节点参数，带类型自动转换。例：`--set 9:denoise=0.4`。
- 代码位置：`src/rh_cli/workflow/{client.py,commands.py}`，在 `src/rh_cli/main.py` 注册。轮询走经典 outputs 端点（**不是**上游的 `/openapi/v2/query`，那个只适用于 AI 应用）。

标准命令仍在：`rh check / image / video / model / app`。

## 安装与配置

```bash
uv tool install <本仓库路径> --editable --with socksio   # editable：改源码即时生效
rh auth set-key <你的 API Key>                            # 存到 ~/.config/rh/config.toml（不是 .env）
rh check                                                  # 验证 key / 查余额
```

- **`--with socksio` 是必需的**：本机有本地 SOCKS 代理（`127.0.0.1:12334`），否则 httpx 建客户端就崩。
- **代理陷阱**：`NO_PROXY` 含 `.cn`，所以 `runninghub.cn` 直连、绕过代理；国际接口才走代理。
- **workflowId** 由 `-w` 传入（工作流页面 URL 末尾的数字）。此前测试用的是 `2075188854994329602`。

## 附带资源

- `workflows/`：示例 ComfyUI 工作流 JSON（**API 格式**导出）——`FaceFix_api.json`、`Qwen 单图 去背景杂物 v2_api.json`。
- `SS_tools/macOS-duck-decoder`：鸭鸭图（隐写图）解码器二进制（25 MB，Mach-O）。
  > ⚠️ `rh workflow run` **不做自动解密**。若某工作流输出的是鸭鸭图，手动解：
  > `SS_tools/macOS-duck-decoder --duck <encoded.png> --out <decoded.png>`
  > 多数工作流（如 FaceFix、去背景杂物 v2）输出的是普通图，不需要解密。

## 工作流 JSON 注意事项

- 必须是 **API 格式**导出（节点为 `{id: {inputs, class_type, _meta}}`）。
- **常见报错**：提交后 `code 805` + `prompt_outputs_failed_validation` + `tuple index out of range`，多为某节点引用了另一节点**不存在的输出槽**（节点版本/连线不匹配）。修法：在 RunningHub 网页版跑通后**重新导出 API JSON**，别盲改本地文件。
- 轮询状态码：`0` 完成、`804` 运行中、`813` 排队、`805` 失败；提交返回 `421` = 队列已满（命令内已做退避重试）。

## 约定

- 用 **uv** 管理与安装；editable 安装下改 `src/rh_cli/` 即时生效。
- **绝不**把 API Key、代理密码打印到日志或提交到 git（`.gitignore` 已忽略 `.env`、`config.json` 等）。
- 想把新功能贡献回上游：从当前分支向 `HM-RunningHub/RH_CLI` 提 PR。
