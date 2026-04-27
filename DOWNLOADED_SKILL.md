# 下载的技能包说明

## 📋 已下载技能包概览

本目录已下载 **2个技能包**：

| 技能包 | 版本 | 文档数 | 用途 |
|--------|------|--------|------|
| **using-coze-cli** | 2.0.0 | 15个 | Coze CLI 共享基础 |
| **github** | 1.0.0 | 1个 | GitHub 代码仓库管理 |

---

## 技能包 1: using-coze-cli

### 基本信息

- **技能名称**: using-coze-cli
- **技能ID**: aee0a560-9634-415e-9ff3-77b1587a4134
- **版本**: 2.0.0
- **描述**: Coze CLI 共享基础：安装、认证登录(OAuth)、组织与空间切换、配置管理、全局执行原则、安全规则、错误码处理、代理配置
- **下载时间**: 2024-04-27
- **文档数**: 15个 Markdown 文件

### 目录结构

```
downloaded_skills/
├── using-coze-cli/                              # Coze CLI 技能包
│   ├── SKILL.md                                # 主技能文档
│   ├── coze-code/                              # Coze Code 相关
│   │   ├── MODULE.md
│   │   └── references/                         # 参考文档
│   │       ├── coze-code-deploy.md            # 部署文档
│   │       ├── coze-code-domain.md            # 域名文档
│   │       ├── coze-code-env.md               # 环境文档
│   │       ├── coze-code-message.md           # 消息文档
│   │       ├── coze-code-preview.md           # 预览文档
│   │       ├── coze-code-project.md           # 项目文档
│   │       └── coze-code-skill.md             # 技能文档
│   ├── coze-file/                              # Coze File 相关
│   │   ├── MODULE.md
│   │   └── references/
│   │       └── coze-file-upload.md            # 文件上传文档
│   └── coze-generate/                          # Coze Generate 相关
│       ├── MODULE.md
│       └── references/
│           ├── coze-generate-audio.md        # 音频生成文档
│           ├── coze-generate-image.md        # 图片生成文档
│           └── coze-generate-video.md        # 视频生成文档
└── github/                                     # GitHub 技能包 ⭐ 新下载
    └── SKILL.md                                # GitHub 代码仓库管理文档
```

---

## 技能包 2: github

### 基本信息

- **技能名称**: github
- **技能ID**: 53fd8a1c-a147-4ec8-9611-169e7800c3a9
- **版本**: 1.0.0
- **描述**: GitHub 代码仓库管理工具。支持代码提交、Issue 管理、PR 创建、代码审查、Release 发布等完整开发工作流
- **作者**: OpenClaw Community
- **下载时间**: 2024-04-27
- **文档数**: 1个 Markdown 文件
- **依赖**: Git (已安装)

### 核心功能

1. **仓库操作**: clone/pull/push
2. **Issue 管理**: 创建/编辑/关闭/标签
3. **Pull Request**: 创建/审查/合并
4. **代码分析**: 变更查看/搜索/统计
5. **Actions 工作流**: 监控/日志/触发
6. **Release 管理**: 创建/上传/标签

### 应用场景

- 代码版本管理
- 团队协作开发
- 项目进度跟踪
- 自动化部署
- 代码审查流程

### 详细文档

查看完整安装和使用说明：[GITHUB_AGENT_INSTALL.md](../GITHUB_AGENT_INSTALL.md)

### 快速使用

```bash
# 查看文档
cat downloaded_skills/github/SKILL.md

# 初始化项目
git init
git add .
git commit -m "feat: 初始化量化项目"

# 推送到 GitHub
git remote add origin https://github.com/your-username/repo.git
git push -u origin main
```

---

## 技能包 1: using-coze-cli

## 技能说明

### SKILL.md

Coze CLI 共享规则文档，包含：

1. **适用场景**
   - 用户明确要求使用 Coze CLI
   - 需要使用 `coze generate` 生成音频、图片或视频
   - 需要把本地生成文件上传后返回在线链接
   - 需要创建 Coze 项目、发送消息、查询状态、部署、获取预览

2. **必须遵守的执行原则**
   - 用户明确指定 Coze CLI 时，禁止私自改用别的能力
   - 优先使用 `--format json`
   - 对用户交付文件时，必须返回在线链接，不要返回本地路径
   - 不确定命令用法时，使用 `--help` 或 `--man` 查看

### 子模块说明

#### coze-code
Coze Code 相关功能：
- 项目创建和管理
- 消息发送
- 部署功能
- 预览功能
- 环境配置
- 域名管理
- 技能管理

#### coze-file
文件上传功能：
- 本地文件上传到 Coze 云存储
- 获取在线可访问链接

#### coze-generate
生成功能：
- 音频生成
- 图片生成
- 视频生成

## 使用方法

### 查看主文档

```bash
cat downloaded_skills/using-coze-cli/SKILL.md
```

### 查看具体模块

```bash
# 查看所有文件列表
find downloaded_skills/using-coze-cli -type f

# 查看部署文档
cat downloaded_skills/using-coze-cli/coze-code/references/coze-code-deploy.md

# 查看音频生成文档
cat downloaded_skills/using-coze-cli/coze-generate/references/coze-generate-audio.md
```

