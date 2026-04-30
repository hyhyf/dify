#!/bin/bash
# ============================================================================
# Dify Docker 镜像构建与推送脚本
# 构建 API + Web 镜像并推送至 Docker Hub
# ============================================================================
set -e

# --- 代理配置 ---
export http_proxy=http://127.0.0.1:7890
export https_proxy=http://127.0.0.1:7890
export all_proxy=socks5h://127.0.0.1:7891
export NO_PROXY="localhost,127.0.0.1,docker.m.daocloud.io,docker.1ms.run,mirrors.aliyun.com,192.168.31.155,redis,weaviate,sandbox,ssrf_proxy,plugin_daemon,nginx,agentbox"

# --- 镜像标签 ---
DOCKER_USER="gaoyue1989"
VERSION="1.14.0-rc-2-custom"

API_IMAGE="${DOCKER_USER}/dify-api:${VERSION}"
WEB_IMAGE="${DOCKER_USER}/dify-web:${VERSION}"
LATEST_API_IMAGE="${DOCKER_USER}/dify-api:latest"
LATEST_WEB_IMAGE="${DOCKER_USER}/dify-web:latest"

LOG_FILE="/tmp/dify-build-push-$(date +%Y%m%d_%H%M%S).log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

log "╔══════════════════════════════════════════════════════╗"
log "║     Dify Docker 镜像构建 & 推送                       ║"
log "╠══════════════════════════════════════════════════════╣"
log "║ API Image:  ${API_IMAGE}"
log "║ Web Image:  ${WEB_IMAGE}"
log "║ Log:        ${LOG_FILE}"
log "╚══════════════════════════════════════════════════════╝"

# ========================================================================
# Step 1: Build API Image
# ========================================================================
log ""
log "=============================================="
log "Step 1/4: Building API image..."
log "=============================================="

docker build \
    --build-arg COMMIT_SHA="$(git -C /root/dify rev-parse --short HEAD 2>/dev/null || echo 'custom')" \
    --build-arg http_proxy="${http_proxy}" \
    --build-arg https_proxy="${https_proxy}" \
    --build-arg HTTP_PROXY="${http_proxy}" \
    --build-arg HTTPS_PROXY="${https_proxy}" \
    -t "${API_IMAGE}" \
    -f /root/dify/api/Dockerfile \
    /root/dify/api \
    2>&1 | tee -a "$LOG_FILE"

log "[DONE] API image built: ${API_IMAGE}"
docker image inspect "${API_IMAGE}" --format 'Size: {{.Size}}' | tee -a "$LOG_FILE"

# ========================================================================
# Step 2: Build Web Image
# ========================================================================
log ""
log "=============================================="
log "Step 2/4: Building Web image..."
log "=============================================="

docker build \
    --build-arg COMMIT_SHA="$(git -C /root/dify rev-parse --short HEAD 2>/dev/null || echo 'custom')" \
    --build-arg http_proxy="${http_proxy}" \
    --build-arg https_proxy="${https_proxy}" \
    --build-arg HTTP_PROXY="${http_proxy}" \
    --build-arg HTTPS_PROXY="${https_proxy}" \
    -t "${WEB_IMAGE}" \
    -f /root/dify/web/Dockerfile \
    /root/dify/web \
    2>&1 | tee -a "$LOG_FILE"

log "[DONE] Web image built: ${WEB_IMAGE}"
docker image inspect "${WEB_IMAGE}" --format 'Size: {{.Size}}' | tee -a "$LOG_FILE"

# ========================================================================
# Step 3: Push API Image
# ========================================================================
log ""
log "=============================================="
log "Step 3/4: Pushing API image to Docker Hub..."
log "=============================================="

docker push "${API_IMAGE}" 2>&1 | tee -a "$LOG_FILE"
log "[DONE] API image pushed: ${API_IMAGE}"

# Tag and push latest
docker tag "${API_IMAGE}" "${LATEST_API_IMAGE}"
docker push "${LATEST_API_IMAGE}" 2>&1 | tee -a "$LOG_FILE"
log "[DONE] API latest pushed: ${LATEST_API_IMAGE}"

# ========================================================================
# Step 4: Push Web Image
# ========================================================================
log ""
log "=============================================="
log "Step 4/4: Pushing Web image to Docker Hub..."
log "=============================================="

docker push "${WEB_IMAGE}" 2>&1 | tee -a "$LOG_FILE"
log "[DONE] Web image pushed: ${WEB_IMAGE}"

# Tag and push latest
docker tag "${WEB_IMAGE}" "${LATEST_WEB_IMAGE}"
docker push "${LATEST_WEB_IMAGE}" 2>&1 | tee -a "$LOG_FILE"
log "[DONE] Web latest pushed: ${LATEST_WEB_IMAGE}"

# ========================================================================
# Summary
# ========================================================================
log ""
log "╔══════════════════════════════════════════════════════╗"
log "║              BUILD & PUSH COMPLETE                   ║"
log "╠══════════════════════════════════════════════════════╣"
log "║ ${API_IMAGE}"
log "║ ${LATEST_API_IMAGE}"
log "║ ${WEB_IMAGE}"
log "║ ${LATEST_WEB_IMAGE}"
log "╠══════════════════════════════════════════════════════╣"
log "║ Log: ${LOG_FILE}"
log "╚══════════════════════════════════════════════════════╝"
