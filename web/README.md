# RH Workflow Desk

纯本地 RunningHub API 工作流提交 Web 应用。所有运行数据都保存在本目录下的 `data/`：

- `data/keys.json`：本地 API Key 凭据（权限 600）
- `data/tasks.sqlite3`：任务历史、状态、taskId 和产物索引
- `data/workflows/`：上传过的 API 工作流副本
- `data/outputs/`：默认输出目录下的任务产物

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

## 说明

- 工作流必须是 ComfyUI API 格式：顶层为节点字典，每个节点包含 `inputs` 和 `class_type`。
- 文件输入会识别常见的 `LoadImage`、`LoadAudio`、`LoadVideo`、`VHS_LoadVideo` 等节点。
- 提示词会识别 `CLIPTextEncode`、`TextEncode*`、`Prompt` 等节点中的文本字段。
- 文件输入支持拖入文件或点击“预览”进行图片预览；浏览器不会把原始路径交给网页，因此要提交任务请点击路径框旁的“选择文件”，由 macOS 原生文件选择器返回真实绝对路径，也可以手动填写本机绝对路径。应用不会把输入文件复制到项目目录，真正的远程上传只在执行任务时发生。
- `apiType` 包含 `shared` 或 `enterprise` 时（包括 wallet 型 Key），本地调度器按 100 个并发槽处理；其他已验证且有余额的 Key 按 3 个并发槽处理。
- 应用重启后，已完成任务历史仍可查看；重启前尚未完成的任务会标记为“已中断”，不会伪装成已完成。
