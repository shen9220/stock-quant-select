# code domain commands

> **前置条件：** 先阅读 [`../../SKILL.md`](../../SKILL.md) 了解认证、全局参数和安全规则。

自定义域名管理命令。用于为项目绑定和管理自定义域名。

## 命令概览

| 命令 | 说明 |
|------|------|
| `coze code domain list <project-id>` | 列出项目已绑定的域名 |
| `coze code domain add <domain> -p <id>` | 添加自定义域名 |
| `coze code domain remove <domain> -p <id>` | 删除自定义域名 |

---

## domain list

列出项目关联的所有自定义域名。

### 参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `<project-id>` | 是 | 项目 ID（位置参数） |

### 推荐命令模板

```bash
coze code domain list <project-id>
```

---

## domain add

为项目添加自定义域名。

### 参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `<domain>` | 是 | 域名（位置参数） |
| `-p` / `--project-id` | 是 | 项目 ID |

### 推荐命令模板

```bash
coze code domain add example.com -p <project-id>
coze code domain add api.myapp.com -p <project-id>
```

---

## domain remove

从项目移除自定义域名。

### 参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `<domain>` | 是 | 域名（位置参数） |
| `-p` / `--project-id` | 是 | 项目 ID |

### 推荐命令模板

```bash
coze code domain remove example.com -p <project-id>
```
