# code project commands

> **前置条件：** 先阅读 [`../../SKILL.md`](../../SKILL.md) 了解认证、全局参数和安全规则。

项目管理相关命令索引。涵盖项目的创建、列表查询、详情获取和删除操作。

## 命令导航

| 文档 | 命令 | 说明 |
|------|------|------|
| 本文档 | `coze code project create` | 创建新项目（仅支持 web/app） |
| 本文档 | `coze code project list` | 列出项目（支持按类型/名称筛选） |
| 本文档 | `coze code project get <id>` | 查看项目详情 |
| 本文档 | `coze code project delete <id>` | 删除项目（不可逆） |

> 注意：所有命令均需先完成认证和组织/空间上下文配置。别名 `coze code proj` 可替代 `coze code project`。

---

## project create

创建新的 Coze Coding 项目。

### 参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `--message` / `-m` | 是 | 项目描述或需求文本 |
| `--type` | 是 | 项目类型，**仅支持 `web` 或 `app`** |
| `--wait` | 否 | 等待项目创建完成（含首次 AI 响应） |
| `--format` | 否 | 输出格式，默认 `text` |

### 推荐命令模板

```bash
# 创建 Web 项目（最常用）
coze code project create --message "创建一个聊天机器人" --type web --format json

# 创建 App 项目
coze code project create -m "移动端应用" --type app --format json

# 等待创建完成（含首次 AI 响应）
coze code project create -m "电商网站" --type web --wait --format json
```

### 返回值

`--format json` 时返回包含 `projectId` 等关键字段的结构化数据。**务必记录 projectId** 供后续 message/deploy/preview 使用。

### 坑点

- **`--type` 只支持 `web` 和 `app`**：不要传 `agent`、`workflow`、`skill` 等其他值，即使 help 文本中列出了这些类型。
- 不带 `--wait` 时，命令会立即返回 projectId，但项目可能还在初始化中。

---

## project list

列出当前组织和空间下的项目。

### 参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `--type` | 否 | 按类型筛选（可多次传入）：`agent` / `workflow` / `app` / `skill` / `web` / `miniprogram` / `assistant` |
| `--name` | 否 | 按名称搜索（模糊匹配） |
| `--size` | 否 | 返回数量限制 |
| `--has-published` | 否 | 是否已发布 |
| `--search-scope` | 否 | 搜索范围 |
| `--format` | 否 | 输出格式，默认 `text` |

### 推荐命令模板

```bash
# 列出所有项目
coze code project list --format json

# 按 Web 类型筛选
coze code project list --type web --format json

# 按名称搜索
coze code project list --name "客服" --format json

# 多类型筛选
coze code project list --type agent --type workflow --format json
```

---

## project get

获取单个项目的详细信息。

### 参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `<project-id>` | 是 | 项目 ID（位置参数） |
| `--format` | 否 | 输出格式，默认 `text` |

### 推荐命令模板

```bash
coze code project get <project-id> --format json
```

---

## project delete

删除指定项目。

### 参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `<project-id>` | 是 | 项目 ID（位置参数） |

### 推荐命令模板

```bash
coze code project delete <project-id>
```

### 注意事项

- **不可逆操作**，执行前确认用户意图。
- 用户已经明确要求删除且目标明确时可直接执行。
