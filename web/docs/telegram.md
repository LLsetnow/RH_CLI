# Telegram 成片推送

任务完成并且产物已经保存到本机后，RH Workflow Desk 可以通过 Telegram Bot 将每个成片发送到一个聊天。推送在独立后台线程执行，Telegram 网络错误不会影响 RunningHub 任务的完成状态。

## 配置

打开任务提交页右上角“设置”，在“Telegram 成片推送”中填写：

- `Bot Token`：从 Telegram 的 `@BotFather` 创建 Bot 后获得。
- `Chat ID`：接收成片的个人聊天、群组或频道 ID；Bot 必须已经加入目标群组/频道并有发送消息权限。
- 勾选“启用成片自动推送”，点击“保存 Telegram”。

“测试连接”会向目标聊天发送一条测试消息。Bot Token 只保存在本机 `web/data/keys.json`，页面不会回显完整 Token；点击“清除配置”会删除本机保存的 Telegram 配置。

也可以使用环境变量：

```bash
export RH_TELEGRAM_BOT_TOKEN="..."
export RH_TELEGRAM_CHAT_ID="..."
export RH_TELEGRAM_ENABLED="1"
```

环境变量只在启动本地服务时读取，优先级低于本机设置。不要把 Token 写入仓库、日志、截图或提交记录。

## 发送类型与重试

- 图片使用 `sendPhoto`，视频使用 `sendVideo`，音频使用 `sendAudio`。
- 其它文件使用 `sendDocument`；文本产物使用 `sendMessage`。
- 每个任务产物有独立投递记录，应用重启或状态刷新不会重复发送已经成功的产物。
- 单个产物最多自动尝试 3 次；失败会记录在任务详情的“阶段日志”中，可以手动重新运行任务。
