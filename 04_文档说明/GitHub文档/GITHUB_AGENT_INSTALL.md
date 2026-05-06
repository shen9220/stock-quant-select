# GitHub Agent 技能包安装完成

## 📋 基本信息

- **技能ID**: 53fd8a1c-a147-4ec8-9611-169e7800c3a9
- **技能名称**: github
- **版本**: 1.0.0
- **下载时间**: 2024-04-27
- **下载位置**: `/workspace/projects/stock-quant-select/downloaded_skills/github/`

## 📖 技能描述

GitHub 代码仓库管理工具。支持代码提交、Issue 管理、PR 创建、代码审查、Release 发布等完整开发工作流。

## ✅ 安装检查

### 核心依赖

| 工具 | 状态 | 版本 | 说明 |
|------|------|------|------|
| Git | ✅ 已安装 | 2.34.1 | 核心版本控制工具 |
| GitHub CLI | ❌ 未安装 | - | 可选工具 |

### 安装说明

Git 已预装在系统中，无需额外安装。

GitHub CLI（gh）是可选工具，用于增强 GitHub 操作体验。如需安装：

**Linux**:
```bash
curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null
sudo apt update
sudo apt install gh
```

**Mac**:
```bash
brew install gh
```

**Windows**:
```bash
winget install --id GitHub.cli
```

## 📁 文件结构

```
downloaded_skills/
├── github/                          # GitHub 技能包 ⭐
│   └── SKILL.md                     # 主文档
└── using-coze-cli/                  # Coze CLI 技能包
    ├── SKILL.md
    ├── coze-code/
    ├── coze-file/
    └── coze-generate/
```

## 🎯 核心功能

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

### 5. Actions 工作流

- 监控工作流运行
- 查看日志
- 触发工作流

### 6. Release 管理

- 创建 Release
- 上传 Assets
- 管理标签

## 💡 在量化项目中的应用

### 应用场景

#### 1. 版本控制量化策略代码
```bash
# 初始化仓库
git init

# 添加所有文件
git add .

# 提交量化模型
git commit -m "feat: 添加首板战法量化模型"

# 推送到远程仓库
git remote add origin https://github.com/username/stock-quant.git
git push -u origin main
```

#### 2. 管理量化策略迭代
```bash
# 创建开发分支
git checkout -b feature/new-strategy

# 开发新策略
# ... 编写代码 ...

# 提交更改
git add .
git commit -m "feat: 添加三阴战法模型"

# 推送到远程
git push origin feature/new-strategy

# 创建 Pull Request
# 通过 GitHub 网页或 gh cli 创建
```

#### 3. 备份量化结果
```bash
# 每日备份量化结果
git add results/
git commit -m "backup: 2024-04-27 量化结果"
git push origin main
```

#### 4. 团队协作
```bash
# 拉取最新更改
git pull origin main

# 解决冲突
# ... 解决代码冲突 ...

# 提交合并
git add .
git commit -m "resolve: 合并冲突"
git push origin main
```

### 实际使用示例

#### 示例1：提交量化代码到GitHub

```bash
cd /workspace/projects/stock-quant-select

# 初始化 Git 仓库（如果还没有）
git init

# 添加文件
git add SKILL.md scripts/ 首板战法/ 涨停三阴法/

# 提交
git commit -m "feat: 添加多个量化策略模型"

# 连接远程仓库（替换为你的仓库地址）
git remote add origin https://github.com/your-username/stock-quant-select.git

# 推送
git push -u origin main
```

#### 示例2：创建策略分支

```bash
# 创建新策略分支
git checkout -b strategy/pop3-improvement

# 修改代码
# ... 编辑 pop3.py ...

# 提交更改
git add 涨停三阴法/pop3.py
git commit -m "improve: 优化涨停三阴法模型"

# 推送分支
git push -u origin strategy/pop3-improvement

# 创建 Pull Request（需要安装 gh cli）
gh pr create --title "优化涨停三阴法模型" --body "改进了..."
```

#### 示例3：管理量化结果

```bash
# 添加结果目录到 .gitignore
echo "results/*.json" >> .gitignore
echo "*.log" >> .gitignore

# 提交重要结果
git add results/2024-04-27-summary.json
git commit -m "docs: 添加2024-04-27量化结果摘要"
git push origin main
```

## 🔐 认证配置

