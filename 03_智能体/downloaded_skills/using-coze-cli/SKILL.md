---
name: using-coze-cli
version: 2.0.0
description: "Coze CLI 共享基础：安装、认证登录(OAuth)、组织与空间切换、配置管理、全局执行原则、安全规则、错误码处理、代理配置。当用户首次使用 coze-cli、需要登录授权、遇到权限不足、切换组织/空间、或任何操作前的基础检查时触发。"
metadata:
  requires:
    bins: ["coze"]
  cliHelp: "coze --help"
---

# Coze CLI 共享规则

本技能指导你如何通过 `coze` 命令完成基础配置和上下文管理，以及有哪些通用注意事项。

## 适用场景

- 用户明确要求"用 Coze CLI"或"用 `coze` 命令"完成任务。
- 需要使用 `coze generate` 生成音频、图片或视频。
- 需要把本地生成文件上传后，将在线可访问链接返回给用户。
- 需要创建 Coze 项目、发送 `message`、查询状态、部署、获取 preview。

## 必须遵守的执行原则

### 1. 用户明确指定 Coze CLI 时，禁止私自改用别的能力

- 如果用户明确要求"用 Coze CLI 生成语音/图片/视频"，就必须优先把 Coze CLI 路径跑通。
- **禁止**在未充分排查 Coze CLI 正确用法前，擅自改用 OpenClaw、自带 TTS、第三方生成接口或 Web 页面手工下载流程。
- 只有在 Coze CLI 明确报错、能力缺失，且已经向用户解释并获得同意时，才允许退回到其他方案。

### 2. 优先使用 `--format json`

- 需要结构化解析结果时，优先增加 `--format json`。
- 注意：`coze code message send --format json` 输出 NDJSON 事件流，必须按行解析，不能直接整段 `JSON.parse()`。
- NDJSON 每行是一个独立 JSON 对象，格式为 `{"content": "...", "role": "...", "finish": true/false, "type": "..."}`，需要找 `finish: true` 的行来获取最终结果。
- `--format` 默认值为 `text`，text 模式下输出人类可读的格式化文本。

### 3. 对用户交付文件时，必须返回在线链接，不要返回本地路径

- 本地路径如 `/tmp/foo.mp3`、`./output/image.png`、相对路径或沙箱路径，对用户不可直接访问。
- 生成文件后，**必须**继续执行 `coze file upload <path>`。
- 最终返回给用户的应是上传后的在线 `URL`，而不是本地文件路径。

### 4. 不确定命令用法时，使用 `--help` 或 `--man` 查看

- 任何命令都支持 `--help` 查看简要帮助，`--man` 查看完整手册（含参数说明、示例、错误码）。
- 当不确定某个命令的参数、选项或用法时，**先执行 `<command> --help` 或 `<command> --man`** 获取准确信息，不要凭猜测拼命令。
- 示例：
  ```bash
  coze code project create --help
  coze code deploy --man
  coze generate video create --help
  ```

## 安装 CLI

```bash
npm install -g @coze/cli
```

如果找不到包，可执行：

```bash
npm config set registry https://registry.npmjs.org/
```

## 登录与身份验证

#### 避坑：OAuth 授权超时与阻塞问题

- `coze auth login` 的 OAuth 激活链接和设备码通常输出在 `stderr`，需要合并捕获。
- **致命问题**：设备码有 **10 分钟有效期**，且命令会前台阻塞等待。如果 Agent 同步等待该命令执行，可能会卡死流程，且用户往往来不及操作。
- **推荐方案：后台执行 + 轮询获取链接 + 进程自记录退出码**：
  ```bash
  # 0. 清理上次残留的临时文件（避免误读旧状态）
  rm -f /tmp/coze-login.log /tmp/coze-login.pid /tmp/coze-auth-exit-code.txt

  # 1. 后台启动登录命令，让进程自己记录退出码
  nohup bash -c '
    coze auth login
    EC=$?
    echo $EC > /tmp/coze-auth-exit-code.txt
    exit $EC
  ' > /tmp/coze-login.log 2>&1 &
  echo $! > /tmp/coze-login.pid

  # 2. 轮询等待授权链接出现（最多等待 30 秒）
  for i in $(seq 1 15); do
    if grep -q "user_code=" /tmp/coze-login.log 2>/dev/null; then
      break
    fi
    sleep 2
  done

  # 2.1 检查是否成功获取到链接
  if ! grep -q "user_code=" /tmp/coze-login.log 2>/dev/null; then
    echo "获取授权链接超时，请检查网络连接或重试"
    cat /tmp/coze-login.log 2>/dev/null
  fi

  # 3. 提取并返回授权链接
  grep "user_code=" /tmp/coze-login.log | grep -oE 'https://[^ ]+'
  ```
