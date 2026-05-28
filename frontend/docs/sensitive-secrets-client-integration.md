# AiceMind 敏感数据客户端对接文档

## 1. 功能说明

后台新增了“系统设置 → 敏感数据”配置页，用于统一维护 API Key、Token、私钥片段等敏感参数。

服务端保存时会自动加密存储；客户端读取时，通过接口由服务端解密后返回明文，客户端无需持有主密钥。

---

## 2. 管理端配置

菜单路径：`系统设置 / 敏感数据`

每条配置包含以下字段：

- `key`：唯一标识，建议使用业务域命名，例如 `llm.openai.api_key.prod`
- `name`：显示名称
- `category`：分类，如 `llm`、`payment`、`storage`
- `value`：敏感值
- `description`：用途说明
- `enabled`：是否启用
- `clientAccessLevel`：客户端访问级别
  - `admin`：仅管理员可读
  - `authenticated`：登录用户可读
  - `entitled`：已登录且权益有效的用户可读

---

## 3. 客户端读取接口

### 3.1 接口地址

`POST /admin-api/client/sensitive-secrets/resolve`

### 3.2 请求头

```http
Authorization: Bearer <后台登录 token>
Content-Type: application/json
```

> token 沿用现有后台登录体系。

### 3.3 请求参数

```json
{
  "key": "llm.openai.api_key.prod"
}
```

### 3.4 成功响应

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "key": "llm.openai.api_key.prod",
    "name": "OpenAI 生产 API Key",
    "category": "llm",
    "description": "桌面客户端策略解释模型使用",
    "value": "sk-xxxxxx",
    "updatedAt": "2026-05-28 15:40:00"
  }
}
```

### 3.5 失败响应示例

```json
{
  "code": -1,
  "message": "敏感数据不存在",
  "data": null
}
```

常见失败原因：

- key 不存在
- 配置未启用
- 当前 token 权限不足
- 当前用户无权益（当 `clientAccessLevel = entitled`）

---

## 4. 推荐调用流程

1. 客户端先完成登录，获得 `accessToken`
2. 在需要访问第三方能力时，调用敏感数据解析接口
3. 取回明文 `value`
4. 直接用于当前请求，不建议长期落盘缓存
5. 如需缓存，建议仅保存在内存，并设置失效时间

---

## 5. JavaScript / TypeScript 示例

```ts
async function resolveSensitiveSecret(accessToken: string, key: string) {
  const response = await fetch('/admin-api/client/sensitive-secrets/resolve', {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${accessToken}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ key }),
  });

  const result = await response.json();
  if (result.code !== 0) {
    throw new Error(result.message || '读取敏感数据失败');
  }
  return result.data.value as string;
}
```

---

## 6. Java 示例

```java
String json = "{\"key\":\"llm.openai.api_key.prod\"}";
HttpRequest request = HttpRequest.newBuilder()
    .uri(URI.create(baseUrl + "/admin-api/client/sensitive-secrets/resolve"))
    .header("Authorization", "Bearer " + accessToken)
    .header("Content-Type", "application/json")
    .POST(HttpRequest.BodyPublishers.ofString(json))
    .build();

HttpResponse<String> response = httpClient.send(request, HttpResponse.BodyHandlers.ofString());
```

---

## 7. 安全建议

1. **不要把主密钥下发到客户端**
2. **不要把返回的明文长期写入本地磁盘**
3. **建议按用途拆 key，不要多个系统共用同一条敏感数据**
4. **建议定期轮换 value，并在 description 中记录用途与轮换规则**
5. **对高敏感 key 建议使用 `admin` 级别，避免普通客户端读取**

---

## 8. 管理端接口（供后台页面使用）

- `GET /admin-api/system/sensitive-secrets/list`
- `POST /admin-api/system/sensitive-secrets/save`
- `POST /admin-api/system/sensitive-secrets/delete`
- `POST /admin-api/system/sensitive-secrets/resolve`

其中：

- `list` 返回脱敏值
- `resolve` 返回明文，仅管理员/具备读取权限用户可调用

---

## 9. 兼容说明

当前实现为**单 key 单次读取**模式。

如果后续客户端存在批量拉取诉求，可继续扩展：

- 批量读取接口
- 按分类列出可读取 key
- 带版本号/etag 的本地缓存机制
