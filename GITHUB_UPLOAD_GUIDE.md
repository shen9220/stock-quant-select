# GitHub 上传配置指南

## 📋 配置文件说明

我已经为您创建了一个配置文件模板：
```
github_upload_config.txt
```

## 📝 需要您提供的信息

### 1. GitHub 账户信息

```
GITHUB_USERNAME="你的GitHub用户名"
GITHUB_EMAIL="你的GitHub邮箱"
```

**获取方式**：
- 登录 GitHub
- 点击右上角头像 → Settings
- 查看 Username 和 Email

---

### 2. 仓库配置

```
REPO_NAME="仓库名称"
REPO_DESCRIPTION="仓库描述"
REPO_VISIBILITY="public"  # public 或 private
```

**说明**：
- `REPO_NAME`: 仓库名称（只能包含字母、数字、连字符）
- `REPO_DESCRIPTION`: 仓库的简短描述
- `REPO_VISIBILITY`: 
  - `public`: 公开仓库（任何人可见）
  - `private`: 私有仓库（仅自己可见）

---

### 3. 认证方式（二选一）

#### 方式1: Personal Access Token（推荐）⭐

```
AUTH_METHOD="token"
GITHUB_TOKEN="ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

**获取 Token 步骤**：

1. 访问：https://github.com/settings/tokens
2. 点击 "Generate new token (classic)"
3. 填写 Note（如：stock-quant-select）
4. 选择权限：
   - ✅ `repo` - 完整的仓库权限
   - ✅ `workflow` - GitHub Actions 权限
5. 点击 "Generate token"
6. **立即复制 Token**（只显示一次！）

**Token 格式**：`ghp_` 开头 + 40个字符

---

#### 方式2: SSH Key（需要提前配置）

```
AUTH_METHOD="ssh"
SSH_KEY_PATH="~/.ssh/id_ed25519"
```

**前提条件**：
- 已经生成 SSH Key
- 已经将公钥添加到 GitHub
- 可以正常通过 SSH 访问 GitHub

**生成 SSH Key 步骤**（如果还没有）：

```bash
# 1. 生成 SSH Key
ssh-keygen -t ed25519 -C "your_email@example.com"

# 2. 启动 ssh-agent
eval "$(ssh-agent -s)"

# 3. 添加 SSH Key
ssh-add ~/.ssh/id_ed25519

# 4. 复制公钥
cat ~/.ssh/id_ed25519.pub

