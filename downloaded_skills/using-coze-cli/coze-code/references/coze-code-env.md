# code env commands

> **前置条件：** 先阅读 [`../../SKILL.md`](../../SKILL.md) 了解认证、全局参数和安全规则。

环境变量（Secrets）管理命令。用于管理项目级别的环境变量，支持区分开发和生产环境。

## 命令概览

| 命令 | 说明 |
|------|------|
| `coze code env set <key> <value> -p <id>` | 设置环境变量 |
| `coze code env list -p <id>` | 列出环境变量 |
| `coze code env delete <key> -p <id>` | 删除环境变量 |

### 共享参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `-p` / `--project-id` | 是 | 项目 ID |
| `--env` | 否 | 环境标识：`dev`（默认）或 `prod` |

---

## env set

设置项目环境变量。

### 参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `<key>` | 是 | 环境变量名 |
| `<value>` | 是 | 环境变量值 |
| `-p` / `--project-id` | 是 | 项目 ID |
| `--env` | 否 | 目标环境：`dev`（默认）/ `prod` |

### 推荐命令模板

```bash
# 设置开发环境变量（默认 dev）
coze code env set API_KEY sk-xxxxx -p <project-id>

# 设置生产环境变量
coze code env set DATABASE_URL postgres://... -p <project-id> --env prod
```

---

## env list

列出项目的所有环境变量。

### 参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `-p` / `--project-id` | 是 | 项目 ID |
| `--env` | 否 | 目标环境：`dev`（默认）/ `prod` |

### 推荐命令模板

```bash
# 列出开发环境变量（默认）
coze code env list -p <project-id>

# 列出生产环境变量
coze code env list -p <project-id> --env prod
```

---

## env delete

删除指定的环境变量。

### 参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `<key>` | 是 | 要删除的环境变量名 |
| `-p` / `--project-id` | 是 | 项目 ID |

### 推荐命令模板

```bash
coze code env delete API_KEY -p <project-id>
```
