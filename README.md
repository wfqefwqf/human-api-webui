---
title: Human-API WebUI
emoji: 🤖
colorFrom: blue
colorTo: purple
sdk: docker
app_port: 7860
pinned: true
license: agpl-3.0
---

# Human-API WebUI

> 把你当做 API — 外部程序调用 Chat 接口，你在 WebUI 管理后台人工回复冒充 AI

**仅供学习与娱乐，请勿用于非法用途！**

---

## 功能特性

- **OpenAI 兼容接口** — `/v1/chat/completions`，支持流式/非流式响应
- **WebUI 管理后台** — Claude 风格暖色 UI，QQ 风格气泡对话
- **WebSocket 实时推送** — 新消息即时通知，无需刷新页面
- **会话管理** — 自动分配会话 ID，保存完整聊天上下文
- **会话去重** — 相同模型相同提问自动复用已有会话
- **AI 自动回复** — 接入 OpenAI 兼容 API，可一键 AI 回复
- **AI 自动托管** — 开启后新消息自动调用 AI 回复，无需人工干预
- **心跳自动回复** — 匹配关键词（如 `hi`, `ping`）自动原样回复，不进入会话列表
- **超时自动回复** — 可自定义超时时间和默认回复内容
- **接口鉴权** — 后台设置 API 密钥，非法请求直接拦截
- **一键清理会话** — 清空所有屏幕上的会话
- **并发处理** — 多线程 + Event 机制，支持多个请求同时等待回复
- **完整日志** — 控制台 + 文件双输出，按日期自动分割
- **一键部署** — 支持 Docker / HuggingFace Spaces 一键部署

---

## 项目结构

```
human-api/
├── app.py              # 后端主服务（Flask + SocketIO + AI 集成）
├── config.py           # 配置管理模块（JSON 持久化 + AI/心跳配置）
├── human_api.py        # 原始项目文件（保留）
├── requirements.txt    # Python 依赖
├── Dockerfile          # Docker 容器化部署配置
├── start.bat           # Windows 一键启动脚本
├── start.sh            # Linux/Mac 一键启动脚本
├── static/
│   ├── index.html      # WebUI 管理页面
│   ├── css/
│   │   └── style.css   # 样式文件（Claude 风格暖色主题）
│   └── js/
│       └── app.js      # 前端逻辑
├── data/               # 配置持久化目录（自动生成）
│   └── config.json     # 运行时配置文件
└── logs/               # 日志目录（自动生成）
    └── human-api-*.log # 按日期分割的日志文件
```

---

## 快速开始

### 方式一：本地运行

#### 1. 安装依赖

```bash
# Python 版本要求：3.8+
pip install -r requirements.txt
```

所需依赖：
- `flask` — Web 框架
- `flask-cors` — 跨域支持
- `flask-socketio` — WebSocket 支持
- `requests` — HTTP 客户端（AI 接口调用）

#### 2. 启动服务

```bash
python app.py
```

或者使用一键启动脚本：
- Windows：双击 `start.bat`
- Linux/Mac：`bash start.sh`

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

#### 3. 打开 WebUI

浏览器访问 **http://127.0.0.1:5000** 即可进入管理后台。

局域网其他设备访问：`http://<你的IP>:5000`

---

### 方式二：Docker 部署

#### 1. 构建镜像

```bash
git clone https://github.com/wfqefwqf/human-api-webui.git
cd human-api-webui
docker build -t human-api-webui .
```

#### 2. 运行容器

```bash
docker run -d -p 7860:7860 --name human-api human-api-webui
```

浏览器访问 **http://127.0.0.1:7860** 即可。

#### 3. 持久化数据（可选）

挂载本地目录以保存配置和日志：

```bash
docker run -d -p 7860:7860 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/logs:/app/logs \
  --name human-api human-api-webui
```

---

### 方式三：HuggingFace Spaces 一键部署

> 这是最简单的部署方式，无需服务器、无需 Docker，完全免费。

#### 第一步：注册 HuggingFace 账号

