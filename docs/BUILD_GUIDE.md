# Dify 本地构建与部署指南

本文档记录了在当前环境（中国大陆网络、MySQL 8.0、本地代码开发）下构建和部署 Dify 的完整流程。

---

## 一、环境准备

### 1.1 Docker 守护进程代理配置

Docker 构建时需要拉取基础镜像（如 `python:3.12-slim-bookworm`、`node:22-alpine`）和下载 Python/Node 依赖包。
由于无法依赖镜像源加速，Dockerfile 内 `apt-get`、`pip`、`npm` 等命令的外部下载必须通过代理，
因此需配置 Docker 守护进程代理。

**关键点**: 容器内 `127.0.0.1` 指向容器自身而非宿主机，必须使用 Docker 桥接网关 IP。

**获取 Docker 桥接网关 IP**:
```bash
docker network inspect bridge | grep -oP '"Gateway": "\K[^"]+'
# 通常为 172.17.0.1
```

**创建 `/etc/systemd/system/docker.service.d/http-proxy.conf`**:
```ini
[Service]
Environment="HTTP_PROXY=http://172.17.0.1:7890"
Environment="HTTPS_PROXY=http://172.17.0.1:7890"
Environment="NO_PROXY=localhost,127.0.0.1,mirrors.aliyun.com,192.168.31.155,redis,weaviate,sandbox,ssrf_proxy,plugin_daemon,nginx,agentbox,172.17.0.0/16"
```

**生效配置**:
```bash
systemctl daemon-reload
systemctl restart docker
systemctl show docker | grep -i proxy
```

**前提条件**: 宿主机代理服务（如 mihomo/clash）需监听在 `0.0.0.0:7890`（而非仅 `127.0.0.1`），
并设置 `allow-lan: true`。

> 代理配置详见 `/root/proxy-setup.md`，使用 `/root/proxy.sh {start|stop|restart|status}` 管理 mihomo 服务。

### 1.3 验证代理连通性

构建前建议从容器内测试代理是否可达：

```bash
docker run --rm -e http_proxy=http://172.17.0.1:7890 -e https_proxy=http://172.17.0.1:7890 alpine:latest sh -c "wget -q -O - -Y on -T 10 http://httpbin.org/ip"
# 预期返回海外出口 IP
```

---

## 二、代码修改

### 2.1 前端构建优化 (`web/Dockerfile`)

启用国内源并增加构建内存，防止 OOM 或超时。

```dockerfile
# 1. Alpine 包源加速
RUN sed -i 's/dl-cdn.alpinelinux.org/mirrors.aliyun.com/g' /etc/apk/repositories

# 2. npm/pnpm 源加速
RUN npm config set registry https://registry.npmmirror.com

# ... 在 packages stage 中 ...
RUN pnpm config set registry https://registry.npmmirror.com
RUN pnpm install --frozen-lockfile

# 3. 增加构建内存 (默认 4GB 可能不足)
ENV NODE_OPTIONS="--max-old-space-size=8192"
ENV NEXT_TELEMETRY_DISABLED=1
RUN pnpm build
```

### 2.2 禁用 Turbopack 实验特性 (`web/next.config.ts`)

生产构建中 Turbopack 可能导致超时，需禁用相关实验配置。

```typescript
const nextConfig: NextConfig = {
  // ...
  experimental: {
    turbopackFileSystemCacheForDev: false,
    // 确保没有其他导致不稳定的实验性配置
  },
}
```

### 2.3 MySQL 8.0 兼容性修复

Dify 1.14.0+ 的迁移文件使用了 MySQL 8.4+ 特性，在 MySQL 8.0 上需手动修复。

**修复 1: `uuidv7()` 不支持**
*文件*: `api/migrations/versions/2026_02_09_1726-227822d22895_add_workflow_comments_table.py`

移除 `server_default=sa.text("uuidv7()")`，改为由应用层生成 UUID。
```python
sa.Column("id", models.types.StringUUID(), nullable=False), # 移除 server_default
```

**修复 2: TEXT 列默认值**
*文件*: `api/migrations/versions/2026_03_09_1200-5ee0aa981887_add_app_asset_contents_table.py`

MySQL 8.0 不允许 TEXT 列有 `server_default`。
```python
sa.Column("content", sa.Text(), nullable=False), # 移除 server_default=""
```

---

## 三、Docker Compose 配置

修改 `docker/docker-compose.override.yaml`，使用本地代码构建而非拉取远程镜像。