### 在量化项目中使用

可以将这个技能集成到您的量化模型中，用于：

1. **生成报告音频**：使用 `coze generate audio` 生成选股结果的语音播报
2. **生成图表图片**：使用 `coze generate image` 生成K线图、趋势图
3. **上传文件**：使用 `coze file upload` 上传分析结果到云端
4. **项目管理**：使用 `coze code project` 管理量化项目

## 文件规范

### 清理说明

已清理以下文件：
- ✅ 移除 `__MACOSx` 目录
- ✅ 移除 `.DS_Store` 文件
- ✅ 移除 `._*` 文件（Mac资源文件）
- ✅ 移除临时zip文件
- ✅ 规范目录结构

### 文件格式

- 主文档：`SKILL.md` (Markdown格式)
- 模块文档：`MODULE.md` (Markdown格式)
- 参考文档：`references/*.md` (Markdown格式)

所有文件均为标准Markdown格式，易于阅读和维护。

## API Key 配置

技能包下载使用的 API Key：
```
agent-world-01ef7d478fcba92da677ce800b6519380d4dd7a41a920fd8
```

详细配置请参考：
- [API_KEY.md](../API_KEY.md)
- [.env](../.env)

---

## 📊 技能包对比

| 特性 | using-coze-cli | github |
|------|----------------|--------|
| **主要功能** | Coze CLI 工具链 | GitHub 代码管理 |
| **文档数量** | 15个 | 1个 |
| **核心依赖** | coze CLI | Git |
| **应用场景** | 文件上传、内容生成、项目部署 | 版本控制、团队协作 |
| **复杂度** | 高（多模块） | 中（核心功能） |
| **安装状态** | ✅ 已下载 | ✅ 已下载 |
| **依赖检查** | - | ✅ Git 已安装 |

---

## 🚀 快速开始

### 查看文档

```bash
# 查看 Coze CLI 文档
cat downloaded_skills/using-coze-cli/SKILL.md

# 查看 GitHub 文档
cat downloaded_skills/github/SKILL.md
```

### 使用示例

#### 1. Coze CLI - 生成图片
```bash
# 参考文档
cat downloaded_skills/using-coze-cli/coze-generate/references/coze-generate-image.md

# 生成股票K线图
coze generate image --prompt "股票K线图" --output chart.png

# 上传到云端
coze file upload --file chart.png
```

#### 2. GitHub - 版本控制
```bash
# 参考文档
cat downloaded_skills/github/SKILL.md

# 初始化仓库
git init
git add .
git commit -m "feat: 添加量化模型"

# 推送到 GitHub
git remote add origin https://github.com/your-username/repo.git
git push -u origin main
```

---

## 📚 相关文档

### 技能文档
- [using-coze-cli/SKILL.md](downloaded_skills/using-coze-cli/SKILL.md)
- [github/SKILL.md](downloaded_skills/github/SKILL.md)

### 安装文档
- [GITHUB_AGENT_INSTALL.md](../GITHUB_AGENT_INSTALL.md) - GitHub 技能包详细安装说明
- [API_KEY.md](../API_KEY.md) - API Key 配置指南
- [技能包下载完成报告.md](../技能包下载完成报告.md) - 第一个技能包下载报告

### 外部文档
- Agent World 指南: https://xiaping.coze.site/skill.md
- Coze CLI 官方文档: https://www.coze.cn/docs/developer_guides/cli
- Git 官方文档: https://git-scm.com/doc
- GitHub 官方文档: https://docs.github.com

---

## ✅ 下载清单

- [x] using-coze-cli (v2.0.0) - 15个文档
- [x] github (v1.0.0) - 1个文档
- [x] 文件规范化清理
- [x] 依赖检查（Git）
- [x] 创建详细文档
- [x] API Key 配置

---

## 🎉 总结

已成功下载 **2个技能包**，包含 **16个 Markdown 文档**。

### 技能包概览

1. **using-coze-cli**: Coze CLI 完整工具链
   - 代码开发（coze-code）
   - 文件上传（coze-file）
   - 内容生成（coze-generate）

2. **github**: GitHub 代码仓库管理
   - 版本控制
   - 团队协作
   - 项目部署

### 联合使用

两个技能包可以配合使用：
1. 使用 GitHub 管理量化代码版本
2. 使用 Coze CLI 生成图表和分析报告
3. 上传结果到云端并返回链接
4. 通过 GitHub Issues 和 PR 管理项目进度

---

**最后更新**: 2024-04-27
**下载状态**: ✅ 全部成功
**文档总数**: 16个
**总大小**: ~100KB

详细配置说明请参考 `/workspace/projects/stock-quant-select/API_KEY.md`

## 更新日志

- **2024-04-27**: 下载技能包 aee0a560-9634-415e-9ff3-77b1587a4134
- **2024-04-27**: 清理文件结构，移除系统文件
- **2024-04-27**: 创建使用说明文档

## 技术支持

- Agent World 文档: https://xiaping.coze.site/skill.md
- Coze CLI 文档: 详见 SKILL.md

---

**注意**：此技能包已规范整理，可直接使用。如需更新技能，可使用相同的 API Key 重新下载。
