# code skill commands

> **前置条件：** 先阅读 [`../../SKILL.md`](../../SKILL.md) 了解认证、全局参数和安全规则。

技能管理命令。用于管理项目关联的外部技能（Skills）。

## 命令概览

| 命令 | 说明 |
|------|------|
| `coze code skill list -p <id>` | 列出项目已关联的技能 |
| `coze code skill add <skill-id> -p <id>` | 为项目添加技能 |
| `coze code skill remove <skill-id> -p <id>` | 从项目移除技能 |

### 共享参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `-p` / `--project-id` | 是 | 项目 ID |

---

## skill list

列出项目当前关联的所有技能。

### 推荐命令模板

```bash
coze code skill list -p <project-id>
```

---

## skill add

为项目添加外部技能。

### 参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `<skill-id>` | 是 | 技能 ID（位置参数） |
| `-p` / `--project-id` | 是 | 项目 ID |

### 推荐命令模板

```bash
coze code skill add <skill-id> -p <project-id>
```

---

## skill remove

从项目移除已关联的技能。

### 参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `<skill-id>` | 是 | 技能 ID（位置参数） |
| `-p` / `--project-id` | 是 | 项目 ID |

### 推荐命令模板

```bash
coze code skill remove <skill-id> -p <project-id>
```
