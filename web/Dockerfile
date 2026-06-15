# base image
FROM node:22-alpine AS base
LABEL maintainer="takatost@gmail.com"

# China network: use aliyun mirror for Alpine packages (disabled due to proxy routing issues)
# RUN sed -i 's/dl-cdn.alpinelinux.org/mirrors.aliyun.com/g' /etc/apk/repositories

# China network: use npmmirror for npm
RUN npm config set registry https://registry.npmmirror.com

RUN apk add --no-cache tzdata
ENV PNPM_HOME="/pnpm"
ENV PATH="$PNPM_HOME:$PATH"

# China network: set corepack registry to npmmirror
ENV COREPACK_NPM_REGISTRY=https://registry.npmmirror.com

RUN corepack enable
ARG NEXT_PUBLIC_BASE_PATH=""
ENV NEXT_PUBLIC_BASE_PATH="$NEXT_PUBLIC_BASE_PATH"
ARG ENABLE_SOURCE_MAP=""
ENV ENABLE_SOURCE_MAP="$ENABLE_SOURCE_MAP"
ARG ENABLE_PROD_SOURCEMAP="false"
ENV ENABLE_PROD_SOURCEMAP="$ENABLE_PROD_SOURCEMAP"


# install packages
FROM base AS packages

WORKDIR /app/web

COPY package.json pnpm-lock.yaml /app/web/

# Use packageManager from package.json
RUN corepack install

# China network: use npmmirror for pnpm
RUN pnpm config set registry https://registry.npmmirror.com

RUN pnpm install --frozen-lockfile

# build resources
FROM base AS builder
WORKDIR /app/web
COPY --from=packages /app/web/ .
COPY . .

ENV NODE_OPTIONS="--max-old-space-size=6144"
ENV NEXT_TELEMETRY_DISABLED=1
ENV TURBOPACK=0
ENV UV_THREADPOOL_SIZE=8
ENV CI=true
RUN pnpm build


# production stage
FROM base AS production

ENV NODE_ENV=production
ENV EDITION=SELF_HOSTED
ENV DEPLOY_ENV=PRODUCTION
ENV CONSOLE_API_URL=http://127.0.0.1:5001
ENV APP_API_URL=http://127.0.0.1:5001
ENV MARKETPLACE_API_URL=https://marketplace.dify.ai
ENV MARKETPLACE_URL=https://marketplace.dify.ai
ENV PORT=3000
ENV NEXT_TELEMETRY_DISABLED=1

# set timezone
ENV TZ=UTC
RUN ln -s /usr/share/zoneinfo/${TZ} /etc/localtime \
    && echo ${TZ} > /etc/timezone

# Create non-root user
ARG dify_uid=1001
RUN addgroup -S -g ${dify_uid} dify && \
    adduser -S -u ${dify_uid} -G dify -s /bin/ash -h /home/dify dify && \
    mkdir /app && \
    chown -R dify:dify /app


WORKDIR /app/web

COPY --from=builder --chown=dify:dify /app/web/public ./public
COPY --from=builder --chown=dify:dify /app/web/.next/standalone ./
COPY --from=builder --chown=dify:dify /app/web/.next/static ./.next/static

COPY --chown=dify:dify --chmod=755 docker/entrypoint.sh ./entrypoint.sh

ARG COMMIT_SHA
ENV COMMIT_SHA=${COMMIT_SHA}

USER dify
EXPOSE 3000
ENTRYPOINT ["/bin/sh", "./entrypoint.sh"]
