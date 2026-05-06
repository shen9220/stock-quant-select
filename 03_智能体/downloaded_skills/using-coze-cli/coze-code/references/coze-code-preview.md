# code preview

> **前置条件：** 先阅读 [`../../SKILL.md`](../../SKILL.md) 了解认证、全局参数和安全规则。

获取项目的沙盒预览 URL。

## 命令概述

```bash
coze code preview <project-id>
```

### 参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `<project-id>` | 是 | 项目 ID（位置参数） |

### 推荐命令模板

```bash
coze code preview <project-id>
```

### 返回值

返回沙盒预览的可访问 URL。

### 注意事项

- 沙盒初始化通常需要 **1-3 分钟**，如果刚创建完项目就调用 preview，可能还未就绪
- 支持的项目类型：AssistantAgent, GeneralWeb, Skill
- Preview 不需要项目已部署，只需项目有代码即可
