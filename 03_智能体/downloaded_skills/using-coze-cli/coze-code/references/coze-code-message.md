# code message commands

> **前置条件：** 先阅读 [`../../SKILL.md`](../../SKILL.md) 了解认证、全局参数和安全规则。

消息交互相关命令索引。涵盖向项目发送开发需求、查询任务状态、取消进行中的任务、查看对话历史。

## 命令导航

| 文档 | 命令 | 说明 |
|------|------|------|
| 本文档 | `coze code message send` | 发送需求（支持@文件引用、--stdin 附加上下文） |
| 本文档 | `coze code message status` | 查询任务状态（单次查询，需脚本轮询） |
| 本文档 | `coze code message cancel` | 取消正在进行的任务 |
| 本文档 | `coze code message history` | 查看项目对话历史 |

> 共享参数：`-p` / `--project-id` 指定项目 ID（或通过环境变量 `COZE_PROJECT_ID` 设置）。

---

## message send

向项目发送开发需求或指令，由 AI 异步处理。

### 核心警告

**部署前必须确认 message status 已结束！** 状态为 `processing` 时禁止直接部署，否则会出现 `refs/heads/main does not exist` 等错误。

### 参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `<message>` | 条件必填* | 需求文本 |
| `-p` / `--project-id` | 是 | 项目 ID |
| `--stdin` | 否 | 从 stdin 管道读取附加上下文 |
| `--wait` | 否 | 等待 AI 响应完成后再返回（默认后台发送） |
| `--format` | 否 | 输出格式；`json` 时输出 NDJSON 事件流 |

*\* `<message>` 必填，`--stdin` 为可选附加上下文*

### @语法引用本地文件

在 message 文本中可直接用 `@文件路径` 引用本地文件，CLI 会自动上传并作为附件发送：

```bash
# 引用单张图片
coze code message send "请使用这张图片作为头像 @./avatar.png" -p <project-id>

# 对比两个文件
coze code message send "对比 @src/old.ts 和 @src/new.ts 的差异" -p <project-id>
```

- 只支持引用**文件**（不支持目录）
- 文件路径可以是相对路径或绝对路径

### stdin 管道输入（附加上下文）

使用 `--stdin` 显式声明从管道读取附加上下文，与 `<message>` 拼接后发送：

```bash
# 从文件管道输入作为上下文
cat error.log | coze code message send "分析这个错误" --stdin -p <project-id>

# 从其他命令管道输入
echo "修复登录页面的样式问题" | coze code message send "请查看以下日志" --stdin -p <project-id>
```

- **不传 `--stdin` 时 stdin 会被忽略**，不会阻塞等待
- 管道内容会追加在 `<message>` 之后，格式为 `消息\n\n上下文`
- 适合将日志、错误输出、代码片段等作为上下文附加到需求中

### NDJSON 解析规则（`--format json` 时）

当使用 `--format json` 时，输出为 NDJSON 事件流：

- 每行是一个独立的 JSON 对象
- 必须按行逐行解析，不能整段 `JSON.parse()`
- 关键字段：
  - `content`: 响应内容片段
  - `role`: 角色（`assistant` / `user`）
  - `finish`: 是否为最终结果（`true` / `false`）
  - `type`: 事件类型
- **找 `finish: true` 的行来获取最终结果**

### 推荐命令模板

```bash
# 基本发送
coze code message send "请优化应用配色方案" --project-id <project-id> --format json

# 带 @文件引用
coze code message send "分析 @src/app.ts 的代码质量" -p <project-id> --format json

# stdin 管道输入 + json 格式
cat spec.md | coze code message send "请按此规格实现" --stdin -p <project_id> --format json
```

### text 模式 vs json 模式

- **text 模式**（默认）：直接输出人类可读的 AI 响应内容，适合展示给用户
- **json 模式**：输出结构化 NDJSON 事件流，适合 Agent 提取和处理数据

---

## message status

查询项目中最近一次消息的处理状态。

