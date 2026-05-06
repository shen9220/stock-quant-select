# file upload

> **前置条件：** 先阅读 [`../../SKILL.md`](../../SKILL.md) 了解认证、全局参数和安全规则。

上传本地文件到 Coze，获取在线可访问地址。这是媒体生成交付闭环的关键环节。

## 命令概述

```bash
coze file upload <file-path> [options]
```

- 业务类型：`cli_attachment`（保存 7 天）
- 将本地文件上传至对象存储，返回可公开访问的 URL

## 参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `<file-path>` | 是 | 本地文件路径（位置参数） |
| `--format` | 否 | 输出格式：`text` / `json` |

## 返回值结构

`--format json` 时：

```json
{
  "url": "https://xxx.coze.cn/...",     // 在线访问地址 → 优先返回给用户！
  "uri": "cos://xxx/..."                // 内部标识，仅作内部引用
}
```

**关键区分**：
- `url`：用户可直接在浏览器打开的 HTTPS 地址 → **交付给用户时用这个**
- `uri`：内部存储路径标识 → 不对外暴露

## 推荐命令模板

```bash
# 上传单个文件
coze file upload /tmp/coze-audio/output.mp3 --format json

# 上传图片
coze file upload /tmp/coze-image/generated.png --format json

# 上传视频
coze file upload /tmp/coze-video/output.mp4 --format json

# 上传用户自定义文件
coze file upload ./document.pdf --format json
```

## 注意事项

- **文件大小限制**：默认 512MB，超出会报错
- `<file-path>` 必须是有效的本地文件路径（不支持目录）
- 文件保存期限为 **7 天**
- 上传成功后务必将 `url`（不是 `uri`）返回给用户
