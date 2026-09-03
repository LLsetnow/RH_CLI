# 提示词工坊翻译

提示词工坊的“自由文本”积木支持使用阿里云机器翻译通用版翻译为英文。卡片上方是原文输入框，下方是英文结果框；点击“翻译”后，结果会保存到本机提示词状态。

## 配置

打开任务提交页的“设置”，在“阿里云翻译”中填写：

- `AccessKey ID`
- `AccessKey Secret`

配置只保存到本机 `web/data/keys.json`，服务端调用阿里云，浏览器不会直接接触 Secret。也可以在启动 Web/Electron 前设置环境变量：

```bash
export ALIBABA_CLOUD_ACCESS_KEY_ID="你的 AccessKey ID"
export ALIBABA_CLOUD_ACCESS_KEY_SECRET="你的 AccessKey Secret"
```

阿里云接口使用 `TranslateGeneral`，源语言为自动识别，目标语言固定为 `en`，单块自由文本最多 5000 个字符。详见[阿里云通用版文本翻译调用指南](https://help.aliyun.com/zh/machine-translation/developer-reference/api-reference-machine-translation-universal-version-call-guide)。

## 使用规则

- 修改原文后，旧英文结果会清空，需要重新点击“翻译”。
- 没有英文结果的非空自由文本不会进入成品提示词。
- “复制文本”“导出 TXT”和“导入任务”共用英文提示词结果；存在未翻译自由文本时，这三个操作会被禁用。
- 翻译失败只保留原文，不会把未确认的中文内容悄悄当作英文导出。
