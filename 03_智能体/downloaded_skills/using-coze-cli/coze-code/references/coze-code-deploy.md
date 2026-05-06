# code deploy commands

> **前置条件：** 先阅读 [`../../SKILL.md`](../../SKILL.md) 了解认证、全局参数和安全规则。

部署相关命令索引。涵盖将项目部署到生产环境、查询部署状态、自动修复失败部署和浏览部署历史。

## 命令导航

| 文档 | 命令 | 说明 |
|------|------|------|
| 本文档 | `coze code deploy <project-id>` | 部署到生产环境 |
| 本文档 | `coze code deploy status <project-id>` | 查询部署状态 |
| 本文档 | `coze code deploy fix <project-id>` | 自动修复部署失败 |
| 本文档 | `coze code deploy list <project-id>` | 查看部署历史列表 |

---

## deploy

将项目部署到生产环境。

### 核心坑点（最高优先级标注）

**项目 ID 是位置参数，不是 `--project-id` 选项！**

```bash
# 正确 ✅
coze code deploy <project-id>

# 错误 ❌
coze code deploy --project-id <project-id>
```

### 前置条件

1. **message status 必须已结束**（不能是 `processing` 状态）
2. 项目必须有 commit 记录（否则报 `MISSING_COMMIT_HASH` 错误）
3. Mini-program 类型项目需先完成 AppID 授权配置（否则报 `MINIPROGRAM_NOT_AUTHORIZED` 错误）

### 参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `<project-id>` | 是 | **位置参数**，不是选项 |
| `--commit-id <commitId>` | 否 | 指定要部署的 commit ID（默认自动获取最新 commit） |
| `--wait` | 否 | 轮询等待部署完成（内置 3 秒间隔轮询，无超时限制） |
| `--format` | 否 | 输出格式，默认 `text` |

### 推荐命令模板

```bash
# 基本部署（触发后立即返回）
coze code deploy <project-id> --format json

# 等待部署完成（阻塞直到终态）
coze code deploy <project-id> --wait --format json

# 部署指定 commit
coze code deploy <project-id> --commit-id <commit-id> --format json
```

### 返回值

`--format json` 时返回结构化数据：

| 字段 | 说明 |
|------|------|
| `projectId` | 项目 ID |
| `status` | 部署状态（见下方状态枚举） |
| `domain` | 线上访问域名 |
| `deployHistoryId` | 部署记录 ID |
| `commitHash` | 部署的 commit hash |

### 部署状态枚举

| 状态 | 含义 | 是否终态 |
|------|------|:---:|
| `Pending` | 等待部署 | ❌ |
| `Running` | 部署进行中 | ❌ |
| `Succeeded` | 部署成功 | ✅ |
| `Failed` | 部署失败 | ✅ |
| `Canceled` | 部署已取消 | ✅ |
| `Interrupted` | 部署被中断 | — |

> 终态：`Succeeded`、`Failed`、`Canceled`。`--wait` 轮询会在到达终态时停止。

### 支持的项目类型

Agent、AssistantAgent、Automation、Skill、Web (GeneralWeb)、App。

> **注意**：AssistantAgent 类型走独立的部署 API，不需要 commit 记录，行为与其他类型不同。

---

## deploy status

查询项目的部署状态。**单次查询**，不加 `--wait` 时查一次就返回。

### 参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `<project-id>` | 是 | **位置参数**，不是选项 |
| `--deploy-id <deployId>` | 否 | 指定具体的部署记录 ID（默认查最新） |
| `--wait` | 否 | 轮询直到部署到达终态（内置 3 秒间隔，无超时限制） |
| `--format` | 否 | 输出格式，默认 `text` |

### 推荐命令模板

```bash
# 查询最新部署状态
coze code deploy status <project-id> --format json

# 查询指定部署记录
coze code deploy status <project-id> --deploy-id <deploy-id> --format json

# 轮询等待终态（阻塞）
coze code deploy status <project-id> --wait --format json
```

### 终态判断

- `Succeeded`：部署成功，返回线上地址（`domain` 字段）给用户
- `Failed`：部署失败，检查错误信息，可使用 `deploy fix` 自动修复
- `Canceled`：部署已取消

