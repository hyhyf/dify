# Dify 生产环境部署指南

本文档记录了使用自定义镜像在外部 MySQL 8.0 和 Weaviate 环境下部署 Dify 1.14.0-rc-2-custom 的完整流程。

---

## 一、环境要求

### 1.1 软件依赖
- Docker 20.10+
- Docker Compose V2
- 外部 MySQL 8.0 数据库
- 外部 Weaviate 向量数据库（或其他支持的向量库）

### 1.2 网络要求
- 服务器需能访问 Docker Hub（拉取基础镜像）
- 如需使用代理，请配置 Docker 守护进程代理（见 1.3）

### 1.3 Docker 代理配置（可选）
若服务器网络受限，需配置 Docker 守护进程代理：

```bash
# 编辑 /etc/systemd/system/docker.service.d/http-proxy.conf
[Service]
Environment="HTTP_PROXY=http://<proxy-ip>:<port>"
Environment="HTTPS_PROXY=http://<proxy-ip>:<port>"
Environment="NO_PROXY=localhost,127.0.0.1,<internal-ips>"
```

```bash
systemctl daemon-reload
systemctl restart docker
```

---

## 二、镜像准备

### 2.1 加载本地镜像
若无法直接访问 Docker Hub，请将导出的镜像文件传输至服务器并加载：

```bash
# 传输镜像文件到服务器
scp dify-api-1.14.0-rc-2-custom.tar user@server:/path/to/images/
scp dify-web-1.14.0-rc-2-custom.tar user@server:/path/to/images/

# 加载镜像
docker load -i dify-api-1.14.0-rc-2-custom.tar
docker load -i dify-web-1.14.0-rc-2-custom.tar

# 验证加载
docker images | grep gaoyue1989/dify
```

### 2.2 镜像列表
| 镜像名 | 标签 | 大小 |
|--------|------|------|
| `gaoyue1989/dify-api` | `1.14.0-rc-2-custom` | ~4.15GB |
| `gaoyue1989/dify-web` | `1.14.0-rc-2-custom` | ~565MB |

---

## 三、代码准备

### 3.1 获取源码
```bash
git clone https://github.com/langgenius/dify.git
cd dify
# 切换到对应版本分支或 tag
git checkout <target-branch-or-tag>
```

### 3.2 关键修改清单
部署前需确认以下修改已应用：

1. **版本号修改**
   - `api/pyproject.toml`: `version = "1.14.0rc2+custom"`
   - `web/package.json`: `"version": "1.14.0-rc-2-custom"`
   - `docker/.env`: `DIFY_VERSION=1.14.0-rc-2-custom`

2. **Gunicorn 启动修复** (`api/gunicorn.conf.py`)
   - 将 `psycogreen.gevent` 和 `grpc.experimental.gevent` 的导入移至 `post_patch()` 回调内部
   - 避免在 gevent monkey-patch 前导入导致 worker 启动失败

3. **MySQL 8.0 兼容性**
   - 移除迁移文件中的 `uuidv7()` 函数调用（MySQL 8.0 不支持）
   - 移除 TEXT 列的 `server_default` 默认值

---

## 四、Docker Compose 配置

### 4.1 目录结构
```
dify/
├── docker/
│   ├── docker-compose.yaml
│   ├── docker-compose.override.yaml
│   └── .env
├── api/
│   ├── Dockerfile
│   ├── pyproject.toml
│   └── gunicorn.conf.py
└── web/
    ├── Dockerfile
    └── package.json
```

### 4.2 docker-compose.override.yaml
创建或修改 `docker/docker-compose.override.yaml`，配置外部数据库和服务：

```yaml
services:
  api:
    image: gaoyue1989/dify-api:1.14.0-rc-2-custom
    environment:
      DB_TYPE: mysql
      DB_HOST: <external-mysql-host>
      DB_PORT: 3306
      DB_USERNAME: <db-user>
      DB_PASSWORD: <db-password>
      DB_DATABASE: dify
    extra_hosts:
      - "host.docker.internal:host-gateway"
    volumes:
      - ../api/models:/app/api/models:ro
      - ../api/migrations:/app/api/migrations:ro

  worker:
    image: gaoyue1989/dify-api:1.14.0-rc-2-custom
    environment:
      DB_TYPE: mysql
      DB_HOST: <external-mysql-host>
      DB_PORT: 3306
      DB_USERNAME: <db-user>
      DB_PASSWORD: <db-password>
      DB_DATABASE: dify
    extra_hosts:
      - "host.docker.internal:host-gateway"
    volumes:
      - ../api/models:/app/api/models:ro

  worker_beat:
    image: gaoyue1989/dify-api:1.14.0-rc-2-custom
    environment:
      DB_TYPE: mysql
      DB_HOST: <external-mysql-host>
      DB_PORT: 3306
      DB_USERNAME: <db-user>
      DB_PASSWORD: <db-password>
      DB_DATABASE: dify
    extra_hosts:
      - "host.docker.internal:host-gateway"
    volumes:
      - ../api/models:/app/api/models:ro

  web:
    image: gaoyue1989/dify-web:1.14.0-rc-2-custom

  # 禁用内置 MySQL 容器
  db_mysql:
    profiles: ["disabled"]

  # 向量数据库配置（使用外部 Weaviate）
  weaviate:
    profiles: ["disabled"]
```

