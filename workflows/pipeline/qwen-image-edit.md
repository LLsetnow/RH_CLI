# Qwen 图片编辑流水线（Agent 执行指令）

单阶段流水线：输入一张图片 → Qwen Image Edit 工作流编辑 → 鸭鸭图解码 → 输出真图。

---

## 前置条件

- `rh` CLI 已安装，API Key 已配置
- workflowId 映射已配好（`rh auth set-workflow-id <ID> Qwen+单图编辑_api.json`）
- 鸭鸭图解码器位于 `SS_tools/macOS-duck-decoder`

---

## Step 1：提交 Qwen 图片编辑工作流

```bash
rh workflow run workflows/ai/Qwen+单图编辑_api.json \
    -i "<输入图片路径>" \
    -o "output/temp/<输出名>_duck.png"
```

输出为鸭子图（工作流内置 DuckHideNode 加密）。

如需覆盖提示词（节点 68 的正向 prompt），追加：
```bash
--set "68:prompt=你的新提示词"
```

如需覆盖随机种子（节点 65）：
```bash
--set "65:seed=123456789"
```

> 提示词示例：`"去掉背景，保留人物，高画质"`

---

## Step 2：解码鸭子图 → 输出真图

```bash
DECODER="SS_tools/macOS-duck-decoder"
"$DECODER" --duck "output/temp/<输出名>_duck.png" \
           --out "output/<输出名>.png"
```

解码后得到真图。

---

## 完整示例

```bash
# 输入：input/photo.jpg，输出：output/result.png

# Step 1：编辑
rh workflow run workflows/ai/Qwen+单图编辑_api.json \
    -i input/photo.jpg \
    --set "68:prompt=去掉背景杂物，保留人物主体，面部保持不变" \
    -o output/temp/photo_duck.png

# Step 2：解码
SS_tools/macOS-duck-decoder \
    --duck output/temp/photo_duck.png \
    --out output/photo_result.png
```

---

## 流程图

```
输入图片
    │
    └── Step 1 ── Qwen 编辑 ──► output/temp/ (鸭子图)
            │
            └── Step 2 ── 解码 ──► output/ (真图)
```

---

## 关键节点对照

| 节点 | 类型 | 说明 |
|------|------|------|
| 41 | `LoadImage` | 输入图片（自动替换为上传文件） |
| 68 | `TextEncodeQwenImageEditPlus` | 正向提示词，通过 `--set 68:prompt=...` 修改 |
| 69 | `TextEncodeQwenImageEditPlus` | 负向提示词（为空） |
| 65 | `KSampler` | 采样器，steps=4, cfg=1, seed 可用 `--set` 改 |
| 93 | `LoraLoaderModelOnly` | Lightning 4-step LoRA（加速推理） |
| 111 | `ResizeLongestToNode` | 图片缩放，当前 size=1280 |
| 104 | `DuckHideNode` | 鸭鸭图加密（无密码） |
| 105 | `SaveImage` | 保存加密结果 |

---

## 注意事项

- 模型是 `qwnImageEdit_v16Bf16`（16B 参数），4-step Lightning LoRA，跑一次约 1–2 分钟
- 输出是鸭子图，必须解码才能得到真图
- 提示词建议用中文，效果更好
- 节点 111 `size=1280` 控制图片缩放，图片最长边会缩到 1280
- 如果图片有审核风险（裸露/敏感内容），RunningHub 会返回 code 805
