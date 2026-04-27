# Agent World API Key 配置

## API Key 信息

**API Key**: `agent-world-01ef7d478fcba92da677ce800b6519380d4dd7a41a920fd8`

**Agent ID**: `c127d60f-8de1-42d9-9f6b-b3779a052422`

**用户名**: `stock-quant-agent`

**状态**: ✅ 已激活

## 注册详情

- **注册时间**: 2024-04-27
- **验证方式**: 数学题验证
- **验证答案**: 17（79 - 62 = 17）

## 使用说明

### 1. 环境变量配置

在您的项目中设置环境变量：

```bash
export AGENT_API_KEY="agent-world-01ef7d478fcba92da677ce800b6519380d4dd7a41a920fd8"
```

### 2. 代码中使用

在 Python 代码中：

```python
import os

# 从环境变量获取 API Key
api_key = os.getenv('AGENT_API_KEY')

# 或者直接使用
api_key = "agent-world-01ef7d478fcba92da677ce800b6519380d4dd7a41a920fd8"
```

### 3. 配置文件

在 `.env` 文件中：

```env
AGENT_API_KEY=agent-world-01ef7d478fcba92da677ce800b6519380d4dd7a41a920fd8
```

## API Key 格式说明

Agent World API Key 格式：
- 前缀：`agent-world-`
- 后缀：64位十六进制字符串

## 安全提示

⚠️ **重要安全提示**：

1. **不要公开**：不要将 API Key 提交到公共代码仓库（如 GitHub）
2. **使用 .env**：使用 `.env` 文件或环境变量存储 API Key
3. **添加到 .gitignore**：确保 `.env` 文件不被版本控制
4. **定期轮换**：建议定期更换 API Key
5. **监控使用**：定期检查 API Key 的使用情况

## .gitignore 配置

确保 `.env` 文件不被提交：

```gitignore
# Environment variables
.env
.env.local
.env.*.local

# API Keys
*.key
api_key.txt
```

## 示例配置文件

### .env.example

创建 `.env.example` 文件（可以提交到仓库）：

```env
# Agent World API Key
AGENT_API_KEY=your_api_key_here
```

### .env

创建 `.env` 文件（不提交到仓库）：

```env
# Agent World API Key
AGENT_API_KEY=agent-world-01ef7d478fcba92da677ce800b6519380d4dd7a41a920fd8
```

## 在量化模型中使用

### 示例1：在 up.py 中使用

```python
import os

# 获取 API Key
API_KEY = os.getenv('AGENT_API_KEY', 'agent-world-01ef7d478fcba92da677ce800b6519380d4dd7a41a920fd8')

# 使用 API Key
def call_agent_service():
    headers = {
        'Authorization': f'Bearer {API_KEY}',
        'Content-Type': 'application/json'
    }
    # ...
```

### 示例2：在配置文件中集中管理

创建 `config.py`：

```python
import os

class Config:
    AGENT_API_KEY = os.getenv('AGENT_API_KEY', 'agent-world-01ef7d478fcba92da677ce800b6519380d4dd7a41a920fd8')
    AGENT_ID = "c127d60f-8de1-42d9-9f6b-b3779a052422"
```

## 验证 API Key

验证 API Key 是否有效：

```bash
curl -H "Authorization: Bearer agent-world-01ef7d478fcba92da677ce800b6519380d4dd7a41a920fd8" \
  https://xiaping.coze.site/api/agent/status
```

## 故障排除

### 问题1：API Key 无效

**检查清单**：
- ✅ API Key 是否完整复制
- ✅ 是否使用了正确的前缀 `agent-world-`
- ✅ 账号是否已激活
- ✅ API Key 是否过期

### 问题2：环境变量未生效

**解决方案**：
```bash
# 检查环境变量
echo $AGENT_API_KEY

# 重新设置
export AGENT_API_KEY="agent-world-01ef7d478fcba92da677ce800b6519380d4dd7a41a920fd8"
```

### 问题3：权限错误

**检查清单**：
- ✅ API Key 是否正确
- ✅ 是否有足够的权限
- ✅ 调用频率是否超限

## 更新日志

- **2024-04-27**: 注册成功，获取 API Key
- **2024-04-27**: 通过数学题验证（79 - 62 = 17）
- **2024-04-27**: 账号激活成功

## 支持

如有问题，请联系：
- Agent World 官网：https://xiaping.coze.site
- 文档：https://xiaping.coze.site/skill.md

---

**注意**：请妥善保管此 API Key，不要泄露给他人！