### 参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `--project-id` / `-p` | 是 | 项目 ID |
| `--format` | 否 | 输出格式，默认 `text` |

> `message status` 是**单次查询**，不会自动轮询。如需等待任务完成，需在脚本中循环调用（见 `SKILL.md` "长时间命令处理"）。

### 推荐命令模板

```bash
# 查询状态
coze code message status --project-id <project-id> --format json

# 脚本轮询等待完成
for i in $(seq 1 60); do
  result=$(coze code message status -p <project-id> --format json)
  status=$(echo "$result" | grep -o '"status":"[^"]*"' | cut -d'"' -f4)
  if [ "$status" = "done" ] || [ "$status" = "completed" ]; then
    echo "$result"
    break
  fi
  sleep 30
done
```

### 状态含义

| 状态 | 含义 | 可执行的操作 |
|------|------|-------------|
| `processing` | 正在处理中 | 禁止部署，必须等待 |
| `completed` | 处理完成 | 可以部署 |
| `failed` | 处理失败 | 检查错误信息后重试 |

---

## message cancel

取消当前正在进行的消息任务。

### 参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `--project-id` / `-p` | 是 | 项目 ID |

### 推荐命令模板

```bash
coze code message cancel --project-id <project-id>
```

---

## message history

查看项目的对话历史，默认返回最新 10 条对话记录。支持通过游标分页加载更旧或更新的消息。

### 参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `--project-id` / `-p` | 是 | 项目 ID |
| `--after <msg_id>` | 否* | 游标，获取该消息之后（更新）的消息。新消息 index 更小 |
| `--before <msg_id>` | 否* | 游标，获取该消息之前（更旧）的消息。旧消息 index 更大 |
| `--format` | 否 | 输出格式，默认 `text` |

*\* `--after` 和 `--before` 不能同时使用*

### 推荐命令模板

```bash
# 查看最新 10 条对话（人类可读格式）
coze code message history -p <project-id>

# JSON 格式输出（适合 Agent 解析，含游标信息）
coze code message history -p <project-id> --format json

# 使用 before 游标加载更旧的消息
coze code message history -p <project-id> --before <msg_id_from_cursor>

# 使用 after 游标加载更新的消息
coze code message history -p <project-id> --after <msg_id_from_cursor>
```

### 分页机制

- 不传游标时，默认返回**最新 10 条**记录，方向为 `loadPrev`（向更旧方向翻页）
- 返回结果中包含游标信息，用于下一页请求：
  - `after`：当前结果中**最新**消息的 ID，用作 `--after` 可获取更新的消息
  - `before`：当前结果中**最旧**消息的 ID，用作 `--before` 可获取更旧的消息
  - `has_more`：是否还有更多消息
  - `loadDirection`：本次请求的加载方向（`loadNext` / `loadPrev`）

### 输出格式

**text 模式**（默认）：按条目展示问答记录，末尾附带游标信息

```
--- #1 ---
Q: 添加暗黑模式支持
A: 已完成暗黑模式的实现...

--- #2 ---
Q: 修复登录页面的样式问题
A: 已修复，调整了 CSS 布局...

[cursor] after=msg_abc, before=msg_xyz, has_more=true, loadDirection=loadPrev
```

> `#N` 为当前页面的条目序号，不代表全局轮次序号。

**json 模式**：输出结构化对象，包含 `histories` 数组和 `cursor` 游标对象

```json
{
  "histories": [
    { "userMessage": "添加暗黑模式支持", "answer": "已完成暗黑模式的实现..." },
    { "userMessage": "修复登录页面的样式问题", "answer": "已修复，调整了 CSS 布局..." }
  ],
  "cursor": {
    "after": "msg_abc",
    "before": "msg_xyz",
    "has_more": true,
    "loadDirection": "loadPrev"
  }
}
```

> 仅返回有 AI 回答的记录，按时间正序排列（最早的在前）。如果没有对话历史，text 模式输出 `No conversation history found.`。