- **为什么让进程自己记录退出码**：
  - `wait` 命令只能在启动进程的 shell 中使用，无法在后台子 shell 中跨进程等待。
  - 通过 `bash -c '...; EC=$?; echo $EC > file'` 让进程自己捕获并记录退出码。
  - 退出码精确反映**本次**授权结果，不受上次登录状态影响：
    - 退出码 `0`：用户完成授权
    - 非零退出码：授权失败（常见值：`1` 一般错误、`2` 认证失败、`6` 网络错误、`8` 超时）
- **授权流程（必须遵守）**：
  1. **先检查授权状态**：在发起任何需要认证的操作前，必须先执行 `coze auth status` 确认是否已完成授权。
  2. **若未授权，后台执行并轮询获取链接**：
     - 使用上述后台执行 + 轮询方案获取授权链接。
     - 一旦获取到链接，**立即返回给用户**。
     - 同时启动后台轮询任务自动检查授权状态。
  3. **检查退出码确认授权结果**：
     - 进程结束后，退出码会自动写入 `/tmp/coze-auth-exit-code.txt`。
     - 通过轮询检查该文件确认授权结果：
       ```bash
       # 轮询检查退出码文件（最长等待 10 分钟）
       for i in $(seq 1 60); do
         if [ -f /tmp/coze-auth-exit-code.txt ]; then
           exit_code=$(cat /tmp/coze-auth-exit-code.txt)
           if [ "$exit_code" = "0" ]; then
             echo "授权成功"
           else
             echo "授权失败（退出码: $exit_code）"
           fi
           break
         fi
         sleep 10
       done

       # 兜底：如果轮询结束仍未获得退出码，说明进程异常
       if [ ! -f /tmp/coze-auth-exit-code.txt ]; then
         echo "授权超时或进程异常退出"
         # 检查后台进程是否仍在运行
         if [ -f /tmp/coze-login.pid ] && kill -0 "$(cat /tmp/coze-login.pid)" 2>/dev/null; then
           echo "登录进程仍在运行，尝试终止"
           kill "$(cat /tmp/coze-login.pid)" 2>/dev/null
         fi
       fi
       ```
  4. **授权完成后，再启动后续任务**：
     - 授权是所有后续操作的前置条件。在确认已登录之前，**不要**创建任何后台任务或发起项目创建/部署等后续操作。
- 如果提示 `[Auth] No API token found`，先执行：

```bash
coze auth status
```

### 检查登录状态

- `coze auth status` 返回当前凭证状态。`--format json` 输出结构化数据，包含 `status`（`active`/`expired`/`not_logged_in`）、`user_id`、`token_expires_at`。
- Token 过期后 CLI 会在执行命令时自动尝试刷新，无需手动重新登录。

### 登出

- `coze auth logout` 清除本地凭证。

## 长时间命令处理

### 避坑：长耗时命令超时问题

- 部分命令（AI 生成、部署、OAuth 登录等）可能耗时数分钟甚至更久，超过沙箱超时限制（通常 600 秒）会导致命令被强制中断。
- 不同命令的阻塞行为不同，需要区分处理。

### 长耗时命令速查表

| 长耗时命令 | 默认行为 | `--wait` | 对应的轮询命令 |
|-----------|---------|:---:|--------------|
| `coze auth login` | 同步阻塞（等待用户浏览器授权） | — | 轮询退出码文件（见上文"登录与身份验证"） |
| `coze code message send` | 同步阻塞（等待 AI 回答流完成） | — | `coze code message status -p <project_id>` |
| `coze code project create` | **非阻塞**（后台子进程执行 AI 生成） | ✅ 加后变阻塞 | `coze code message status -p <project_id>` |
| `coze code deploy <id>` | 触发部署后立即返回 | ✅ 加后内置轮询(3s) | `coze code deploy status <project_id>` |
| `coze code deploy fix <id>` | **非阻塞**（后台子进程执行修复） | ✅ 加后变阻塞 | `coze code message status -p <project_id>` |
| `coze generate video create` | 提交任务后立即返回 taskId | ✅ 加后内置轮询(2s/5min超时) | `coze generate video status <task_id>` |
| `coze generate audio` | 同步阻塞（SSE 流式返回） | — | — |
| `coze generate image` | 同步阻塞（单次 API 调用） | — | — |