1. 访问 [https://huggingface.co/join](https://huggingface.co/join) 注册账号
2. 注册完成后登录

#### 第二步：创建 Access Token

1. 点击右上角头像 → **Settings** → **Access Tokens**
2. 点击 **Create new token**
3. 名称随便填，权限选择 **Write**
4. 点击 **Create token**，复制生成的 token（格式：`hf_xxxx`）

#### 第三步：Fork 仓库

1. 访问 [https://github.com/wfqefwqf/human-api-webui](https://github.com/wfqefwqf/human-api-webui)
2. 点击右上角 **Fork** 按钮，将仓库 fork 到你自己的 GitHub 账号下

#### 第四步：在 HuggingFace 创建 Space

1. 访问 [https://huggingface.co/new-space](https://huggingface.co/new-space)
2. 填写 Space 名称，例如 `human-api-webui`
3. **Space SDK** 选择 **Docker**
4. **Docker Template** 选择 **Blank**
5. **License** 选择 **agpl-3.0**
6. 点击 **Create Space**

#### 第五步：推送代码到 Space

在你 fork 的仓库目录下执行：

```bash
# 添加 HuggingFace Space 作为远程仓库
git remote add hf https://<你的HF用户名>:<你的HF token>@huggingface.co/spaces/<你的HF用户名>/human-api-webui

# 推送代码
git push hf main
```

**完整示例**（假设你的 HF 用户名是 `myname`，token 是 `hf_xxxx`）：

```bash
git remote add hf https://myname:hf_xxxx@huggingface.co/spaces/myname/human-api-webui
git push hf main
```

#### 第六步：等待构建完成

1. 推送后 HuggingFace 会自动开始构建 Docker 镜像
2. 在 Space 页面可以看到构建进度和日志
3. 构建通常需要 2-5 分钟
4. 当日志显示 `Running on http://0.0.0.0:7860` 时，说明构建完成

#### 第七步：访问你的服务

- **WebUI 管理后台**：`https://<你的HF用户名>-human-api-webui.hf.space`
- **API 端点**：`https://<你的HF用户名>-human-api-webui.hf.space/v1/chat/completions`

#### 后续更新

当你修改了代码并想更新 HuggingFace 上的服务：

```bash
git add .
git commit -m "你的更新说明"
git push origin main    # 推送到 GitHub
git push hf main        # 推送到 HuggingFace（自动重新构建）
```

#### 常见问题

**Q: 构建失败怎么办？**
A: 在 Space 页面点击 **Logs** 查看构建日志，通常是因为依赖缺失或代码错误。

**Q: 如何自定义端口？**
A: HuggingFace Spaces 强制使用 7860 端口，无需修改。Dockerfile 中已配置 `ENV PORT=7860`。

**Q: 免费版有什么限制？**
A: HuggingFace Spaces 免费版使用 CPU 基础硬件，长时间无访问会自动休眠。再次访问时需要等待约 30 秒冷启动。

**Q: 如何设置环境变量？**
A: 在 Space 页面 → **Settings** → **Variables and secrets** 中添加。

---

### 方式四：Railway / Render 云平台部署

#### Railway

1. 访问 [https://railway.app](https://railway.app) 登录
2. 点击 **New Project** → **Deploy from GitHub repo**
3. 选择你 fork 的仓库
4. Railway 会自动检测 Dockerfile 并部署
5. 部署完成后会分配一个公网域名

#### Render

1. 访问 [https://render.com](https://render.com) 登录
2. 点击 **New** → **Web Service**
3. 选择你 fork 的仓库
4. 设置：
   - **Runtime**: Docker
   - **Port**: 7860
5. 点击 **Create Web Service**

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

**OpenAI SDK 示例（伪装成 OpenAI）：**

```python
from openai import OpenAI

client = OpenAI(
    api_key="anything",
    base_url="http://127.0.0.1:5000/v1"
)

resp = client.chat.completions.create(
    model="gpt-4",
    messages=[{"role": "user", "content": "你好"}]
)
print(resp.choices[0].message.content)
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

1. 打开浏览器访问管理后台
2. 左侧会话列表会**实时弹出**新消息
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
| **右侧设置面板** | API 密钥、超时时间、AI 配置、心跳配置、清空历史 |

### 系统设置

#### 基础设置
- **API 访问密钥** — 设置后外部调用必须携带密钥（Bearer Token）
- **回复超时时间** — 超过此时间未回复，自动返回默认回复（最小 10 秒）
- **超时默认回复** — 超时时自动返回给调用方的内容
- **一键清理会话** — 清空所有屏幕上的会话

#### AI 自动回复设置
- **启用 AI 自动回复** — 在回复区可选择「AI 回复」按钮
- **AI 自动托管** — 开启后新消息自动调用 AI 回复，无需人工干预
- **AI API 地址** — OpenAI 兼容格式的 API 端点
- **AI API 密钥** — AI 服务的 API 密钥
- **AI 模型名称** — 使用的模型，如 `gpt-4o`
- **AI 系统提示词** — AI 的角色设定

#### 心跳自动回复设置
- **启用心跳自动回复** — 匹配关键词的请求自动原样回复
- **心跳关键词** — 逗号分隔的关键词列表，如 `hi, hello, ping, test, 你好`

---

## API 接口文档

### Chat Completions（核心接口）

兼容 OpenAI 格式，外部程序调用此接口发送消息。

```
POST /v1/chat/completions
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

返回 500+ 个模型名称（包含 OpenAI、Claude、Gemini、DeepSeek、通义千问、豆包、GLM、文心、Kimi 等主流 AI 大模型）。

### 管理后台 API

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/admin/sessions` | GET | 获取所有会话列表 |
| `/api/admin/sessions/<id>` | GET | 获取单个会话详情 |
| `/api/admin/sessions/<id>/messages` | GET | 获取会话消息历史 |
| `/api/admin/reply` | POST | 提交人工回复 `{"session_id":"xxx","content":"回复内容"}` |
| `/api/admin/ai_reply` | POST | 触发 AI 自动回复 `{"session_id":"xxx"}` |
| `/api/admin/stats` | GET | 获取统计数据 |
| `/api/admin/config` | GET | 获取当前配置 |
| `/api/admin/config` | POST | 更新配置 |
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
curl -X POST http://127.0.0.1:5000/v1/chat/completions \
  -H "Authorization: Bearer my-secret-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4","messages":[{"role":"user","content":"你好"}]}'
```

---

## AI 自动回复配置

### 手动 AI 回复

1. 在 WebUI 设置面板中配置 AI API 信息：
   - AI API 地址：如 `https://api.openai.com/v1/chat/completions`
   - AI API 密钥：你的 API Key
   - AI 模型名称：如 `gpt-4o`
2. 在会话回复区点击「AI 回复」按钮
3. AI 会自动生成回复并发送给调用方

### 自动 AI 托管

1. 在设置面板中启用「AI 自动托管」
2. 配置好 AI API 信息
3. 之后所有新消息会自动调用 AI API 生成回复
4. 无需人工干预，完全自动化

支持任何 OpenAI 兼容格式的 API，包括：
- OpenAI API
- DeepSeek API
- 通义千问 API
- 本地部署的 Ollama / vLLM / LocalAI 等

---

## 常见问题

**Q: 如何在局域网内其他设备访问？**

A: 启动后服务默认监听 `0.0.0.0:5000`，同一局域网内其他设备访问 `http://<你的IP>:5000` 即可。

**Q: 调用接口后一直没返回怎么办？**

A: 接口会阻塞等待人工回复，最长等待超时时间（默认 120 秒）。请到 WebUI 中查看是否有新消息并及时回复。如果开启了 AI 自动托管，新消息会自动回复。

**Q: 如何修改端口？**

A: 通过 API 修改：

```bash
curl -X POST http://127.0.0.1:5000/api/admin/config \
  -H "Content-Type: application/json" \
  -d '{"port": 8080}'
```

修改后需重启服务生效。Docker 部署时可通过 `ENV PORT=xxxx` 修改。

**Q: 支持哪些 model 参数？**

A: model 参数可以是任意字符串，不影响功能。它只是透传显示在 WebUI 中，方便你区分不同调用来源。`/v1/models` 返回 500+ 个主流 AI 模型名称供选择。

**Q: 心跳自动回复是什么？**

A: 应对各种心跳检测场景。当收到匹配关键词的请求（如 `hi`、`ping`）时，自动原样回复，不创建会话，避免刷屏。

**Q: HuggingFace Spaces 免费版会休眠吗？**

A: 会的。长时间无访问时 Space 会自动休眠，再次访问需要约 30 秒冷启动。可以通过设置面板中的 AI 自动托管功能定期发请求保持活跃，或升级到付费版。

**Q: 如何同步 GitHub 和 HuggingFace 的代码？**

A: 每次修改代码后同时推送到两个远程仓库：

```bash
git push origin main    # 推送到 GitHub
git push hf main        # 推送到 HuggingFace
```

---

## 许可证

AGPL-3.0 License

详见 [LICENSE](LICENSE) 文件。
