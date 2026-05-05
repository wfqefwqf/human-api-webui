# Human-API WebUI

> 把你当做 API — 外部程序调用 Chat 接口，你在 WebUI 管理后台人工回复冒充 AI

**仅供学习与娱乐，请勿用于非法用途！**

---

## 功能特性

- **OpenAI 兼容接口** — `/v1/chat/completions`，支持流式/非流式响应
- **WebUI 管理后台** — 深色主题，三栏布局（会话列表 / 回复区 / 设置面板）
- **WebSocket 实时推送** — 新消息即时通知，无需刷新页面
- **会话管理** — 自动分配会话 ID，保存完整聊天上下文
- **超时自动回复** — 可自定义超时时间和默认回复内容
- **接口鉴权** — 后台设置 API 密钥，非法请求直接拦截
- **并发处理** — 多线程 + Event 机制，支持多个请求同时等待回复
- **完整日志** — 控制台 + 文件双输出，按日期自动分割

---

## 项目结构

```
human-api/
├── app.py              # 后端主服务（Flask + SocketIO）
├── config.py           # 配置管理模块（JSON 持久化）
├── human_api.py        # 原始项目文件（保留）
├── requirements.txt    # Python 依赖
├── static/
│   ├── index.html      # WebUI 管理页面
│   ├── css/
│   │   └── style.css   # 样式文件
│   └── js/
│       └── app.js      # 前端逻辑
├── data/               # 配置持久化目录（自动生成）
│   └── config.json     # 运行时配置文件
└── logs/               # 日志目录（自动生成）
    └── human-api-*.log # 按日期分割的日志文件
```

---

## 快速开始

### 1. 安装依赖

```bash
# Python 版本要求：3.8+
pip install -r requirements.txt
```

所需依赖仅两个：
- `flask` — Web 框架
- `flask-socketio` — WebSocket 支持

### 2. 启动服务

```bash
python app.py
```

启动后会看到：

```
==================================================
  Human-API Server
==================================================
  WebUI 管理界面: http://127.0.0.1:5000
  API 端点:       http://0.0.0.0:5000/v1/chat/completions
  超时时间:       120秒
  接口鉴权:       未启用（任何人可调用）
==================================================
```

### 3. 打开 WebUI

浏览器访问 **http://127.0.0.1:5000** 即可进入管理后台。

局域网其他设备访问：`http://<你的IP>:5000`

---

## 使用流程

### 完整工作流程

```
外部程序调用 API  →  后端接收请求  →  WebUI 实时显示  →  管理员回复  →  响应返回给调用方
```

### 第一步：外部程序发送请求

**curl 示例：**

```bash
curl -X POST http://127.0.0.1:5000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4",
    "messages": [
      {"role": "system", "content": "你是一个有用的助手"},
      {"role": "user", "content": "你好，请介绍一下自己"}
    ]
  }'
```

**Python 示例：**

```python
import requests

response = requests.post(
    "http://127.0.0.1:5000/v1/chat/completions",
    json={
        "model": "gpt-4",
        "messages": [
            {"role": "user", "content": "你好，请介绍一下自己"}
        ]
    }
)
print(response.json())
```

**Node.js 示例：**

```javascript
const response = await fetch("http://127.0.0.1:5000/v1/chat/completions", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    model: "gpt-4",
    messages: [{ role: "user", content: "你好" }]
  })
});
const data = await response.json();
console.log(data);
```

**流式响应：**

```bash
curl -X POST http://127.0.0.1:5000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4",
    "stream": true,
    "messages": [{"role": "user", "content": "你好"}]
  }'
```

### 第二步：在 WebUI 中回复

1. 打开浏览器访问 `http://127.0.0.1:5000`
2. 左侧会话列表会**实时弹出**新消息（带声音提示）
3. 点击等待中的会话，中间区域会显示完整对话历史
4. 在底部输入框输入回复内容
5. 点击「发送回复」或按 `Ctrl + Enter` 提交
6. 回复会以 OpenAI 格式返回给调用方

### 第三步：查看历史

- 左侧列表可按状态筛选：全部 / 等待中 / 已回复 / 已超时
- 点击任意会话查看完整聊天上下文
- 右上角统计面板显示待回复数、已回复数、总会话数

---

## WebUI 管理后台

### 页面布局