> `coze code message status` 和 `coze code deploy status` 本身都是**单次查询**，不会自动轮询。需要在脚本中循环调用。
> `coze code message cancel -p <project_id>` 可取消进行中的消息任务。

### 场景一：命令支持非阻塞——不加 `--wait` 直接使用

`project create`、`deploy fix` 默认就是非阻塞的（通过 detached 子进程后台执行），`deploy`、`generate video create` 默认提交后立即返回。这些命令返回后，通过上表中对应的轮询命令检查结果即可。

```bash
# 示例：项目创建 + 轮询（默认非阻塞）
coze code project create --type web --message "需求描述"
# 命令立即返回 project_id，然后轮询消息状态
for i in $(seq 1 60); do
  result=$(coze code message status -p <project_id> --format json)
  status=$(echo "$result" | grep -o '"status":"[^"]*"' | cut -d'"' -f4)
  echo "[$(date +%H:%M:%S)] 状态: $status"
  if [ "$status" = "done" ] || [ "$status" = "completed" ]; then
    echo "处理完成！"
    echo "$result"
    break
  elif [ "$status" = "failed" ]; then
    echo "处理失败"
    break
  fi
  sleep 30
done
```

> **注意**：这些命令加 `--wait` 后会变成同步阻塞，应按场景二处理。推荐**不加 `--wait`**，改用对应轮询命令手动轮询。

### 场景二：命令处于阻塞状态——需要 `nohup` 后台执行

以下情况命令会同步阻塞，如果预计耗时较长可能超出沙箱超时限制，必须通过 `nohup` 后台执行，再用对应轮询命令检查结果：

- **本身始终阻塞的命令**：`message send`、`generate audio`、`generate image`、`auth login` 没有 `--wait` 选项，始终同步阻塞。
- **加了 `--wait` 的命令**：`project create --wait`、`deploy --wait`、`deploy fix --wait`、`generate video create --wait` 加上 `--wait` 后也会变成同步阻塞。

```bash
# 示例：消息发送后台执行 + 轮询
rm -f /tmp/coze-message.log /tmp/coze-message-exit-code.txt

nohup bash -c '
  coze code message send "需求描述" -p <project_id>
  EC=$?
  echo $EC > /tmp/coze-message-exit-code.txt
  exit $EC
' > /tmp/coze-message.log 2>&1 &

# 通过 message status 轮询进度（对应轮询命令见上表）
for i in $(seq 1 60); do
  result=$(coze code message status -p <project_id> --format json 2>/dev/null)
  status=$(echo "$result" | grep -o '"status":"[^"]*"' | cut -d'"' -f4)
  echo "[$(date +%H:%M:%S)] 状态: $status"
  if [ "$status" = "done" ] || [ "$status" = "completed" ]; then
    echo "处理完成！"
    echo "$result"
    break
  elif [ "$status" = "failed" ]; then
    echo "处理失败"
    break
  fi
  sleep 30
done

# 兜底：轮询结束仍未完成时检查后台进程退出码
if [ "$status" != "done" ] && [ "$status" != "completed" ]; then
  if [ -f /tmp/coze-message-exit-code.txt ]; then
    exit_code=$(cat /tmp/coze-message-exit-code.txt)
    echo "后台进程已退出，退出码: $exit_code"
  else
    echo "任务可能仍在进行中，请稍后再次执行轮询命令检查"
  fi
fi
```

**核心原则**：任何可能长时间阻塞的命令，都应后台执行或使用其内置的非阻塞模式，然后通过对应的轮询命令检查结果，避免沙箱超时中断。

## 组织与空间上下文

### 核心行为

- 切换组织时会**自动清空 Space ID**，需要重新选择工作空间。
- 指定不存在的组织 ID 会**报错且不修改配置**。
- 省略 org_id 可切换到个人账户模式（需要个人账户可用）。
- CLI 有自动上下文补全机制：如果未配置组织或空间，首次执行需要上下文的命令时，CLI 会尝试自动选择第一个可用的组织/空间，但**自动选择的结果可能不是用户期望的**，建议始终显式设置。

### 遇到 `No permission` 的修正顺序

1. `coze config list` — 查看当前配置
2. `coze organization list` — 查看可用组织
3. `coze organization use <org_id>` — 切换到正确的组织（或 `coze organization use` 切换到个人账户）
4. `coze space list` — 查看当前组织下的空间
5. `coze space use <space_id>` — 切换到正确的空间

