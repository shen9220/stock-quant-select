# generate video

> **前置条件：** 先阅读 [`../../SKILL.md`](../../SKILL.md) 了解认证、全局参数和安全规则。
> **交付闭环：** 生成完成后，需继续执行 `coze file upload` 上传文件并返回在线链接。详细用法参见 [`../coze-file/references/coze-file-upload.md`](../coze-file/references/coze-file-upload.md)。

视频生成。采用**异步任务模型**，分两步操作：创建任务 → 查询状态。

## 异步任务模型

```
Step 1: coze generate video create  →  返回 taskId
Step 2: coze generate video status <taskId>  →  查询任务状态
```

---

## video create

创建视频生成任务。

### 参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `[prompt]` | — | 视频描述文本（或通过 `--stdin` 从管道读取，或首帧/尾帧图至少一项） |
| `--model` | `doubao-seedance-1-5-pro-251215` | 视频生成模型 |
| `--resolution` | `720p` | 分辨率：`480p` / `720p` / `1080p` |
| `--ratio` | `16:9` | 画面比例（如 `16:9`、`9:16`、`1:1`） |
| `--duration` | `5` | 时长（秒），范围 4~12 |
| `--no-watermark` | 有水印 | 添加此标志禁用水印 |
| `--first-frame <url>` | — | 首帧图片 URL |
| `--last-frame <url>` | — | 尾帧图片 URL |
| `--reference-image <url>` | — | 参考图 URL（可多次传入） |
| `--camerafixed` | `false` | 固定镜头（减少运动） |
| `--no-generate-audio` | 生成配音 | 添加此标志不生成配音 |
| `--seed` | — | 随机种子（用于复现相同结果） |
| `--stdin` | — | 从管道读取 prompt 文本（与位置参数二选一，位置参数优先） |
| `--wait` | — | 自动轮询等待完成（2秒间隔，5分钟超时） |
| `--output-path` | — | 保存目录（配合 --wait 使用） |
| `--format` | `text` | 输出格式：`text` / `json` |

### 推荐工作流

**方法 1（推荐）：`--wait` 一站式完成**

```bash
mkdir -p /tmp/coze-video
coze generate video create "一只跳舞的小猫" \
  --wait \
  --output-path /tmp/coze-video \
  --format json
# → 命令完成后直接从 output-path 目录获取视频文件
```

- `--wait` 每 2 秒轮询一次，最长等待 **5 分钟**
- 超时后返回 taskId 供手动查询
- 配合 `--output-path` 可自动保存生成的视频文件

**方法 2：手动两步**

```bash
# Step 1: 创建任务，获取 taskId
coze generate video create "海边日落" --format json
# → 从返回中提取 taskId

# Step 2: 轮询查询状态
coze generate video status <taskId> --format json
```

### 更多示例

```bash
# 指定分辨率和比例
coze generate video create "城市航拍" \
  --resolution 1080p --ratio 16:9 --duration 8 \
  --wait --output-path /tmp/coze-video --format json

# 使用首帧图
coze generate video create "照片动态化" \
  --first-frame https://example.com/frame.jpg \
  --wait --output-path /tmp/coze-video --format json

# 固定镜头 + 无配音
coze generate video create "产品展示" \
  --camerafixed --no-generate-audio \
  --resolution 720p --ratio 1:1 \
  --wait --output-path /tmp/coze-video --format json
```

---

## video status

查询视频生成任务的状态。

### 参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `<taskId>` | 是 | 任务 ID（位置参数，来自 create 的返回值） |
| `--format` | 否 | 输出格式：`text` / `json` |
| `--wait` | 否 | 轮询直到任务到达终态 |

### 推荐命令模板

```bash
# 查询状态
coze generate video status <taskId> --format json

# 轮询等待完成
coze generate video status <taskId> --wait --format json
```

### 状态含义

| 状态 | 含义 | 下一步操作 |
|------|------|-------------|
| `pending` / `processing` | 处理中 | 继续等待或稍后重试查询 |
| `succeeded` | 生成成功 | 从返回中获取视频信息，执行 file upload |
| `failed` | 生成失败 | 检查错误信息，调整参数后重试 |

### 注意事项

- **必须提供 Prompt 文本（位置参数或 `--stdin`）或者首帧/尾帧图片中至少一项作为输入**
- 首帧/尾帧图片必须是有效的可访问 URL