# 5. 在 GitHub 添加 SSH Key
#    Settings → SSH and GPG keys → New SSH key
#    粘贴公钥内容
```

---

### 4. Git 配置

```
GIT_USER_NAME="你的名字"
GIT_USER_EMAIL="你的邮箱"
MAIN_BRANCH="main"
```

**说明**：
- `GIT_USER_NAME`: 提交时显示的名字
- `GIT_USER_EMAIL`: 提交时显示的邮箱
- `MAIN_BRANCH`: 主分支名称（建议使用 `main`）

---

### 5. .gitignore 配置

文件中已经预配置了常用的忽略规则，包括：

✅ Python 文件
✅ 虚拟环境
✅ IDE 配置
✅ 系统文件
✅ 敏感文件
✅ 日志文件
✅ 临时文件
✅ 量化结果

**重要提醒**：
- `.env` 文件会被忽略（包含 API Key）
- `API_KEY.md` 会被忽略
- 结果文件 `results/*.json` 会被忽略

如果需要忽略其他文件，可以在 `GITIGNORE_PATTERNS` 中添加。

---

### 6. README.md 配置

```
GENERATE_README="true"
README_TITLE="股票量化选股模型"
README_SHORT_DESC="多策略量化选股工具..."
LICENSE_TYPE="MIT"
```

**说明**：
- `GENERATE_README`: 是否自动生成 README.md
- `LICENSE_TYPE`: 开源协议类型
  - `MIT` - 最宽松（推荐）
  - `Apache-2.0` - 需要保留版权声明
  - `GPL-3.0` - 必须开源修改后的代码
  - `BSD-3-Clause` - 需要保留版权声明和许可证
  - `none` - 不添加许可证

---

## 📊 配置文件示例

### 最小配置示例

```bash
# GitHub 账户
GITHUB_USERNAME="yourusername"
GITHUB_EMAIL="youremail@example.com"

# 仓库配置
REPO_NAME="stock-quant-select"
REPO_DESCRIPTION="股票量化选股模型"
REPO_VISIBILITY="public"

# 认证方式
AUTH_METHOD="token"
GITHUB_TOKEN="ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"

# Git 配置
GIT_USER_NAME="Your Name"
GIT_USER_EMAIL="youremail@example.com"
MAIN_BRANCH="main"

# 其他配置使用默认值...
```

---

## ⚠️ 安全注意事项

### 1. Token 安全

- ✅ **不要将 Token 提交到代码仓库**
- ✅ **不要在公开场合分享 Token**
- ✅ **定期更换 Token**
- ✅ **只授予必要的权限**

### 2. 敏感文件

默认会忽略以下敏感文件：
- `.env` 文件
- API Key 文件
- 凭证文件
- 日志文件

**检查清单**：
- [ ] 确认 `.env` 文件在 `.gitignore` 中
- [ ] 确认 `API_KEY.md` 在 `.gitignore` 中
- [ ] 确认没有其他敏感文件

### 3. 仓库可见性

- **public**：任何人都可以访问和克隆
- **private**：只有你可以访问

**选择建议**：
- 如果是开源项目 → 选择 `public`
- 如果是个人使用 → 选择 `private`

---

## 🔍 配置检查清单

在提交配置文件前，请检查以下项目：

### 必填项

- [ ] `GITHUB_USERNAME` 已填写
- [ ] `GITHUB_EMAIL` 已填写
- [ ] `REPO_NAME` 已填写
- [ ] `REPO_DESCRIPTION` 已填写
- [ ] `REPO_VISIBILITY` 已选择（public/private）
- [ ] `AUTH_METHOD` 已选择（token/ssh）
- [ ] `GITHUB_TOKEN` 已填写（如果选择 token）
- [ ] `GIT_USER_NAME` 已填写
- [ ] `GIT_USER_EMAIL` 已填写
- [ ] `MAIN_BRANCH` 已填写（建议 main）

### 选填项

- [ ] `README_CUSTOM_CONTENT` 自定义内容（可选）
- [ ] `LICENSE_TYPE` 已选择（推荐 MIT）
- [ ] `.gitignore` 配置已检查
- [ ] 敏感文件已确认不会上传

---

## 📝 填写步骤

1. **打开配置文件**
   ```bash
   cat github_upload_config.txt
   ```

2. **编辑配置文件**
   ```bash
   # 使用你喜欢的编辑器
   nano github_upload_config.txt
   # 或
   vim github_upload_config.txt
   ```

3. **填写所有必要信息**
   - 按照上面的说明填写
   - 所有值用双引号包裹
   - 不需要的项留空即可

4. **保存文件**
   - 根据编辑器的保存方式保存

5. **通知我**
   - 告诉我："配置文件已填写完成，请处理"

---

## 🚀 填写完成后

我会执行以下操作：

1. ✅ 检查配置文件的完整性
2. ✅ 验证认证信息是否正确
3. ✅ 检查敏感文件是否被忽略
4. ✅ 初始化 Git 仓库
5. ✅ 配置 Git 用户信息
6. ✅ 添加所有文件到 Git
7. ✅ 创建初始提交
8. ✅ 创建 GitHub 仓库（或连接已有仓库）
9. ✅ 推送代码到 GitHub
10. ✅ 生成 README.md 和 LICENSE（如果配置）

---

## ❓ 常见问题

### Q1: Token 在哪里获取？
**A**: 访问 https://github.com/settings/tokens → Generate new token (classic)

### Q2: 我应该选择 public 还是 private？
**A**: 
- 想要分享给所有人 → public
- 个人私有使用 → private

### Q3: 如何确认我的配置正确？
**A**: 填写完成后通知我，我会检查配置并告诉你需要调整的地方

### Q4: 可以上传后再修改配置吗？
**A**: 可以，但有些配置（如仓库名称）修改后会影响仓库 URL

### Q5: 如果上传失败怎么办？
**A**: 我会检查错误原因并告诉你如何修复，通常是认证或权限问题

---

## 📞 需要帮助？

如果填写过程中遇到问题：
1. 查看上面的配置说明
2. 查看 GitHub 官方文档
3. 直接问我，我会提供详细指导

---

**准备好后，请填写 `github_upload_config.txt` 文件并通知我！** 🚀
