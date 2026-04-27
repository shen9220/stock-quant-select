---
name: coze-code
version: 1.0.0
description: "Coze Coding 项目开发工作流：创建项目、发送需求消息、查询状态、预览、部署、环境变量管理、域名管理、技能管理。当用户需要创建/管理 Coze 项目、发送开发需求、部署应用、或使用 coze code * 任意命令时触发。"
metadata:
  requires:
    bins: ["coze"]
  cliHelp: "coze code --help"
---

# Coze Coding 工作流

> **前置条件：** 先阅读 [`../SKILL.md`](../SKILL.md) 完成认证和上下文配置。
> **执行前必做：** 执行任何 `code` 命令前，必须先阅读对应命令的 reference 文档，再调用命令。

## 核心概念

- **Project（项目）**：Coze Coding 的核心实体，包含 AI 编程对话历史和代码仓库。通过 `project create` 创建。
- **Message（消息）**：发送给项目的需求或指令，由 AI 异步处理。通过 `message send` 发送。
- **Deploy（部署）**：将项目部署到生产环境。通过 `deploy` 执行。
- **Preview（预览）**：获取沙盒预览链接。通过 `preview` 获取。
- **Env（环境变量）**：项目级 Secrets 管理，支持 dev/prod 环境。
- **Domain（域名）**：项目自定义域名绑定。
- **Skill（技能）**：项目关联的外部技能。

## Agent 快速执行顺序

1. **判断任务类型**
   - 新建项目 → `project create`
   - 迭代开发 → `message send` → [`message status`](references/coze-code-message.md) → [`preview`](references/coze-code-preview.md) → [`deploy`](references/coze-code-deploy.md)
   - 查询/管理 → `project list/get/delete`
   - 配置管理 → `env` / `domain` / `skill`
2. **确认前置条件**
   - 已登录? (`coze auth status`)
   - 已选组织和空间?
   - 如果是已有项目: 拿到 projectId
3. **执行命令前必读对应 reference**

## 标准 Coze Coding 工作流

### Step 0: 使用 `@` 语法引用本地文件

在 `coze code message send` 中，可直接用 `@文件路径` 引用本地文件，CLI 会自动上传并作为附件发送。

- 只支持引用**文件**（不支持目录）。
- 文件路径可以是相对路径或绝对路径。

```bash
coze code message send "请使用这张图片作为头像 @./avatar.png" -p <project-id>
coze code message send "对比 @src/old.ts 和 @src/new.ts 的差异" -p <project-id>
```

详细用法参见 [`coze-code-message.md`](references/coze-code-message.md)。

### Step 1: 创建项目

详细参数和示例参见 [`coze-code-project.md`](references/coze-code-project.md)。

```bash
coze code project create --message "创建一个聊天机器人" --type web
```

- `--message`（`-m`）和 `--type` 均为**必填参数**。
- **`--type` 只支持 `web` 和 `app` 两种类型**（不要传其他值，即使 help 文本中列出了更多类型）。
- `--wait` 选项会等待项目创建完成（包括首次 AI 消息响应）后再返回。
- 记录返回的 `projectId`。

### Step 2: 发送需求

详细参数和示例参见 [`coze-code-message.md`](references/coze-code-message.md)。

```bash
coze code message send "请优化应用配色..." \
  --project-id <project-id> \
  --format json
```

- `-p` / `--project-id` 指定项目 ID，也可通过 `COZE_PROJECT_ID` 环境变量设置。
- 支持 stdin 管道输入：`cat requirements.txt | coze code message send -p <id>`。
- `--format json` 时输出 NDJSON 事件流，每行一个 JSON 对象。**必须按行解析**。

### Step 3: 部署前先查状态

详细说明参见 [`coze-code-message.md`](references/coze-code-message.md)。

- 收到"部署"要求时，**必须**先确认 `message status` 已结束。
- 状态为 `processing` 时禁止直接部署，否则可能出现 `refs/heads/main does not exist` 等错误。

```bash
coze code message status --project-id <project-id> --format json
```

- `message status` 也支持 `--wait` 选项，会轮询直到消息处理完成。
- `message cancel` 可取消正在进行的消息。

### Step 4: 部署

详细参数和坑点参见 [`coze-code-deploy.md`](references/coze-code-deploy.md)。

- `deploy` 直接接收项目 ID 作为**位置参数**，**不要**加 `--project-id`。

```bash
coze code deploy <project-id> --format json
```

- `--wait` 会轮询等待部署完成（轮询间隔 3 秒）。
- 部署前项目必须有 commit 记录，否则会失败。