```yaml
services:
  api:
    build:
      context: ../api
      dockerfile: Dockerfile
    environment:
      DB_TYPE: mysql
      DB_HOST: db_mysql
      DB_PORT: 3306
      DB_USERNAME: root
      DB_PASSWORD: difyai123456
      DB_DATABASE: dify

  worker:
    build:
      context: ../api
      dockerfile: Dockerfile
    environment:
      DB_TYPE: mysql
      DB_HOST: db_mysql
      DB_PORT: 3306
      DB_USERNAME: root
      DB_PASSWORD: difyai123456
      DB_DATABASE: dify

  worker_beat:
    build:
      context: ../api
      dockerfile: Dockerfile
    environment:
      DB_TYPE: mysql
      DB_HOST: db_mysql
      DB_PORT: 3306
      DB_USERNAME: root
      DB_PASSWORD: difyai123456
      DB_DATABASE: dify

  web:
    build:
      context: ../web
      dockerfile: Dockerfile

  db_mysql:
    profiles: [] # 移除 postgresql 限制

  plugin_daemon:
    image: langgenius/dify-plugin-daemon:0.5.3-local # 使用本地已有版本
    environment:
      DB_TYPE: mysql
      DB_HOST: db_mysql
      DB_PORT: 3306
      DB_USERNAME: root
      DB_PASSWORD: difyai123456
      DB_PLUGIN_DATABASE: dify_plugin

  agentbox:
    image: langgenius/dify-agentbox:latest
    user: "0:0"
    restart: always
    environment:
      AGENTBOX_SSH_USERNAME: agentbox
      AGENTBOX_SSH_PASSWORD: agentbox
      AGENTBOX_SSH_PORT: 22
      AGENTBOX_SOCAT_TARGET_HOST: api
      AGENTBOX_SOCAT_TARGET_PORT: 5001
      AGENTBOX_NGINX_HOST: nginx
      AGENTBOX_NGINX_PORT: 80
    command: >
      sh -c "
      set -e;
      mkdir -p /run/sshd;
      ssh-keygen -A;
      if [ \"$${AGENTBOX_SSH_USERNAME}\" = \"root\" ]; then
        echo \"root:$${AGENTBOX_SSH_PASSWORD}\" | chpasswd;
        grep -q '^PermitRootLogin' /etc/ssh/sshd_config && sed -i 's/^PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config || echo 'PermitRootLogin yes' >> /etc/ssh/sshd_config;
      else
        id -u \"$${AGENTBOX_SSH_USERNAME}\" >/dev/null 2>&1 || useradd -m -s /bin/bash \"$${AGENTBOX_SSH_USERNAME}\";
        echo \"$${AGENTBOX_SSH_USERNAME}:$${AGENTBOX_SSH_PASSWORD}\" | chpasswd;
      fi;
      grep -q '^PasswordAuthentication' /etc/ssh/sshd_config && sed -i 's/^PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config || echo 'PasswordAuthentication yes' >> /etc/ssh/sshd_config;
      nohup socat TCP-LISTEN:$${AGENTBOX_SOCAT_TARGET_PORT},bind=127.0.0.1,fork,reuseaddr TCP:$${AGENTBOX_SOCAT_TARGET_HOST}:$${AGENTBOX_SOCAT_TARGET_PORT} >/tmp/socat.log 2>&1 &
      nohup socat TCP-LISTEN:$${AGENTBOX_NGINX_PORT},bind=127.0.0.1,fork,reuseaddr TCP:$${AGENTBOX_NGINX_HOST}:$${AGENTBOX_NGINX_PORT} >/tmp/socat-nginx.log 2>&1 &
      exec /usr/sbin/sshd -D -p $${AGENTBOX_SSH_PORT}
      "
    ports:
      - "2222:22"
```

### 3.1 Agentbox 环境变量配置

在 `docker/.env` 中确保以下 agentbox 相关变量已配置：

```env
# SSH Sandbox 配置
SSH_SANDBOX_HOST=agentbox
SSH_SANDBOX_PORT=22
SSH_SANDBOX_USERNAME=agentbox
SSH_SANDBOX_PASSWORD=agentbox
SSH_SANDBOX_BASE_WORKING_PATH=/workspace/sandboxes

# Agentbox 服务配置
AGENTBOX_SSH_USERNAME=agentbox
AGENTBOX_SSH_PASSWORD=agentbox
AGENTBOX_SSH_PORT=22
AGENTBOX_SOCAT_TARGET_HOST=api
AGENTBOX_SOCAT_TARGET_PORT=5001
AGENTBOX_NGINX_HOST=nginx
AGENTBOX_NGINX_PORT=80

# 暴露的 SSH 端口
EXPOSE_AGENTBOX_SSH_PORT=2222
```

