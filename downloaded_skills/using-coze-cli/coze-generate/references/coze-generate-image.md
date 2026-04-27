# generate image

> **前置条件：** 先阅读 [`../../SKILL.md`](../../SKILL.md) 了解认证、全局参数和安全规则。
> **交付闭环：** 生成完成后，需继续执行 `coze file upload` 上传文件并返回在线链接。详细用法参见 [`../coze-file/references/coze-file-upload.md`](../coze-file/references/coze-file-upload.md)。

文本生成图片。支持参考图输入、连续组图生成等高级功能。

## 命令概述

```bash
coze generate image [prompt] [options]
```

- 图片**直接同步返回**（不同于视频的异步任务模型）
- 支持 `--stdin` 从管道读取 prompt
- 默认模型：`doubao-seedream-4-5-251128`

## 参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `[prompt]` | — | 图片描述文本（或通过 `--stdin` 从管道读取） |
| `--size` | `2K` | 图片尺寸：`2K` / `4K` / `WIDTHxHEIGHT` |
| `--no-watermark` | 有水印 | 添加此标志禁用水印 |
| `--image <url>` | — | 参考图 URL（可多次传入） |
| `--response-format` | `url` | 响应格式：`url` / `b64_json` |
| `--sequential` | `disabled` | 连续图像生成模式：`auto` / `disabled` |
| `--max-images` | `15` | 连续生成最大图片数（范围 1-15） |
| `--optimize-prompt-mode` | — | 提示词优化模式 |
| `--stdin` | — | 从管道读取 prompt（与位置参数二选一，位置参数优先） |
| `--output-path` | — | **保存目录**（不是最终文件名！） |
| `--format` | `text` | 输出格式：`text` / `json` |

## 标准图片生成工作流

```bash
# 1. 创建输出目录
mkdir -p /tmp/coze-image

# 2. 生成图片到该目录
coze generate image "一只在太空漫步的猫" \
  --output-path /tmp/coze-image \
  --format json

# 3. 从输出中提取 saved_paths，上传文件
coze file upload /tmp/coze-image/<generated-file>.png --format json

# 4. 将返回的在线 URL 发给用户
```

### 更多示例

```bash
# 基本生成
coze generate image "赛博朋克风格的城市夜景" --output-path /tmp/coze-image --format json

# 指定尺寸 + 无水印
coze generate image "山水画" --size 4K --no-watermark --output-path /tmp/coze-image --format json

# 使用参考图
coze generate image "将这张照片转为油画风格" \
  --image https://example.com/photo.jpg \
  --output-path /tmp/coze-image --format json

# 连续生成多张
coze generate image "不同角度的产品展示图" \
  --sequential auto --max-images 4 \
  --output-path /tmp/coze-image --format json

# 自定义分辨率
coze generate image "头像图标" --size 1024x1024 --output-path /tmp/coze-image --format json
```

## 注意事项

- **`--output-path` 是目录**：CLI 在该目录下自动命名保存生成的图片文件
- **同步返回**：命令执行完毕即表示图片已生成完毕，无需额外轮询
- **支持 `--stdin`**：`echo "描述" | coze generate image --stdin --output-path /tmp/img`（需显式传 `--stdin`）
- 尺寸约束：总像素范围约 2560x1440 ~ 4096x4096，宽高比范围约 1:16 ~ 16:1
- `--response-format b64_json` 时返回 base64 编码数据（不写文件），一般场景用默认 `url` 即可
