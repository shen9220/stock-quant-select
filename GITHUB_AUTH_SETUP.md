# GitHub 认证配置指南

## 问题描述

```
remote: Invalid username or token. Password authentication is not supported for Git operations.
fatal: Authentication failed for 'https://github.com/shen9220/stock-quant-select.git/'
```

## 解决方案

### 方案1：使用 Personal Access Token（推荐）✅

#### 步骤1：获取 GitHub Token

1. 访问：https://github.com/settings/tokens
2. 点击 "Generate new token" → "Generate new token (classic)"
3. 填写 Token 名称（任意）
4. 设置过期时间
5. 选择权限：
   - ✅ `repo` (Full control of private repositories)
   - ✅ `workflow` (Update GitHub Action workflows)
6. 点击 "Generate token"
7. **立即复制 Token**（只显示一次！）

#### 步骤2：配置 Git 使用 Token

在终端执行：

```bash
# 配置 Git 存储凭据
git config --global credential.helper store

# 设置远程仓库URL（替换 YOUR_TOKEN 为你的 Token）
git remote set-url origin https://YOUR_TOKEN@github.com/shen9220/stock-quant-select.git

# 推送（第一次会提示输入用户名，随便输入即可，Token 会自动使用）
git push origin main
```

#### 步骤3：永久保存 Token

```bash
# 添加凭据到文件
echo "https://YOUR_TOKEN:@github.com" > ~/.git-credentials

# 或使用 credential helper
git config --global credential.helper "cache --timeout=3600"
```

---

### 方案2：使用 SSH（推荐用于长期使用）✅

#### 步骤1：检查是否有 SSH Key

```bash
ls -la ~/.ssh/
```

#### 步骤2：生成 SSH Key（如不存在）

```bash
ssh-keygen -t ed25519 -C "1436114007@qq.com"
```

提示时：
- 输入保存路径：直接按回车（默认）
- 输入密码：可以留空或设置密码

#### 步骤3：添加 SSH Key 到 GitHub

1. 查看公钥：
   ```bash
   cat ~/.ssh/id_ed25519.pub
   ```

2. 复制公钥内容

3. 访问：https://github.com/settings/keys

4. 点击 "New SSH key"

5. 填写：
   - Title：任意（如 "My Server"）
   - Key：粘贴公钥内容

6. 点击 "Add SSH key"

#### 步骤4：切换到 SSH

```bash
git remote set-url origin git@github.com:shen9220/stock-quant-select.git
git push origin main
```

---

### 方案3：手动输入用户名和 Token

每次推送时手动输入：

```bash
git push origin main
# Username: shen9220
# Password: 粘贴你的 GitHub Token
```

---

## 快速验证

```bash
# 检查远程仓库配置
git remote -v

# 应该显示：
# origin  https://github.com/shen9220/stock-quant-select.git (fetch)
# origin  https://github.com/shen9220/stock-quant-select.git (push)

# 测试连接
ssh -T git@github.com
# 如果成功，会显示：Hi shen9220! You've successfully authenticated...
```

---

## 配置文件位置

| 配置文件 | 路径 | 说明 |
|---------|------|------|
| 全局配置 | `~/.gitconfig` | Git 全局配置 |
| 凭据存储 | `~/.git-credentials` | 存储的用户名和 Token |
| SSH 配置 | `~/.ssh/config` | SSH 连接配置 |
| SSH 公钥 | `~/.ssh/id_ed25519.pub` | SSH 公钥 |
| SSH 私钥 | `~/.ssh/id_ed25519` | SSH 私钥 |

---

## 故障排除

### 问题1：Token 无效

**错误**：`remote: Invalid username or token`

**解决**：
1. 确认 Token 没有过期
2. 确认 Token 有 `repo` 权限
3. 重新生成 Token

### 问题2：SSH 连接失败

**错误**：`Connection refused` 或 `Permission denied`

**解决**：
1. 检查 SSH Key 是否添加到 GitHub
2. 检查私钥文件权限：`chmod 600 ~/.ssh/id_ed25519`
3. 测试连接：`ssh -T git@github.com`

### 问题3：用户名或邮箱错误

```bash
git config --global user.name "shen9220"
git config --global user.email "1436114007@qq.com"
```

---

## 推荐的配置流程

### 首次配置（一次性）

```bash
# 1. 生成 SSH Key
ssh-keygen -t ed25519 -C "1436114007@qq.com"

# 2. 添加到 GitHub（复制公钥）
cat ~/.ssh/id_ed25519.pub

# 3. 切换到 SSH
git remote set-url origin git@github.com:shen9220/stock-quant-select.git

# 4. 验证连接
ssh -T git@github.com

# 5. 推送代码
git push origin main
```

### 后续使用

```bash
# 直接推送即可
git add .
git commit -m "你的修改说明"
git push origin main
```

---

## 相关资源

- [GitHub SSH 文档](https://docs.github.com/en/authentication/connecting-to-github-with-ssh)
- [GitHub Token 文档](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/about-authentication-to-github#authenticating-with-the-api)
- [Git Credential Storage](https://git-scm.com/book/en/v2/Git-Tools-Credential-Storage)

---

## 需要的信息

请提供以下任一信息以便配置：

1. **GitHub Personal Access Token**（推荐）
   - 获取地址：https://github.com/settings/tokens
   - 需要 `repo` 权限

2. **或者确认使用 SSH**
   - 我可以帮您生成 SSH Key
   - 您需要在 GitHub 添加公钥

提供后，我会立即帮您配置好并推送代码！
