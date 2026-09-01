# SS_tools 本地鸭鸭图解码网页工具

这是 RH_CLI 仓库里基于 SS_tools 的 macOS / Linux 解码后端增加的一个轻量本地网页界面。它只监听 `127.0.0.1`，支持：

- 通过网页上传鸭子图，或调用 macOS 文件选择器获取真实本地路径；
- 可选输入解码密码；
- 设置导出目录或完整文件路径；
- 调用同目录的 `macOS-duck-decoder`，并显示实际生成的输出路径、文件大小和耗时。
- 解码成功后在右侧直接预览图片，或使用原生控件播放视频。

## 启动

macOS 可以在 Finder 中双击 `start_duck_web_ui.command`，或在终端执行：

```bash
cd /Users/apple/Documents/github/RH_CLI/SS_tools
./start_duck_web_ui.command
```

默认浏览器地址是 `http://127.0.0.1:8765/`。不想自动打开浏览器时可以执行：

```bash
./start_duck_web_ui.command --no-browser
```

## Linux

Linux 使用纯 Python 解码后端，不需要 macOS 二进制、ComfyUI、PyTorch 或 MoviePy。首次使用：

```bash
cd /path/to/RH_CLI/SS_tools
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-linux.txt
./start_duck_web_ui.sh
```

Linux 网页中的“从本机路径选择”会自动切换为浏览器文件选择；也可以直接粘贴绝对路径。

网页上传的文件只会在解码期间暂存到系统临时目录，任务结束后自动清理。输入导出目录时，程序会自动使用 `原文件名.decoded` 作为解码器的输出基名；输入带后缀的完整路径时，则直接使用该路径。

预览地址只在当前本地服务进程内有效，并且只指向本次解码产生的文件；视频预览支持浏览器的拖动播放。

注意：不要使用图片编辑器重新保存鸭子图，否则尾部隐藏数据可能被截断。解码器本身支持的载荷类型与密码规则以 SS_tools 上游项目为准：<https://github.com/copyangle/SS_tools>。
