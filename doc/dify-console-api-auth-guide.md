# Dify Console API 认证与调用指南

## 1. 认证机制概述

Dify Console API 采用 **三令牌 + CSRF 双重提交** 的认证机制：

| 令牌 | Cookie 名称 | HttpOnly | 用途 | 默认过期时间 |
|------|-------------|----------|------|-------------|
| Access Token | `access_token` | 是 | 用户身份认证 (JWT) | 60 分钟 |
| Refresh Token | `refresh_token` | 是 | 刷新 access token | 30 天 |
| CSRF Token | `csrf_token` | 否 | 防止跨站请求伪造 | 60 分钟（与 access token 一致） |

> **HTTPS 环境下**（且未配置 `COOKIE_DOMAIN`），所有 cookie 名称会加上 `__Host-` 前缀，例如 `__Host-csrf_token`。

### CSRF 校验规则

每个需要 `@login_required` 的请求，后端会校验：

1. `X-CSRF-Token` 请求头的值 == Cookie 中 `csrf_token` 的值
2. CSRF Token 的 JWT 签名有效且未过期
3. JWT 中的 `sub` 字段 == 当前登录用户的 ID

**不校验 CSRF 的场景：**

- Service API（使用 `validate_app_token`，不走 `@login_required`）
- Admin API Key 鉴权的请求（需开启 `ADMIN_API_KEY_ENABLE`）
- `CSRF_WHITE_LIST` 中匹配的路由

---

## 2. 登录流程

```
POST /console/api/login
Content-Type: application/json

{
  "email": "your@email.com",
  "password": "your_password"
}
```

**成功响应：**

```json
{ "result": "success" }
```

响应通过 `Set-Cookie` 同时写入三个令牌：

```
Set-Cookie: access_token=xxx; HttpOnly; Path=/
Set-Cookie: refresh_token=yyy; HttpOnly; Path=/
Set-Cookie: csrf_token=zzz; Path=/
```

> `csrf_token` 的 `HttpOnly=false`，这是为了让前端 JS 能读取它并放入请求头。

---

## 3. 调用受保护接口

登录成功后，调用任何 Console API 需满足：

- **Cookie 自动携带**：`access_token`、`refresh_token`、`csrf_token`（由 HTTP 客户端自动处理）
- **手动设置请求头**：`X-CSRF-Token: <csrf_token 的值>`

### 示例：获取应用列表

```http
GET /console/api/apps
Cookie: access_token=xxx; refresh_token=yyy; csrf_token=zzz
X-CSRF-Token: zzz
```

> `X-CSRF-Token` 的值必须与 Cookie 中 `csrf_token` 的值完全一致。

---

## 4. Token 刷新

Access Token 和 CSRF Token 同时过期（默认 60 分钟），需要通过 Refresh Token 获取新的令牌对：

```
POST /console/api/refresh-token
Cookie: refresh_token=yyy
```

> 此接口不需要 `X-CSRF-Token` 头（不在 `@login_required` 装饰器保护范围内）。

**成功响应：**

```json
{ "result": "success" }
```

响应通过 `Set-Cookie` 写入全新的三个令牌（旧的全部失效）：

```
Set-Cookie: access_token=new_xxx; HttpOnly; Path=/
Set-Cookie: refresh_token=new_yyy; HttpOnly; Path=/
Set-Cookie: csrf_token=new_zzz; Path=/
```

**刷新后必须使用新的 csrf_token 值作为后续请求的 `X-CSRF-Token`。**

---

## 5. Token 过期处理策略

```
请求 → 401?
  ├─ 否 → 正常返回
  └─ 是 → 调用 /refresh-token
       ├─ 成功 → 用新 token 重试原请求
       └─ 401 → refresh_token 也过期 → 重新登录
```

| 令牌过期 | 处理方式 |
|---------|---------|
| Access Token 过期 | 用 Refresh Token 调用 `/refresh-token` 获取新令牌 |
| Refresh Token 过期 | 必须重新调用 `/console/api/login` |
| CSRF Token 过期 | 与 Access Token 同时刷新，无需单独处理 |

---

## 6. 登出

```
POST /console/api/logout
Cookie: access_token=xxx; refresh_token=yyy; csrf_token=zzz
X-CSRF-Token: zzz
```

登出后服务端清除 Refresh Token，响应清除所有 Cookie。

---

## 7. Python 完整示例