### 环境变量覆盖

- `COZE_ORG_ID`、`COZE_SPACE_ID` 环境变量可临时覆盖配置，无需修改持久化配置。
- `--org-id`、`--space-id` 全局参数也可以临时覆盖。

## 配置管理概要

- `coze config get <keys...>` — 获取配置值
- `coze config set <key> <value>` — 设置配置值
- `coze config delete <keys...>` — 删除配置值
- `coze config list` — 列出所有配置

详细用法参见各业务模块 reference 文档中的配置相关章节。

## 安全规则

- **禁止输出密钥**（token、API Key 等）到终端明文。
- **写入/删除操作前必须确认用户意图**。
- 用 `--dry-run` 预览危险请求（如适用）。

## 代理配置

如果需要通过代理访问 Coze API：

```bash
export HTTPS_PROXY=http://your-proxy:8080
export HTTP_PROXY=http://your-proxy:8080
```

CLI 会自动识别并使用配置的代理。

## 升级与补全

### 升级 CLI

```bash
coze upgrade          # 升级到最新版本
coze upgrade --force  # 强制升级
```

### Shell 自动补全

```bash
coze completion --setup    # 安装补全脚本
coze completion --cleanup   # 移除补全脚本
```

支持的 Shell：bash, zsh, fish, powershell。

## 退出码参考

Agent 在判断命令执行结果时，应根据退出码判断：

| 退出码 | 名称 | 含义 |
|--------|------|------|
| `0` | `SUCCESS` | 成功 |
| `1` | `GENERAL_ERROR` | 一般错误 |
| `2` | `AUTH_FAILED` | 认证失败，需要重新登录 |
| `3` | `RESOURCE_NOT_FOUND` | 资源未找到（如项目 ID 不存在） |
| `4` | `INVALID_ARGUMENT` | 参数无效 |
| `5` | `PERMISSION_DENIED` | 权限不足，检查组织/空间上下文 |
| `6` | `NETWORK_ERROR` | 网络错误，检查代理或网络连接 |
| `7` | `SERVER_ERROR` | 服务端错误，稍后重试 |
| `8` | `TIMEOUT` | 操作超时 |
| `9` | `QUOTA_EXCEEDED` | 配额超限 |
| `10` | `CONFLICT` | 资源冲突 |

### 退出码处理建议

- 退出码 `2`：执行 `coze auth login` 重新登录。
- 退出码 `5`：按"组织与空间上下文"章节的修正顺序排查。
- 退出码 `6`/`7`：可重试一次。
- 退出码 `4`：检查参数拼写和格式。

## 业务模块导航

| 模块 | 触发场景 | 入口 |
|------|---------|------|
| [`coze-code`](./coze-code/MODULE.md) | 创建项目、发送需求、部署应用、环境变量/域名/技能管理 | `coze code *` |
| [`coze-generate`](./coze-generate/MODULE.md) | 生成图片、语音合成(TTS)、视频生成 | `coze generate *` |
| [`coze-file`](./coze-file/MODULE.md) | 上传本地文件获取在线访问地址 | `coze file upload` |

## 典型错误速查（基础类）

### 错误 1：用户要求用 Coze CLI，但 agent 改用别的 TTS

- 问题：偏离用户指令，且掩盖了 Coze CLI 实际可用路径。
- 修正：先补上 `--output-path`，完整跑完 Coze CLI 生成与上传闭环。

### 错误 2：OAuth 登录卡死或用户来不及授权

- 问题：直接后台执行 `coze auth login` 后继续发起后续任务，导致后续任务因未授权而全部失败；或前台阻塞等待导致 Agent 卡死；或使用 `wait` 在后台子 shell 中跨进程等待导致失败。
- 修正：
  1. **用 `bash -c` 包装命令**，让进程自己记录退出码到文件。
  2. **轮询获取授权链接**，拿到后立即返回给用户。
  3. **轮询检查退出码文件**，判断本次授权结果（0=成功，非0=失败）。

### 错误 3：未使用 `--format json` 导致输出无法解析

- 问题：Agent 需要从输出中提取 projectId、taskId 等结构化数据，但默认 text 格式不方便解析。
- 修正：Agent 场景下始终加 `--format json`，然后按 JSON 解析输出。

### 错误 4：忽视退出码直接认为命令成功

- 问题：命令可能返回非零退出码表示失败，但 Agent 只看了 stdout 输出。
- 修正：始终检查命令退出码，非零时根据退出码参考表排查问题。
