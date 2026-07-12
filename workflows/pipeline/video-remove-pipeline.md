# 动作迁移流水线（Agent 执行指令）

将一段输入视频，经过「提取首帧 → 图片编辑 → 用户审核 → SCAIL-2 动作迁移」流程，最终输出动作迁移后的视频。

---

## 前置条件

- `rh` CLI 已安装，API Key 和 workflowId 已配置
- 工作流文件位于 `workflows/ai/` 目录
- 输入视频位于 `input/videos/` 目录
- 鸭鸭图解码器位于 `SS_tools/macOS-duck-decoder`

---

## Step 1：提取视频首帧

从 `input/videos/` 目录下选取一个视频文件。

执行：

```bash
uv run python script/extract_first_frame.py \
    "input/videos/<视频文件名>" \
    -o "input/firstFrame/<视频名>_first_frame.png"
```

---

## Step 2：提交 Qwen 图片编辑工作流

将首帧提交给 Qwen 图片编辑工作流，输出图为鸭子图。

执行：

```bash
rh workflow run workflows/ai/Qwen+单图编辑_api.json \
    -i "input/firstFrame/<首帧文件名>" \
    -o "output/temp/<视频名>_edited.png"
```

如需覆盖提示词，追加 `--set "68:prompt=新提示词"`。

---

## Step 3：解码鸭子图 → referenceImage

```bash
DECODER="SS_tools/macOS-duck-decoder"
"$DECODER" --duck "output/temp/<视频名>_edited.png" \
           --out "input/referenceImage/<视频名>_reference.png"
```

---

## ⛔️ 审核断点

**执行到此处必须暂停，将 referenceImage 展示给用户，等待确认后方可继续。**

Agent 行为：

```
1. 将 input/referenceImage/ 下的解码结果告知用户
2. 询问："referenceImage 已生成，是否满意？继续提交 SCAIL-2 动作迁移？"
3. 等待用户回复
4. 如果用户确认 → 继续 Step 4
5. 如果用户拒绝/要求修改 → 返回 Step 2 重新编辑，或终止流水线
```

---

## Step 4：提交 SCAIL-2 动作迁移工作流

用户确认通过后，以**原视频（驱动源）**和**referenceImage（参考角色）**为双输入。

### 工作流说明

SCAIL-2 是端到端角色动画模型，不需要骨骼/ControlNet，通过 SAM3 自动分割人物并生成彩色控制 mask。

模式由节点 199（`replacement_mode`）控制：
- `false` = **动画模式**：把参考图人物的外观迁移到驱动视频的动作上
- `true` = **替换模式**：把驱动视频中的人物替换为参考图人物

### 关键可调参数

| 节点 | 类型 | 说明 |
|------|------|------|
| 179 | prompt | 动作描述，如 `"a woman is dancing"` |
| 195 | 视频分割提示词 | 驱动视频中要跟踪的对象，如 `"person in the middle"` |
| 266 | 参考图分割提示词 | 参考图中要提取的角色，如 `"person in the middle"` |
| 199 | replacement_mode | `false`=动画, `true`=替换 |
| 206 | seed | 随机种子 |
| 210 | resolution limit | 分辨率限制，默认 1280 |
| 217 | video clips | 视频分段数（长视频分成多段生成再拼接），默认 2 |
| 11 | LoRA 强度 | `strength_model`，默认 0.8 |

### 提交命令

```bash
rh workflow run "workflows/ai/SCAIL2+Animation+&+Replacement+动作迁移&角色替换_api.json" \
    -i "input/videos/<视频文件名>" \
    --set "30:image=input/referenceImage/<参考图文件名>" \
    --instance-type plus \
    -o "output/temp/<视频名>_result.png"
```

> `--instance-type plus` 指定 48GB GPU 实例。钱包余额 Key 可用，RH 币 Key 不支持。24GB 默认实例此工作流会 OOM。

> `-i` 自动上传视频替换节点 33（VHS_LoadVideo），`--set 30:image=...` 替换参考图 LoadImage 节点。

### 常用参数覆盖

```bash
# 修改提示词
--set "179:value=a person is performing a dance"

# 修改分辨率（模型用 FP8 ~17.7GB，24GB 可跑 720p）
--set "210:value=960"

# 切换为替换模式
--set "199:value=true"

# 修改种子
--set "206:value=1234567890"

# 修改视频分段数（长视频建议增大，避免显存溢出）
--set "217:value=3"
```

---

## Step 5：解码输出视频

```bash
"$DECODER" --duck "output/temp/<视频名>_result.png" \
           --out "output/videos/<视频名>_result.mp4"
```

---

## 流程图

```
input/videos/<视频>.mp4
    │
    ├── Step 1 ── 提取首帧 ──► input/firstFrame/
    │       │
    │       └── Step 2 ── Qwen 编辑 ──► output/temp/ (鸭子图)
    │               │
    │               └── Step 3 ── 解码 ──► input/referenceImage/ (真图)
    │                                          │
    │                    ⛔ 审核断点 ── 等待用户确认
    │                                          │
    └── Step 4 ── SCAIL-2 动作迁移 ──► output/temp/ (鸭子图)
            │
            └── Step 5 ── 解码 ──► output/videos/ (最终视频)
```

---

## 关键节点对照

### Qwen 图片编辑工作流

| 节点 | 类型 | 说明 |
|------|------|------|
| 41 | `LoadImage` | 输入图片（自动替换） |
| 68 | `TextEncodeQwenImageEditPlus` | 正向提示词 |
| 65 | `KSampler` | 采样器（4-step Lightning） |
| 104 | `DuckHideNode` | 鸭鸭图加密 |
| 105 | `SaveImage` | 保存结果 |

### SCAIL-2 动作迁移工作流

| 节点 | 类型 | 说明 |
|------|------|------|
| 33 | `VHS_LoadVideo` | 驱动视频（自动替换） |
| 30 | `LoadImage` | 参考角色图（需 `--set` 替换） |
| 74 | `DiffusionModelLoaderKJ` | 模型：SCAIL-2 FP8（17.7GB） |
| 11 | `LoraLoaderModelOnly` | Lightx2v LoRA，strength=0.8 |
| 85 | `SAM3_VideoTrack` | SAM3 跟踪驱动视频中的目标 |
| 91 | `SAM3_VideoTrack` | SAM3 跟踪参考图中的角色 |
| 104 | `SCAIL2ColoredMask` | 生成 SCAIL-2 彩色控制 mask |
| 114/115 | `WanSCAILToVideo` | 生成节点（两段式：第一段生成 + 第二段拼接） |
| 179 | prompt | 动作描述 |
| 199 | replacement_mode | 动画/替换模式切换 |
| 206 | seed | 随机种子 |
| 210 | resolution limit | 分辨率限制 |
| 217 | video clips | 视频分段数 |
| 299 | `DuckHideNode` | 鸭鸭图加密 |
| 301 | `SaveImage` | 保存结果 |

---

## 注意事项

- SCAIL-2 使用 FP8 模型（~17.7GB），24GB 显存可跑，不需要 SCAIL-help 的 ControlNet/NLF/Complex 额外节点
- 视频会自动缩放，分辨率由节点 210 控制
- 长视频通过节点 217（video clips）分段生成，分段越多显存越低但接缝可能可见
- 输出是鸭子图，必须解码才能得到真视频
- `output/temp/` 下的中间产物可定期清理
