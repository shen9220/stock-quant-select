---
name: github
description: |
  GitHub 代码仓库管理工具。支持代码提交、Issue 管理、PR 创建、
  代码审查、Release 发布等完整开发工作流。
author: OpenClaw Community
version: 1.0.0
tags: ["GitHub", "Git", "代码管理", "版本控制", "开发工具"]
---

# GitHub Skill

## 功能概述

完整的 GitHub 集成能力：
- 代码仓库操作（clone/pull/push）
- Issue 和 PR 管理
- 代码审查和评论
- Release 和标签管理
- Actions 工作流监控
- 代码搜索和分析

## 核心功能

### 1. 仓库操作
```bash
# 克隆仓库
git clone <repo-url>

# 提交更改
git add .
git commit -m "message"
git push origin main

# 拉取更新
git pull origin main
```

### 2. Issue 管理
- 创建/编辑/关闭 Issue
- 添加标签和里程碑
- 分配负责人
- 评论和讨论

### 3. Pull Request
- 创建 PR
- 代码审查
- 合并分支
- 解决冲突

### 4. 代码分析
- 查看代码变更
- 搜索代码片段
- 分析提交历史
- 生成代码统计

## 使用场景

- 代码版本管理
- 团队协作开发
- 项目进度跟踪
- 自动化部署
- 代码审查流程

## 认证方式

- GitHub Token（推荐）
- SSH Key
- OAuth App

## 安全提示

- 使用 Fine-grained Token，最小权限原则
- 不要将 Token 提交到代码中
- 定期轮换 Token
- 敏感操作需二次确认

---

*OpenClaw Community | 2026*