> 如果没有部署记录，会报 `NO_DEPLOYMENT_HISTORY` 错误，需先执行 `coze code deploy` 触发部署。

---

## deploy fix

自动修复失败的部署。获取部署日志发送给 AI 分析并自动修复代码。

### 默认行为

- **不加 `--wait`**：**非阻塞**，修复请求通过 detached 子进程后台执行，命令立即返回。可通过 `coze code message status -p <project-id>` 轮询修复进度。
- **加 `--wait`**：**阻塞**，等待 AI 分析并返回修复结果后才返回。

### 使用场景

当部署失败时（`status` 为 `Failed`），可以使用此命令自动修复：
- 代码编译错误
- 配置问题
- 依赖缺失

### 前置条件

1. **部署状态必须为 `Failed`**（其他状态会报 `DEPLOYMENT_NOT_FAILED` 错误）
2. 必须有部署记录存在
3. 部署日志不能为空

### 参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `<project-id>` | 是 | **位置参数**，不是选项 |
| `--deploy-id <deployId>` | 否 | 指定具体的部署记录 ID（默认修复最新失败的部署） |
| `--wait` | 否 | 等待 AI 修复完成后返回（默认非阻塞） |
| `--format` | 否 | 输出格式，默认 `text` |

### 推荐命令模板

```bash
# 修复最新失败的部署（非阻塞，后台执行）
coze code deploy fix <project-id> --format json

# 修复并等待 AI 完成（阻塞）
coze code deploy fix <project-id> --wait --format json

# 修复指定部署记录
coze code deploy fix <project-id> --deploy-id <deploy-id> --format json
```

### 返回值（`--wait` 时）

`--format json` 时返回：
- `deploy_history_id`: 部署记录 ID
- `status`: 修复状态（`fix_sent` 表示修复请求已发送）
- `message`: AI 分析和修复的详细信息

### 修复流程

1. **获取部署日志**：从失败的部署记录中提取日志 URL 并下载日志内容
2. **发送给 AI**：将日志作为 prompt 发送给项目的 AI（与 `message send` 走同一条通道）
3. **AI 自动修复**：AI 分析日志并尝试修改代码
4. **手动重新部署**：修复完成后需要执行 `coze code deploy` 重新部署

### 完整修复案例

```bash
# 1. 查看部署状态（确认为 Failed）
coze code deploy status <project-id> --format json

# 2. 发起修复（非阻塞）
coze code deploy fix <project-id> --format json

# 3. 轮询等待 AI 修复完成
for i in $(seq 1 60); do
  result=$(coze code message status -p <project-id> --format json)
  status=$(echo "$result" | grep -o '"status":"[^"]*"' | cut -d'"' -f4)
  if [ "$status" = "done" ] || [ "$status" = "completed" ]; then
    echo "修复完成"
    break
  fi
  sleep 30
done

# 4. 重新部署
coze code deploy <project-id> --wait --format json
```

### 注意事项

- `deploy fix` 只修改代码，不会自动触发重新部署
- 修复完成后**必须**手动执行 `coze code deploy` 重新部署
- 如果 AI 无法自动修复，需要手动检查代码或配置

---

## deploy list

查看项目的部署历史列表，支持分页。

### 参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `<project-id>` | 是 | **位置参数**，不是选项 |
| `--page-size <pageSize>` | 否 | 每页记录数（默认 10） |
| `--page-token <pageToken>` | 否 | 分页 token（用于翻页） |
| `--format` | 否 | 输出格式，默认 `text` |

### 推荐命令模板

```bash
# 查看最近部署记录
coze code deploy list <project-id> --format json

# 指定每页条数
coze code deploy list <project-id> --page-size 20 --format json
```

### 返回值

`--format json` 时返回数组，每项包含：

| 字段 | 说明 |
|------|------|
| `deploy_history_id` | 部署记录 ID |
| `project_id` | 项目 ID |
| `status` | 部署状态 |
| `commit_hash` | commit hash |
| `domain_list` | 部署域名列表 |
| `created_at` | 创建时间 |
| `can_rollback` | 是否可回滚 |