### 4.3 .env 配置
编辑 `docker/.env`，设置关键环境变量：

```bash
# 数据库配置
DB_TYPE=mysql
DB_HOST=<external-mysql-host>
DB_PORT=3306
DB_USERNAME=<db-user>
DB_PASSWORD=<db-password>
DB_DATABASE=dify

# Redis 配置
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_PASSWORD=difyai123456

# 向量数据库配置
VECTOR_STORE=weaviate
WEAVIATE_ENDPOINT=http://<external-weaviate-host>:8080
WEAVIATE_API_KEY=<weaviate-api-key>

# 版本标识
DIFY_VERSION=1.14.0-rc-2-custom

# Celery 并发配置（避免 gevent grow() bug）
CELERY_AUTO_SCALE=false
CELERY_WORKER_AMOUNT=8
```

---

## 五、部署步骤

### 5.1 启动服务
```bash
cd docker

# 停止旧服务（如有）
docker compose -f docker-compose.yaml -f docker-compose.override.yaml down

# 启动服务
docker compose -f docker-compose.yaml -f docker-compose.override.yaml up -d
```

### 5.2 验证服务
```bash
# 检查容器状态
docker compose ps

# 检查 API 健康
curl -s http://localhost/console/api/system-features

# 检查 Web 访问
curl -s -o /dev/null -w "%{http_code}" http://localhost/
# 预期: 307 (重定向)
```

### 5.3 查看日志
```bash
# API 服务日志
docker logs docker-api-1 -f

# Web 服务日志
docker logs docker-web-1 -f

# Worker 日志
docker logs docker-worker-1 -f
```

---

## 六、常见问题排查

### 6.1 API 服务无响应
**现象**: 访问 `/console/api/system-features` 返回 499 或超时

**原因**: `gunicorn.conf.py` 中顶层导入了 `psycogreen.gevent` 和 `grpc.experimental.gevent`，导致 gevent monkey-patch 前加载模块，worker 启动失败。

**解决**: 确认 `api/gunicorn.conf.py` 已将导入移至 `post_patch()` 回调内部：

```python
def post_patch(event):
    if not isinstance(event, gevent_events.GeventDidPatchBuiltinModulesEvent):
        return
    import psycogreen.gevent as pscycogreen_gevent
    from grpc.experimental import gevent as grpc_gevent
    grpc_gevent.init_gevent()
    pscycogreen_gevent.patch_psycopg()
```

### 6.2 数据库迁移失败
**现象**: `OperationalError: (1050, "Table 'xxx' already exists")`

**解决**: 手动更新 alembic 版本跳过已完成的迁移：
```sql
UPDATE alembic_version SET version_num = '目标版本号';
```

### 6.3 工作流串行执行
**现象**: 同一时间只能运行一个工作流

**原因**: Celery gevent 池 `grow()` 方法存在 Bug，未正确唤醒阻塞的 greenlet。

**解决**: 使用固定并发替代自动伸缩：
```bash
CELERY_AUTO_SCALE=false
CELERY_WORKER_AMOUNT=8
```

### 6.4 前端构建超时
**现象**: `next build` 阶段卡死

**解决**: 
1. 确保 `NODE_OPTIONS="--max-old-space-size=8192"`
2. 禁用 `turbopackFileSystemCacheForDev`

---

## 七、维护操作

### 7.1 更新镜像
```bash
# 加载新镜像
docker load -i dify-api-new.tar
docker load -i dify-web-new.tar

# 重启服务
docker compose -f docker-compose.yaml -f docker-compose.override.yaml up -d --no-deps api web worker worker_beat
```

### 7.2 备份数据
```bash
# 备份存储卷
docker run --rm -v docker_app_storage:/data -v $(pwd):/backup alpine tar czf /backup/storage-backup.tar.gz -C /data .

# 备份数据库（外部 MySQL）
mysqldump -h <mysql-host> -u <user> -p dify > dify-backup.sql
```

### 7.3 清理资源
```bash
# 停止并删除容器
docker compose -f docker-compose.yaml -f docker-compose.override.yaml down

# 清理未使用镜像
docker image prune -a

# 清理卷（谨慎操作）
docker volume prune
```

---

## 八、性能调优建议

### 8.1 API 服务
- `SERVER_WORKER_AMOUNT`: 根据 CPU 核心数调整，公式 `2 * cores + 1`
- `GUNICORN_TIMEOUT`: 默认 360s，长连接场景可适当增加

### 8.2 Celery Worker
- `CELERY_WORKER_AMOUNT`: 建议 8-16，根据任务负载调整
- `CELERY_PREFETCH_MULTIPLIER`: 保持 1，避免任务分配不均

### 8.3 数据库连接池
- `SQLALCHEMY_POOL_SIZE`: 默认 30，高并发场景可增加至 50-100
- `SQLALCHEMY_MAX_OVERFLOW`: 默认 10，配合 POOL_SIZE 调整

### 8.4 Redis
- 确保 Redis 内存充足，配置 `maxmemory-policy` 为 `allkeys-lru`
- 启用 AOF 持久化保证数据可靠性
