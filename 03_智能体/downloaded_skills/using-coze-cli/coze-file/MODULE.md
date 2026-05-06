---
name: coze-file
version: 1.0.0
description: "Coze 文件操作：上传本地文件获取在线访问地址。当用户需要上传文件、或将本地生成的文件转换为在线可访问链接时触发。"
metadata:
  requires:
    bins: ["coze"]
  cliHelp: "coze file --help"
---

# Coze 文件操作

> **前置条件：** 先阅读 [`../SKILL.md`](../SKILL.md) 完成认证。

## 核心用途

- 为 `coze generate` 产生的本地媒体文件（音频/图片/视频）提供在线访问地址
- 上传用户提供的本地文件供 Coze 项目中使用

## 命令索引

| 意图 | 推荐命令 | Reference |
|------|---------|-----------|
| 上传本地文件 | `coze file upload <path>` | [coze-file-upload.md](references/coze-file-upload.md) |

## 关键规则

- 上传的文件有大小限制（默认 **512MB**）
- 返回值中 `url` 是在线访问地址（优先返回给用户），`uri` 仅作为内部标识
- 使用 `--format json` 获取结构化输出

## 典型使用场景

### 场景 1：媒体生成后的交付闭环

```
coze generate audio "文本" --output-path /tmp/coze-audio
  → coze file upload /tmp/coze-audio/output.mp3
  → 返回 url 给用户
```

### 场景 2：上传用户自定义文件

```bash
coze file upload ./my-document.pdf --format json
```
