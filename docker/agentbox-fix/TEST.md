# AgentBox Conda 修复 - 端到端测试

## 背景

Dify SSH Sandbox 通过 `sh -lc` 执行命令（`ssh_sandbox.py:379`），而 Ubuntu 上 `sh` 是 `dash`，不读取 `.bashrc`。存在两层问题：

1. **镜像层**：conda 激活仅写入 `.bashrc`/`/etc/bash.bashrc`，`sh -l` login shell 不读取这些文件
2. **代码层**：`bash_tool.py` 硬编码 `export PATH=...:/usr/bin:/bin` 覆盖了 conda 激活后的 PATH

导致 `python3 -c "import requests"` 报 `ModuleNotFoundError`。

## 修复范围

| 层 | 文件 | 变更 |
|-----|------|------|
| 镜像 | `docker/agentbox-fix/Dockerfile` | conda 激活写入 `/etc/profile` 和 `~/.profile`（`sh -l` 读取） |
| 代码 | `api/core/sandbox/bash/bash_tool.py:105` | `export PATH=...:/bin` → `export PATH=...:$PATH` |
| 代码 | `api/core/sandbox/bash/bash_tool.py:114` | 移除 `tool_env` 中的 `PATH` 覆盖 |

## 构建

```bash
# 构建 API 镜像（含 bash_tool.py 修复）
docker build -t langgenius/dify-api:fix api/

# 构建 AgentBox 镜像（含 .profile 修复）
docker build -t langgenius/dify-agentbox:fix docker/agentbox-fix/
```

## 部署

```bash
# 1. 载入导出镜像（来源机器）
docker load -i dify-api-fix.tar
docker load -i dify-agentbox-fix.tar

# 2. 更新镜像标签
docker tag langgenius/dify-api:fix      langgenius/dify-api:latest
docker tag langgenius/dify-agentbox:fix  langgenius/dify-agentbox:latest

# 3. 重建容器
docker compose -f docker/docker-compose.yaml \
               -f docker/docker-compose.override.yaml \
               up -d --force-recreate api agentbox
```

## 测试用例

### 1. 容器内直接测试

```bash
# 验证 sh -lc 激活 conda
docker exec docker-agentbox-1 sh -lc 'echo "CONDA_PREFIX: $CONDA_PREFIX"'
# 期望: CONDA_PREFIX: /opt/conda

# 验证 Python 依赖库
docker exec docker-agentbox-1 sh -lc 'python3 -c "import requests; print(requests.__version__)"'
# 期望: 2.33.1

docker exec docker-agentbox-1 sh -lc 'python3 -c "import pandas; print(pandas.__version__)"'
# 期望: 2.3.3
```

### 2. Dify SSH Sandbox 执行链测试（通过 paramiko SSH）

```bash
docker exec docker-api-1 python3 -c "
import paramiko, shlex
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('agentbox', port=22, username='agentbox', password='agentbox', look_for_keys=False)

# 模拟 ssh_sandbox.py _build_exec_command + bash_tool.py 完整链路
command_body = 'cd /workspace && export PATH=/tmp/cli_tools:\$PATH && python3 -c \"import requests; print(requests.__version__)\"'
exec_cmd = f'sh -lc {shlex.quote(command_body)}'
stdin, stdout, stderr = c.exec_command(exec_cmd)

print('VERSION:', stdout.read().decode().strip())
err = stderr.read().decode().strip()
if err:
    print('STDERR:', err)
c.close()
"
# 期望: VERSION: 2.33.1, STDERR 为空
```

### 3. 原始镜像对比测试（验证问题已修复）

```bash
# 修复前
docker run --rm langgenius/dify-agentbox:latest sh -lc 'echo "CONDA_PREFIX: [$CONDA_PREFIX]"'
# 期望: CONDA_PREFIX: []  (空 - 未激活)

# 修复后
docker run --rm langgenius/dify-agentbox:fix sh -lc 'echo "CONDA_PREFIX: [$CONDA_PREFIX]"'
# 期望: CONDA_PREFIX: [/opt/conda]  (已激活)
```

### 4. Dify API 端到端测试

```bash
curl -s -X POST 'http://100.66.1.5/v1/chat-messages' \
  --header 'Authorization: Bearer app-Z713baqOCMC6WP7jm1p6GTyW' \
  --header 'Content-Type: application/json' \
  --data-raw '{
    "inputs": {},
    "query": "用python3执行 import requests; print(requests.__version__)",
    "response_mode": "blocking",
    "conversation_id": "",
    "user": "test-e2e"
  }' | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('answer','')[:200])"
# 期望: 返回包含 2.33.1，无 ModuleNotFoundError
```

### 5. 回归测试

```bash
# Node.js
docker exec docker-agentbox-1 node -e 'console.log("node:", process.version)'

# Go
docker exec docker-agentbox-1 go version

# Playwright
docker exec docker-agentbox-1 sh -lc 'python3 -c "from playwright.sync_api import sync_playwright; print(\"playwright: OK\")"'
```

## 回滚

```bash
# 拉取官方镜像覆盖修复
docker pull langgenius/dify-api:latest
docker pull langgenius/dify-agentbox:latest
docker compose -f docker/docker-compose.yaml \
               -f docker/docker-compose.override.yaml \
               up -d --force-recreate api agentbox

# 还原源码
git -C api checkout -- core/sandbox/bash/bash_tool.py
```