### Step 5: 查询部署结果 & 获取预览

```bash
# 查询部署状态
coze code deploy status <project-id> --format json

# 获取预览链接
coze code preview <project-id>
```

- 默认查询最新部署记录，也可通过 `--deploy-id <id>` 指定具体部署记录。
- 直到 `status` 为 `Succeeded`，再把线上地址返回给用户。
- 沙盒初始化通常需要 1-3 分钟。

## Agent 禁止行为

- 不要在 message 仍为 **processing** 时直接部署
- 不要对 deploy 使用 **`--project-id`**（它是位置参数！）
- 不要给 project create 的 **`--type`** 传 `web`/`app` 以外的值
- 不要忽略 **NDJSON 事件流的逐行解析要求**
- 不要把本地路径发给用户（必须走 file upload 返回在线链接）

## 长耗时任务处理

| 命令 | 轮询间隔 | 说明 |
|------|----------|------|
| `coze code project create --wait` | — | 等待项目创建和首次 AI 响应完成 |
| `coze code deploy --wait` | 3 秒 | 等待部署到达终态 |
| `coze code deploy status --wait` | 3 秒 | 等待部署到达终态 |
| `coze code message status --wait` | — | 轮询直到消息处理完成 |

优先使用 `--wait` 让 CLI 自动轮询。到达终态后，必须主动把最终结果反馈给用户。

## 意图 → 命令索引

| 意图 | 推荐命令 | 备注 |
|------|---------|------|
| 创建新项目 | `coze code project create` | 仅支持 web/app 类型 |
| 列出/查看项目 | `coze code project list/get` | 支持按类型/名称筛选 |
| 删除项目 | `coze code project delete` | 不可逆操作 |
| 发送开发需求 | `coze code message send` | 支持 @文件引用、stdin 管道 |
| 查询任务状态 | `coze code message status` | 支持 --wait 轮询 |
| 取消任务 | `coze code message cancel` | |
| 部署到生产 | `coze code deploy <id>` | **位置参数**, 不用 --project-id |
| 查询部署状态 | `coze code deploy status <id>` | |
| 获取预览链接 | `coze code preview <id>` | 沙盒初始化需 1-3 分钟 |
| 管理环境变量 | `coze code env set/list/delete` | 支持 --env dev\|prod |
| 管理自定义域名 | `coze code domain add/list/remove` | |
| 管理技能 | `coze code skill add/list/remove` | |

## 命令分组

> **执行前必做：** 从下表定位到命令后，务必先阅读对应命令的 reference 文档，再调用命令。

| 命令分组 | 说明 | Reference |
|----------|------|-----------|
| [`project commands`](references/coze-code-project.md) | `create / list / get / delete` | 项目全生命周期管理 |
| [`message commands`](references/coze-code-message.md) | `send / status / cancel` | 需求发送与状态追踪 |
| [`deploy commands`](references/coze-code-deploy.md) | `deploy / status` | 部署与状态查询 |
| [`preview`](references/coze-code-preview.md) | `preview` | 沙盒预览链接 |
| [`env commands`](references/coze-code-env.md) | `set / list / delete` | 环境变量(Secrets)管理 |
| [`domain commands`](references/coze-code-domain.md) | `add / list / remove` | 自定义域名管理 |
| [`skill commands`](references/coze-code-skill.md) | `add / list / remove` | 技能管理 |

## 常见错误速查（Code 专用）

### 错误 3：把本地路径发给用户

- 问题：`/tmp/...` 只能本机访问，用户无法直接打开。
- 修正：始终执行 `coze file upload`，把上传后的在线 `URL` 发给用户。

### 错误 4：误把 `--output-path` 当成最终文件名

- 问题：CLI 帮助说明其语义更接近保存目录。
- 修正：优先传入目录路径，并在生成后从目录中定位实际文件名。（此错误也涉及 generate 场景）

### 错误 6：message 仍在 processing 时直接部署

- 问题：项目还没有可部署的代码或 commit，导致部署失败。
- 修正：始终先执行 `coze code message status -p <id> --format json` 确认状态为完成后再部署。

### 错误 7：deploy 命令使用 --project-id 参数

- 问题：`deploy` 命令的项目 ID 是**位置参数**，不是 `--project-id` 选项。
- 修正：使用 `coze code deploy <project-id>` 而非 `coze code deploy --project-id <id>`。

### 错误 8：project create --type 传了不支持的类型

- 问题：虽然 help 文本中列出了 agent/workflow/skill 等类型，但 create 命令实际只支持 `web` 和 `app`。
- 修正：`--type` 只使用 `web` 或 `app`。