---

## 四、构建与启动

### 4.1 设置版本号

在 `docker/.env` 末尾添加版本标识：

```bash
echo "" >> docker/.env
echo "# Dify Version" >> docker/.env
echo "DIFY_VERSION=1.14.0-rc-2" >> docker/.env
```

`docker-compose.yaml` 中的镜像 tag 已经硬编码为 `1.14.0-rc-2`，此变量仅作标识用。

### 4.2 配置构建代理环境变量

```bash
export http_proxy=http://127.0.0.1:7890
export https_proxy=http://127.0.0.1:7890
export all_proxy=socks5h://127.0.0.1:7891
export NO_PROXY="localhost,127.0.0.1,mirrors.aliyun.com,192.168.31.155,redis,weaviate,sandbox,ssrf_proxy,plugin_daemon,nginx,agentbox"
```

### 4.3 清理环境

```bash
cd docker
docker compose down
docker system prune -af --volumes
```

### 4.4 执行构建

由于构建耗时较长（约 20-40 分钟，通过代理下载 Python 依赖包较慢），建议使用脚本后台运行。

**创建构建脚本 `/tmp/rebuild-dify.sh`**:
```bash
cat > /tmp/rebuild-dify.sh << 'SCRIPT'
#!/bin/bash
set -e

export http_proxy=http://127.0.0.1:7890
export https_proxy=http://127.0.0.1:7890
export all_proxy=socks5h://127.0.0.1:7891
export NO_PROXY="localhost,127.0.0.1,mirrors.aliyun.com,192.168.31.155,redis,weaviate,sandbox,ssrf_proxy,plugin_daemon,nginx,agentbox"

cd /root/dify/docker

echo "[$(date)] Starting Dify rebuild..." | tee /tmp/dify-build.log

docker compose -f docker-compose.yaml -f docker-compose.override.yaml down 2>/dev/null || true
docker compose -f docker-compose.yaml -f docker-compose.override.yaml up -d --build 2>&1 | tee -a /tmp/dify-build.log

echo "[$(date)] Build completed." | tee -a /tmp/dify-build.log
SCRIPT

chmod +x /tmp/rebuild-dify.sh
```

**启动构建**:
```bash
setsid bash /tmp/rebuild-dify.sh > /tmp/dify-build.log 2>&1 &
```

> `setsid` 可确保进程彻底脱离终端，避免会话关闭时被杀掉。

### 4.5 监控进度

```bash
tail -f /tmp/dify-build.log
```

**关键日志标志**:
- `pip install --no-cache-dir uv==0.8.9`: 后端基础环境安装（较快）
- `uv sync --locked --no-dev`: 后端 Python 依赖下载和安装（**通过代理较慢**，约 10-20 分钟）
- `pnpm install --frozen-lockfile`: 前端依赖安装（npm 已配置国内镜像，较快）
- `next build`: 前端构建（约 5-10 分钟）
- `Build completed.`: 构建全部完成

### 4.6 验证服务

```bash
# 1. 检查容器状态
docker compose ps -a

# 2. 检查 Web 访问
curl -s -o /dev/null -w "%{http_code}" http://localhost/
# 预期: 307 (重定向)

# 3. 检查 API 访问
curl -s -o /dev/null -w "%{http_code}" http://localhost/console/api/workspaces/current
# 预期: 401 或 405 (服务已启动，需认证)

# 4. 检查 Agentbox SSH 访问
sshpass -p agentbox ssh -o StrictHostKeyChecking=no -p 2222 agentbox@localhost echo "Agentbox OK"
# 预期: Agentbox OK
```

---

## 五、常见问题排查

### 5.1 磁盘空间不足
```
no space left on device
```
**解决**: `docker system prune -af --volumes`，确保 `/` 分区有 >20GB 空间。

### 5.2 前端构建超时
**现象**: `next build` 阶段卡死或报错。
**解决**: 
1. 确保 `NODE_OPTIONS="--max-old-space-size=8192"`。
2. 禁用 `turbopackFileSystemCacheForDev`。

