# 本地开发与验证

## 启动

在仓库根目录启动纯 Web 版：

```bash
./web/start.sh --no-browser
```

macOS Electron 开发版：

```bash
cd web
npm install
npm run electron
```

Electron 会启动本地 Python 服务并打开桌面窗口。输入文件通过原生选择器获取本机绝对路径；真正的远程上传只在任务提交时发生。

## 修改前的检查

1. 先确认目标页面和已有的公共类名，避免在多个页面重复定义同一控件。
2. 涉及任务字段时，检查前端草稿、`server.py` 请求处理和 `app.py` 持久化是否仍使用同一字段名。
3. 涉及文件输入时，区分“保存路径”“本地预览”和“远程上传”三个阶段，不要为普通输入文件增加项目内复制。
4. 涉及任务历史时，保留现有的 API 快照、阶段日志、`error_detail` 和重启恢复逻辑。

## 推荐验证命令

```bash
# Web Python 测试
.venv/bin/python -m pytest -q web/tests

# 前端脚本语法
node --check web/static/app.js
node --check web/static/prompt.js
node --check web/static/outputs.js
node --check web/static/workflows.js
node --check web/static/compare.js
node --check web/static/motion.js

# 前端风格回归检查
uv run pytest -q web/tests/test_frontend_style.py

# 阿里云翻译接口与自由文本状态测试（不会访问真实阿里云）
uv run pytest -q web/tests/test_translation.py web/tests/test_prompt_store.py

# 检查补丁中是否有空白错误
git diff --check
```

如果只修改了前端样式，可以先运行对应测试文件和 `node --check`，再决定是否运行完整测试集。完整测试失败时要区分本次改动引入的问题和已有基线失败。

## 安全约定

- 不在日志、截图、测试输出、文档或 git 中打印 API Key、账号凭证、代理密码和带 token 的 URL。
- 不把用户原始输入文件复制进项目目录；仅剪贴板图片按当前产品约定保存到 `data/pasted-inputs/`。
- 不为了修复单个任务删除整个 `data/` 或 SQLite 数据库；先备份并定位具体记录。
- 测试默认不创建真实远程任务，不产生 RH 币或费用。

## UI 交付清单

- 任务提交页的输入框和下拉框遵循公共控件字号 token。
- 桌面端左侧导航保持固定，不因右侧内容高度变化而跳动。
- 日夜主题下均检查文本、边框、按钮、弹窗和错误状态的对比度。
- 拖放区覆盖完整输入卡片，悬停、预览、路径写回和选择文件行为不能互相覆盖。
- 图标按钮有 `aria-label`，键盘操作可用，减少动效设置下仍能完成主要流程。