### 方法1：GitHub Token（推荐）

1. 生成 GitHub Personal Access Token:
   - 访问 https://github.com/settings/tokens
   - 点击 "Generate new token (classic)"
   - 选择需要的权限：
     - `repo` - 完整仓库访问权限
     - `workflow` - 工作流权限
   - 生成并复制 Token

2. 配置 Git:
```bash
# 使用 Token 认证
git config --global credential.helper store
# 推送时会提示输入用户名和 Token
```

### 方法2：SSH Key

```bash
# 生成 SSH Key
ssh-keygen -t ed25519 -C "your_email@example.com"

# 启动 ssh-agent
eval "$(ssh-agent -s)"

# 添加 SSH Key
ssh-add ~/.ssh/id_ed25519

# 复制公钥到 GitHub
cat ~/.ssh/id_ed25519.pub

# 在 GitHub 设置中添加 SSH Key
```

### 方法3：GitHub CLI

```bash
# 安装 gh cli 后，执行认证
gh auth login

# 选择 GitHub.com
# 选择 HTTPS 或 SSH
# 粘贴 Token 或使用浏览器认证
```

## 📝 常用命令速查

### 仓库操作
```bash
git clone <url>              # 克隆仓库
git init                     # 初始化仓库
git status                   # 查看状态
git add <file>               # 添加文件
git commit -m "msg"          # 提交
git push                     # 推送
git pull                     # 拉取
```

### 分支管理
```bash
git branch                   # 查看分支
git branch <name>            # 创建分支
git checkout <name>          # 切换分支
git checkout -b <name>       # 创建并切换
git merge <name>             # 合并分支
git branch -d <name>         # 删除分支
```

### 远程操作
```bash
git remote add <name> <url>  # 添加远程仓库
git remote -v                # 查看远程仓库
git remote remove <name>     # 删除远程仓库
```

### 查看历史
```bash
git log                      # 查看提交历史
git diff                     # 查看未提交的更改
git show <commit>            # 查看特定提交
```

## ⚠️ 安全提示

1. **Token 安全**:
   - 使用 Fine-grained Token，遵循最小权限原则
   - 不要将 Token 提交到代码中
   - 定期轮换 Token

2. **敏感文件**:
   - 使用 `.gitignore` 忽略敏感文件
   - 不要提交 API Key、密码等敏感信息

3. **分支保护**:
   - 对 `main` 分支启用保护规则
   - 要求 PR 审查
   - 要求状态检查通过

4. **二次确认**:
   - 敏感操作（删除、强制推送）前确认
   - 使用 `--dry-run` 参数预览操作

## 📊 技能元数据

```yaml
name: github
description: |
  GitHub 代码仓库管理工具。支持代码提交、Issue 管理、PR 创建、
  代码审查、Release 发布等完整开发工作流。
author: OpenClaw Community
version: 1.0.0
tags: ["GitHub", "Git", "代码管理", "版本控制", "开发工具"]
```

## 🚀 快速开始

### 查看文档
```bash
cat downloaded_skills/github/SKILL.md
```

### 初始化项目
```bash
cd /workspace/projects/stock-quant-select
git init
git add .
git commit -m "feat: 初始化量化项目"
```

### 连接远程仓库
```bash
git remote add origin https://github.com/your-username/repo.git
git push -u origin main
```

## 📞 相关文档

- `SKILL.md` - GitHub 技能主文档
- Git 官方文档: https://git-scm.com/doc
- GitHub CLI 文档: https://cli.github.com/manual/

## ✅ 安装清单

- [x] 下载技能包
- [x] 解压到独立目录
- [x] 检查依赖（Git）
- [x] 验证文件完整性
- [x] 创建说明文档
- [x] 提供应用示例

## 🎉 总结

GitHub Agent 技能包已成功下载并准备就绪。

**优势**:
- ✅ Git 已安装，无需额外配置
- ✅ 完整的 GitHub 工作流支持
- ✅ 适合量化项目版本管理
- ✅ 支持团队协作开发

**下一步**:
1. 配置 GitHub 认证（Token 或 SSH）
2. 创建 GitHub 仓库
3. 初始化项目的 Git 仓库
4. 提交和推送量化代码

---

**安装完成时间**: 2024-04-27
**安装状态**: ✅ 成功
**依赖检查**: ✅ 通过
