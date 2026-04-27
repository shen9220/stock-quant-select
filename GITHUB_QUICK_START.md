# GitHub 上传配置 - 快速参考

## 📋 快速填写指南

### 步骤1: 获取 GitHub Token（推荐方式）

1. 访问: https://github.com/settings/tokens
2. 点击 "Generate new token (classic)"
3. 权限勾选: ✅ `repo`, ✅ `workflow`
4. 点击 "Generate token"
5. **立即复制** Token（只显示一次！）

格式: `ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`

---

### 步骤2: 填写配置文件

编辑 `github_upload_config.txt`，填写以下**必填项**：

```bash
# 1. GitHub 账户信息
GITHUB_USERNAME="你的GitHub用户名"
GITHUB_EMAIL="你的GitHub邮箱"

# 2. 仓库配置
REPO_NAME="stock-quant-select"  # 仓库名称
REPO_DESCRIPTION="股票量化选股模型"  # 仓库描述
REPO_VISIBILITY="public"  # public 或 private

# 3. 认证方式
AUTH_METHOD="token"  # 使用 Token
GITHUB_TOKEN="ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"  # 粘贴你的 Token

# 4. Git 配置
GIT_USER_NAME="你的名字"  # 提交时显示的名字
GIT_USER_EMAIL="你的邮箱"  # 提交时显示的邮箱
MAIN_BRANCH="main"  # 主分支名称

# 5. 其他配置（使用默认值即可）
GENERATE_README="true"
ADD_LICENSE="true"
LICENSE_TYPE="MIT"
```

---

### 步骤3: 保存并通知

1. 保存 `github_upload_config.txt` 文件
2. 通知我：**"配置文件已填写完成，请处理"**

---

## ⚡ 最小配置模板

直接复制并修改以下内容到 `github_upload_config.txt`：

```bash
GITHUB_USERNAME="yourusername"
GITHUB_EMAIL="youremail@example.com"

REPO_NAME="stock-quant-select"
REPO_DESCRIPTION="股票量化选股模型"
REPO_VISIBILITY="public"

AUTH_METHOD="token"
GITHUB_TOKEN="ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"

GIT_USER_NAME="Your Name"
GIT_USER_EMAIL="youremail@example.com"
MAIN_BRANCH="main"

GENERATE_README="true"
ADD_LICENSE="true"
LICENSE_TYPE="MIT"
```

**注意**: 将上面所有 `xxxxx` 替换为你的实际信息！

---

## ✅ 填写检查清单

在提交前，确保以下项已填写：

- [ ] `GITHUB_USERNAME` = 你的用户名
- [ ] `GITHUB_EMAIL` = 你的邮箱
- [ ] `REPO_NAME` = stock-quant-select（或自定义）
- [ ] `GITHUB_TOKEN` = 你的 GitHub Token（ghp_ 开头）
- [ ] `GIT_USER_NAME` = 你的名字
- [ ] `GIT_USER_EMAIL` = 你的邮箱

---

## 🔑 Token 获取截图示例

```
┌─────────────────────────────────────────────┐
│  Generate new token (classic)              │
├─────────────────────────────────────────────┤
│                                             │
│  Note: stock-quant-select                   │
│  [________________________]                 │
│                                             │
│  Expiration: No expiration                  │
│  [▼ No expiration ▼]                        │
│                                             │
│  Select scopes:                             │
│  ☑ repo   (Full control of private repos)  │
│  ☑ workflow  (Update GitHub Action)        │
│                                             │
│  [Generate token]                          │
│                                             │
└─────────────────────────────────────────────┘
```

生成后：
```
┌─────────────────────────────────────────────┐
│  Personal access tokens                    │
├─────────────────────────────────────────────┤
│                                             │
│  Note: stock-quant-select                   │
│  Created on: Now                            │
│                                             │
│  ghp_1234567890abcdefghijklmnopqrstuvwxyz  │
│  [Copy to clipboard 📋]                    │
│                                             │
│  Make sure to copy it now. You won't be    │
│  able to see it again!                      │
│                                             │
└─────────────────────────────────────────────┘
```

---

## 📝 配置完成后我会做什么

1. 检查配置完整性
2. 验证 Token 有效性
3. 初始化 Git 仓库
4. 创建 GitHub 仓库
5. 推送所有代码
6. 生成 README.md 和 LICENSE

---

## ❓ 忘记了什么？

| 问题 | 解决方案 |
|------|---------|
| GitHub 用户名是哪个？ | 登录 GitHub，右上角头像旁边显示 |
| 邮箱用哪个？ | GitHub 账户绑定的邮箱 |
| Token 哪里找？ | https://github.com/settings/tokens |
| Token 过期了怎么办？ | 重新生成一个新的 Token |
| 仓库叫什么名字？ | 建议用 `stock-quant-select` |

---

**填写完成后告诉我，我会帮你完成上传！** 🚀