| 区域 | 功能 |
|------|------|
| **顶部导航** | 项目名称、统计数据（待回复/已回复/总会话）、WebSocket 连接状态 |
| **左侧会话列表** | 所有会话按状态排序（等待中优先），支持筛选过滤 |
| **中间回复区** | 选中会话后显示完整对话历史，底部输入回复 |
| **右侧设置面板** | API 密钥、超时时间、超时回复、清空历史 |

### 系统设置

在右侧面板可以配置：

- **API 访问密钥** — 设置后外部调用必须携带密钥（Bearer Token 或 `api_key` 参数）
- **回复超时时间** — 超过此时间未回复，自动返回默认回复（最小 10 秒）
- **超时默认回复** — 超时时自动返回给调用方的内容
- **清空所有会话** — 删除全部聊天历史

---

## API 接口文档

### Chat Completions（核心接口）

兼容 OpenAI 格式，外部程序调用此接口发送消息。

```
POST /v1/chat/completions
POST /api/chat          （兼容旧版路径）
```

**请求头：**

```
Content-Type: application/json
Authorization: Bearer <api_key>    （如果设置了密钥）
```

**请求体：**

```json
{
  "model": "gpt-4",
  "stream": false,
  "messages": [
    {"role": "system", "content": "你是一个有用的助手"},
    {"role": "user", "content": "你好"}
  ]
}
```

**响应（非流式）：**

```json
{
  "id": "chatcmpl-sess-xxxxxxxxxxxx",
  "object": "chat.completion",
  "created": 1714900000,
  "model": "gpt-4",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "你好！有什么我可以帮你的吗？"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0
  }
}
```

**超时响应（120 秒无人回复）：**

```json
{
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": "抱歉，当前人工客服繁忙，请稍后再试。"
      }
    }
  ]
}
```

### 查看模型列表

```
GET /v1/models
```

### 管理后台 API

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/admin/sessions` | GET | 获取所有会话列表 |
| `/api/admin/sessions/<id>` | GET | 获取单个会话详情 |
| `/api/admin/sessions/<id>/messages` | GET | 获取会话消息历史 |
| `/api/admin/reply` | POST | 提交人工回复 `{"session_id":"xxx","content":"回复内容"}` |
| `/api/admin/stats` | GET | 获取统计数据 |
| `/api/admin/config` | GET | 获取当前配置 |
| `/api/admin/config` | POST | 更新配置 `{"api_key":"xxx","timeout":120}` |
| `/api/admin/clear` | POST | 清空所有会话 |

---

## 接口鉴权

### 不设密钥（默认）

任何人可以直接调用 API，适合本地开发测试。

### 设置密钥

1. 在 WebUI 右侧「系统设置」中输入密钥，点击保存
2. 或通过 API 设置：

```bash
curl -X POST http://127.0.0.1:5000/api/admin/config \
  -H "Content-Type: application/json" \
  -d '{"api_key": "my-secret-key"}'
```

3. 之后所有 Chat 接口调用必须携带密钥：

```bash
# 方式一：Authorization Header
curl -X POST http://127.0.0.1:5000/v1/chat/completions \
  -H "Authorization: Bearer my-secret-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4","messages":[{"role":"user","content":"你好"}]}'

# 方式二：请求体参数
curl -X POST http://127.0.0.1:5000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4","api_key":"my-secret-key","messages":[{"role":"user","content":"你好"}]}'

# 方式三：URL 参数
curl -X POST "http://127.0.0.1:5000/v1/chat/completions?api_key=my-secret-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4","messages":[{"role":"user","content":"你好"}]}'
```

---

## 常见问题

**Q: 如何在局域网内其他设备访问？**

A: 启动后服务默认监听 `0.0.0.0:5000`，同一局域网内其他设备访问 `http://<你的IP>:5000` 即可。终端启动时会显示局域网 IP。

**Q: 调用接口后一直没返回怎么办？**

A: 接口会阻塞等待人工回复，最长等待超时时间（默认 120 秒）。请到 WebUI 中查看是否有新消息并及时回复。

**Q: 如何修改端口？**

A: 通过 API 修改：

```bash
curl -X POST http://127.0.0.1:5000/api/admin/config \
  -H "Content-Type: application/json" \
  -d '{"port": 8080}'
```

修改后需重启服务生效。

**Q: 支持哪些 model 参数？**

A: model 参数可以是任意字符串，不影响功能。它只是透传显示在 WebUI 中，方便你区分不同调用来源。

---

## 许可证

MIT License
