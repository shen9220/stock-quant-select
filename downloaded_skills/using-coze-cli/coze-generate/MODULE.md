---
name: coze-generate
version: 1.0.0
description: "Coze 媒体生成：文本生成图片、文本转语音(TTS)、视频生成。当用户需要使用 coze generate image/audio/video 生成音频、图片或视频时触发。"
metadata:
  requires:
    bins: ["coze"]
  cliHelp: "coze generate --help"
---

# Coze 媒体生成

> **前置条件：** 先阅读 [`../SKILL.md`](../SKILL.md) 完成认证。
> **执行前必做：** 执行任何 `generate` 命令前，必须先阅读对应媒体的 reference 文档，再调用命令。

## 统一交付闭环（核心规则）

只要通过 `coze generate` 产出了本地文件，就进入固定闭环：

```
生成本地文件 → coze file upload → 返回在线 URL
```

- 该规则适用于**音频、图片、视频**所有媒体类型
- 禁止返回本地路径给用户（`/tmp/...` 对用户不可访问）
- 优先使用 `url` 而非 `uri` 作为交付链接

详细上传用法参见 [`../coze-file/references/coze-file-upload.md`](../coze-file/references/coze-file-upload.md)。

## 三种媒体对比总览

| 维度 | image (图片) | audio (语音) | video (视频) |
|------|-------------|-------------|-------------|
| 命令 | `coze generate image` | `coze generate audio` | `coze generate video create` |
| 同步/异步 | **同步返回** | **SSE 流式输出** | **异步任务** |
| 输出路径 | `--output-path` (目录) | `--output-path` (目录) | `--output-path` (目录) |
| 必须带 --output-path | 推荐 | **必须** | 推荐（配合 --wait） |
| 轮询机制 | 无 | 无 | `create` → `status` 或 `--wait` |
| 默认格式 | url | mp3 | — |

## Agent 禁止行为

- **不要只执行裸命令不带 `--output-path`**（尤其是 audio！裸命令可能不保存文件）
- **不要把请求信息/元数据误判为最终结果**
- **不要把 `--output-path` 当成最终文件名**（它是一个保存目录）
- **不要在视频生成后不轮询 status 就声称完成**
- **不要把本地路径发给用户**

## 意图 → 命令索引

| 意图 | 推荐命令 | Reference |
|------|---------|-----------|
| 生成图片 | `coze generate image` | [coze-generate-image.md](references/coze-generate-image.md) |
| 语音合成(TTS) | `coze generate audio` | [coze-generate-audio.md](references/coze-generate-audio.md) |
| 创建视频任务 | `coze generate video create` | [coze-generate-video.md](references/coze-generate-video.md) |
| 查询视频状态 | `coze generate video status <taskId>` | [coze-generate-video.md](references/coze-generate-video.md) |

## Reference 导航

| 文件 | 说明 |
|------|------|
| [coze-generate-image.md](references/coze-generate-image.md) | 图片生成详细参数与工作流 |
| [coze-generate-audio.md](references/coze-generate-audio.md) | 音频/TTS 详细参数与工作流（含 SSE 流式说明） |
| [coze-generate-video.md](references/coze-generate-video.md) | 视频异步任务模型详解（create + status） |

## 长耗时任务处理

| 命令 | 轮询间隔 | 说明 |
|------|----------|------|
| `coze generate video create --wait` | 2 秒 | 等待视频生成完成，5 分钟超时 |

对 `video create` 优先采用 `--wait` 参数让 CLI 自动轮询。超时后返回 taskId 供手动查询。不能只把结果留在本地日志里等待用户追问。

## 常见错误速查（Generate 专用）

### 错误 2：把 `coze generate audio` 的请求信息误判为最终结果

- 问题：只拿到了请求元数据，没有拿到用户可访问的音频文件。
- 修正：给命令增加 `--output-path`，然后检查输出目录中的生成文件。

### 错误 4：误把 `--output-path` 当成最终文件名

- 问题：CLI 帮助说明其语义更接近保存目录。
- 修正：优先传入目录路径（如 `/tmp/coze-audio`），并在生成后从目录中定位实际文件名。