```python
import requests


class DifyConsoleClient:
    """
    Dify Console API 客户端，支持自动登录、CSRF 令牌管理和 Token 自动刷新。

    用法:
        client = DifyConsoleClient("http://localhost/v1", "email", "password")
        client.login()
        apps = client.get("/apps").json()
    """

    def __init__(self, base_url: str, email: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.email = email
        self.password = password
        self.session = requests.Session()

    @property
    def _csrf_cookie_name(self) -> str:
        if self.base_url.startswith("https"):
            domain = self.session.cookies.get("csrf_token")
            if domain is None:
                return "__Host-csrf_token"
        return "csrf_token"

    def _get_csrf_token(self) -> str:
        return self.session.cookies.get(self._csrf_cookie_name, "")

    def login(self):
        resp = self.session.post(
            f"{self.base_url}/console/api/login",
            json={"email": self.email, "password": self.password},
        )
        resp.raise_for_status()
        if resp.json().get("result") != "success":
            if resp.json().get("result") == "fail":
                raise RuntimeError(resp.json().get("data", "workspace not found"))
            raise RuntimeError("Login failed")

    def _refresh_token(self) -> bool:
        resp = self.session.post(f"{self.base_url}/console/api/refresh-token")
        if resp.status_code == 401:
            self.login()
            return True
        return resp.status_code == 200

    def request(self, method: str, path: str, **kwargs):
        url = f"{self.base_url}/console/api{path}"
        headers = kwargs.pop("headers", {})
        headers["X-CSRF-Token"] = self._get_csrf_token()
        resp = self.session.request(method, url, headers=headers, **kwargs)
        if resp.status_code == 401:
            if self._refresh_token():
                headers["X-CSRF-Token"] = self._get_csrf_token()
                resp = self.session.request(method, url, headers=headers, **kwargs)
        return resp

    def get(self, path: str, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs):
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs):
        return self.request("PUT", path, **kwargs)

    def delete(self, path: str, **kwargs):
        return self.request("DELETE", path, **kwargs)

    def logout(self):
        headers = {"X-CSRF-Token": self._get_csrf_token()}
        self.session.post(f"{self.base_url}/console/api/logout", headers=headers)
        self.session.cookies.clear()


# 使用示例
if __name__ == "__main__":
    client = DifyConsoleClient(
        base_url="http://localhost/v1",
        email="admin@example.com",
        password="your_password",
    )
    client.login()

    # 获取应用列表
    apps = client.get("/apps")
    print(apps.json())

    # 登出
    client.logout()
```

---

## 8. cURL 示例

### 登录

```bash
# 登录并将 cookie 保存到文件
curl -X POST http://localhost/v1/console/api/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"your_password"}' \
  -c cookies.txt
```

### 调用接口

```bash
# 从 cookie 文件中提取 csrf_token 值
CSRF_TOKEN=$(grep csrf_token cookies.txt | awk '{print $NF}')

# 获取应用列表
curl http://localhost/v1/console/api/apps \
  -H "X-CSRF-Token: $CSRF_TOKEN" \
  -b cookies.txt
```

### 刷新 Token

```bash
curl -X POST http://localhost/v1/console/api/refresh-token \
  -b cookies.txt \
  -c cookies.txt

# 刷新后重新提取 csrf_token
CSRF_TOKEN=$(grep csrf_token cookies.txt | awk '{print $NF}')
```

---

## 9. 常见问题

### Q: 请求返回 401 "CSRF token is missing or invalid"

**原因：** 未携带 `X-CSRF-Token` 头，或头部值与 Cookie 中的 `csrf_token` 不一致。

**排查：**
1. 确认请求头包含 `X-CSRF-Token`
2. 确认其值与 Cookie 中 `csrf_token` 的值完全一致
3. 确认 Token 未过期（默认 60 分钟）

### Q: 刷新 Token 后仍然 401

**原因：** 刷新后 Cookie 更新了，但 `X-CSRF-Token` 头仍使用旧值。

**解决：** 刷新后必须从新 Cookie 中读取新的 `csrf_token` 值，用于后续请求的 `X-CSRF-Token` 头。

### Q: HTTPS 环境下找不到 csrf_token Cookie

**原因：** HTTPS + 未配置 `COOKIE_DOMAIN` 时，Cookie 名称为 `__Host-csrf_token`。

**解决：** 读取 Cookie 时使用 `__Host-csrf_token` 作为键名。

### Q: 能否关闭 CSRF 只用 Token 认证？

目前没有独立的开关。可选方案：

| 方案 | 说明 |
|------|------|
| Service API | 不走 `@login_required`，无需 CSRF |
| Admin API Key | 开启 `ADMIN_API_KEY_ENABLE`，admin key 鉴权跳过 CSRF |
| CSRF_WHITE_LIST | 在 `api/libs/token.py` 中添加免校验路由 |
| 自定义开关 | 在 `check_csrf_token` 中添加环境变量判断 |

---

## 10. 相关源码文件

| 文件 | 说明 |
|------|------|
| `api/libs/token.py` | CSRF 生成/校验、Cookie 读写 |
| `api/libs/login.py` | `@login_required` 装饰器（内含 CSRF 校验） |
| `api/libs/passport.py` | JWT 签发与验证 |
| `api/services/account_service.py` | 登录、登出、Token 刷新逻辑 |
| `api/controllers/console/auth/login.py` | 登录/登出/刷新的 HTTP 端点 |
| `api/extensions/ext_login.py` | Flask-Login 请求加载器 |
| `web/service/fetch.ts` | 前端请求拦截（自动附加 CSRF 头） |
| `web/service/refresh-token.ts` | 前端 Token 自动刷新逻辑 |

---

## 11. 环境变量参考

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `ACCESS_TOKEN_EXPIRE_MINUTES` | 60 | Access Token 和 CSRF Token 过期时间（分钟） |
| `REFRESH_TOKEN_EXPIRE_DAYS` | 30 | Refresh Token 过期时间（天） |
| `SECRET_KEY` | - | JWT 签名密钥，影响所有 Token |
| `ADMIN_API_KEY_ENABLE` | false | 是否启用 Admin API Key（可跳过 CSRF） |
| `ADMIN_API_KEY` | - | Admin API Key 的值 |
| `COOKIE_DOMAIN` | - | Cookie 域名，影响 `__Host-` 前缀 |
| `CONSOLE_WEB_URL` | - | Console 前端地址，影响 HTTPS 判断 |
| `CONSOLE_API_URL` | - | Console API 地址，影响 HTTPS 判断 |
