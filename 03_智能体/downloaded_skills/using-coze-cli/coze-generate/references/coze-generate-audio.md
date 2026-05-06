# generate audio

> **前置条件：** 先阅读 [`../../SKILL.md`](../../SKILL.md) 了解认证、全局参数和安全规则。
> **交付闭环：** 生成完成后，需继续执行 `coze file upload` 上传文件并返回在线链接。详细用法参见 [`../coze-file/references/coze-file-upload.md`](../coze-file/references/coze-file-upload.md)。

文本转语音（TTS）。基于 SSE (Server-Sent Events) 流式输出音频分片。

## 核心警告（最高优先级）

**不要只执行裸命令！始终带 `--output-path`！**

```bash
# 错误 ❌ — 裸命令可能不保存文件
coze generate audio "你好"

# 正确 ✅ — 始终指定输出目录
coze generate audio "你好" --output-path /tmp/coze-audio
```

只跑裸命令时，CLI 可能只返回请求信息或任务参数，**不一定会把音频文件保存到本地**，从而导致 agent 误判为"还需要额外下载步骤"。

## 命令概述

```bash
coze generate audio [prompt] [options]
```

- 基于 **SSE (Server-Sent Events)** 流式接收音频分片并写入文件
- `--output-path` 应理解为**保存目录**，CLI 会在该目录下自动写入生成的音频文件

## 参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `[prompt]` | — | 要合成的文本（或通过 `--stdin` 从管道读取） |
| `--speaker` | `zh_female_xiaohe_uranus_bigtts` | 音色/说话人 |
| `--audio-format` | `mp3` | 音频编码：`pcm` / `mp3` / `ogg_opus` |
| `--sample-rate` | `24000` | 采样率 |
| `--speech-rate` | `0` | 语速，范围 -50 ~ 100 |
| `--loudness-rate` | `0` | 响度，范围 -50 ~ 100 |
| `--ssml` | `false` | 将输入视为 SSML 标记语言 |
| `--stdin` | — | 从管道读取 prompt（与位置参数二选一，位置参数优先） |
| `--output-path` | — | **保存目录**（不是最终文件名！） |
| `--format` | `text` | 输出格式：`text` / `json` |

## 标准语音生成工作流

```bash
# 1. 创建输出目录
mkdir -p /tmp/coze-audio

# 2. 生成音频到该目录
coze generate audio "这里放要合成的文本" \
  --output-path /tmp/coze-audio \
  --format json

# 3. 在目录里定位新生成的音频文件（如 output.mp3）

# 4. 上传该音频文件
coze file upload /tmp/coze-audio/<generated-file>.mp3 --format json

# 5. 把上传后的在线链接发给用户
```

### 推荐命令模板

```bash
# 基本生成（使用默认音色）
mkdir -p /tmp/coze-audio
coze generate audio "你好，欢迎使用 Coze CLI" \
  --output-path /tmp/coze-audio \
  --format json

# 指定音色和编码
coze generate audio "欢迎来到我们的直播间" \
  --output-path /tmp/coze-audio \
  --speaker zh_female_xiaohe_uranus_bigtts \
  --audio-format mp3 \
  --sample-rate 24000

# 调整语速和响度
coze generate audio "这段话需要快一点读" \
  --output-path /tmp/coze-audio \
  --speech-rate 50 \
  --loudness-rate 10

# SSML 输入（从管道读取）
echo '<speak>欢迎来到我们的直播间</speak>' | coze generate audio --ssml --stdin \
  --output-path /tmp/coze-audio --format json
```

## SSE 流式输出说明

- 音频通过 Server-Sent Events (SSE) **逐流式**返回分片
- CLI 会自动收集所有分片并合并写入 `--output-path` 指定的目录
- Agent 无需手动处理 SSE 流，只需等待命令完成即可

## 坑点速查

| 坑点 | 说明 | 修正 |
|------|------|------|
| 裸命令不保存文件 | 不带 `--output-path` 时可能只返回元数据 | 始终带 `--output-path` |
| `--output-path` 不是文件名 | 它是保存目录，CLI 自动命名文件 | 传入目录路径如 `/tmp/coze-audio` |
| 生成后忘记上传 | 只说"已生成"不够 | 必须继续 `coze file upload` 再返回 URL |
| 发了 uri 而非 url | 返回结构同时有 uri 和 url | **优先返回 url 给用户** |