### 5.3 后端依赖下载超时 (uv sync 卡死)
**现象**: `uv sync --locked --no-dev` 阶段长时间无输出或报 timeout。
**原因**: Python 依赖包（如 `pyarrow` 36MB, `llvmlite` 54MB）通过代理下载较慢，可能触发工具超时。
**解决**: 
1. 确保使用 `setsid` 后台启动构建（4.4），避免前端进程超时杀构建。
2. 验证代理连通性（1.3），确认容器内可访问 `172.17.0.1:7890`。
3. 检查 mihomo 是否运行正常：`/root/proxy.sh status`。

### 5.4 数据库迁移失败
**现象**: `OperationalError: (1050, "Table 'xxx' already exists")`。
**解决**: 手动更新 alembic 版本跳过已完成的迁移。
```sql
UPDATE alembic_version SET version_num = '目标版本号';
```

### 5.5 镜像拉取失败
**现象**: `pull access denied` 或连接超时。
**解决**: 确保 Docker 守护进程代理已正确配置（参考 1.1），通过代理拉取镜像。

### 5.6 Dockerfile 中 apt-get/pip 下载失败
**现象**: 构建日志中 `E: Failed to fetch ...` 或 `Connection timed out`，但镜像源已配好。
**原因**: Docker 守护进程代理未正确配置或使用了 `127.0.0.1`（容器内不可达）。
**解决**: 严格按 1.2 配置，使用 `172.17.0.1:7890` 作为代理地址。

### 5.7 Sandbox 文件上传失败 (localhost 不可达)
**现象**: 前端调用 `/files/storage-files/{token}` 时请求 `http://localhost`，但浏览器无法访问。
**解决**: 修改 `docker/.env` 中的 `FILES_API_URL` 为外部可访问地址：
```
FILES_API_URL=http://<your-server-ip>
```
例如：`FILES_API_URL=http://100.66.1.5`

### 5.8 Sandbox Provider 接口报错
**现象**: `/console/api/workspaces/current/sandbox-providers` 返回 `No sandbox provider configured for tenant`。
**解决**: 已在 `api/services/sandbox/sandbox_provider_service.py` 中修复，当无配置时 `is_active` 返回 `False` 而非抛出异常。

### 5.9 工作流串行执行（同一时间只能运行一个工作流）
**现象**: 提交多个工作流后，只有一个在执行，其余处于等待状态，前一个完成后才处理下一个。

**首次分析（已修正）**:

默认 `CELERY_WORKER_AMOUNT` 为空 → `entrypoint.sh:31` 回退为 `-c 1`（1 个 worker），导致串行。

曾尝试启用 `--autoscale=20,1` 解决，但无效。原因是 **`--autoscale` 与 `gevent` 池存在兼容性 Bug**。

**真正的根因 — Celery gevent 池 `grow()` 方法 Bug**:

`api/.venv/lib/python3.12/site-packages/celery/concurrency/gevent.py:128-130`:

```python
def grow(self, n=1):
    self._pool._semaphore.counter += n
    self._pool.size += n
```

`grow()` 仅递增 gevent Semaphore 的 `counter` 计数器，但**未调用 `release()` 来唤醒阻塞在 `acquire()` 上的 greenlet**。

gevent Semaphore 的工作机制：
- `acquire()`: 若 counter>0 直接递减返回；否则加入 waiter 队列并阻塞
- `release()`: 若有 waiter 则唤醒下一个；否则递增 counter

当 autoscaler 检测到积压并调用 `grow()` 时，`_spawn()` 中阻塞等待 semaphore 的 greenlet **永远不会被唤醒** → 实际并发始终为 1。

**解决方案**:

使用固定并发替代自动伸缩。在 `docker/.env` 中设置：

```
CELERY_AUTO_SCALE=false
CELERY_WORKER_AMOUNT=8
```

- gevent 池直接以 8 个 greenlet 启动，无需依赖 `grow()` release
- `--prefetch-multiplier=1`，每个 greenlet 预取 1 个任务，共计可并发 8 个工作流

**其他已验证非原因的路径**：

| 怀疑点 | 验证结论 |
|--------|---------|
| CFS Scheduler (`can_schedule`) | CE 中 `AsyncWorkflowSystemStrategy=Nop`，`can_schedule()` 从未被调用 |
| 数据库行锁 / 唯一约束 | `workflow_runs` 表无 status 唯一约束，允许多个 RUNNING 状态并存 |
| Redis 分布式锁 | 工作流执行路径中无任何分布式锁 |
| Rate Limiting | `APP_DEFAULT_ACTIVE_REQUESTS=0`，无限制 |
| `threading.Lock` | 仅限 GraphEngine 内部节点调度，不限制跨工作流并发 |

修改后重启 worker：`docker compose up -d worker`
