# -*- coding: utf-8 -*-
"""
Human-API 后端主服务

核心功能：
- 兼容 OpenAI Chat Completions 格式的 /v1/chat/completions 接口
- 外部程序发送消息 -> 后端推送到 WebUI 管理界面 -> 管理员人工回复 -> 原路返回给调用方
- WebSocket 实时推送新消息到管理后台
- 会话管理、超时处理、接口鉴权、错误日志

启动方式：python app.py
访问 WebUI：http://127.0.0.1:5000
"""

import json
import time
import logging
import os
import uuid
from datetime import datetime
from threading import Lock

from flask import Flask, request, jsonify, Response, send_from_directory
from flask_socketio import SocketIO, emit
import requests as http_requests

import config as cfg

# ==================== 日志配置 ====================
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            os.path.join(LOG_DIR, f"human-api-{datetime.now():%Y-%m-%d}.log"),
            encoding="utf-8"
        ),
    ],
)
logger = logging.getLogger("human-api")

# ==================== Flask 应用初始化 ====================
app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["SECRET_KEY"] = os.urandom(24).hex()

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# ==================== 会话存储 ====================
# sessions 结构：
# {
#     "session-uuid": {
#         "id": "session-uuid",
#         "model": "gpt-4",
#         "messages": [...],           # 完整消息历史（OpenAI 格式）
#         "status": "waiting" | "replied" | "timeout",
#         "pending_message": {...},    # 当前等待回复的消息
#         "created_at": "ISO 时间",
#         "updated_at": "ISO 时间",
#         "request_event": threading.Event(),  # 用于阻塞等待回复
#         "reply_content": None,       # 人工回复内容
#     }
# }
sessions = {}
sessions_lock = Lock()

# 待处理消息队列 ID 列表（只包含 status == "waiting" 的）
pending_queue = []
pending_lock = Lock()


def get_pending_count():
    """获取待处理消息数量"""
    with pending_lock:
        return len(pending_queue)


def add_to_pending(session_id):
    """将会话加入待处理队列"""
    with pending_lock:
        if session_id not in pending_queue:
            pending_queue.append(session_id)


def remove_from_pending(session_id):
    """从待处理队列移除"""
    with pending_lock:
        if session_id in pending_queue:
            pending_queue.remove(session_id)


# ==================== 辅助函数 ====================
def generate_session_id():
    """生成唯一会话 ID"""
    return f"sess-{uuid.uuid4().hex[:12]}"


def now_iso():
    """返回当前 ISO 时间字符串"""
    return datetime.now().isoformat(timespec="seconds")


def cleanup_expired_sessions(max_age_hours=24):
    """清理超过指定时间的已结束会话（replied/timeout）"""
    now = datetime.now()
    to_remove = []
    with sessions_lock:
        for sid, s in sessions.items():
            if s["status"] in ("replied", "timeout"):
                try:
                    updated = datetime.fromisoformat(s.get("updated_at", s.get("created_at", "")))
                    if (now - updated).total_seconds() > max_age_hours * 3600:
                        to_remove.append(sid)
                except (ValueError, TypeError):
                    pass
        for sid in to_remove:
            del sessions[sid]
            remove_from_pending(sid)
    if to_remove:
        logger.info(f"[清理] 已清理 {len(to_remove)} 个过期会话")


def build_openai_response(session_id, model, content, stream=False):
    """构建 OpenAI 格式的响应数据"""
    if stream:
        return {
            "id": f"chatcmpl-{session_id}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {"content": content},
                "finish_reason": None
            }]
        }
    return {
        "id": f"chatcmpl-{session_id}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop"
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    }


def serialize_session(session):
    """将会话数据序列化为可 JSON 传输的格式"""
    return {
        "id": session["id"],
        "model": session["model"],
        "messages": session["messages"],
        "status": session["status"],
        "created_at": session["created_at"],
        "updated_at": session["updated_at"],
        "message_count": len(session["messages"]),
    }


def call_ai_api(messages, model_override=None):
    """
    调用 OpenAI 兼容 API 获取 AI 回复。

    Args:
        messages: OpenAI 格式的消息列表
        model_override: 可选，覆盖配置中的模型名称

    Returns:
        (success: bool, content: str) 元组
    """
    api_url = cfg.get("ai_api_url", "")
    api_key = cfg.get("ai_api_key", "")
    ai_model = model_override or cfg.get("ai_model", "")
    system_prompt = cfg.get("ai_system_prompt", "")

    if not api_url:
        return False, "未配置 AI API 地址"

    req_messages = []
    if system_prompt:
        req_messages.append({"role": "system", "content": system_prompt})
    req_messages.extend(messages)

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": ai_model,
        "messages": req_messages,
        "stream": False,
    }

    try:
        resp = http_requests.post(api_url, headers=headers, json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()

        choices = data.get("choices", [])
        if choices and choices[0].get("message"):
            content = choices[0]["message"].get("content", "")
            if content:
                return True, content

        return False, "AI 返回内容为空"
    except http_requests.exceptions.Timeout:
        return False, "AI API 请求超时"
    except http_requests.exceptions.ConnectionError:
        return False, "无法连接 AI API 服务"
    except Exception as e:
        return False, f"AI API 调用失败: {str(e)}"


def _auto_host_thread(session_id):
    """后台线程：自动调用 AI API 回复指定会话"""
    import threading

    def _do_auto_host():
        with sessions_lock:
            session = sessions.get(session_id)
            if not session or session["status"] != "waiting":
                return
            messages_copy = list(session["messages"])

        logger.info(f"[AI自动托管] 会话 {session_id} | 正在调用 AI API...")

        success, ai_content = call_ai_api(messages_copy)

        if not success:
            logger.error(f"[AI自动托管失败] 会话 {session_id} | {ai_content}")
            return

        with sessions_lock:
            session = sessions.get(session_id)
            if not session or session["status"] != "waiting":
                return
            session["reply_content"] = ai_content
            session["status"] = "replied"
            session["messages"].append({"role": "assistant", "content": ai_content})
            session["updated_at"] = now_iso()
            session["request_event"].set()

        remove_from_pending(session_id)
        logger.info(f"[AI自动托管成功] 会话 {session_id} | 回复: {ai_content[:80]}...")
        socketio.emit("session_updated", serialize_session(sessions.get(session_id)))

    t = threading.Thread(target=_do_auto_host, daemon=True)
    t.start()


# ==================== 鉴权中间层 ====================
def check_api_key():
    """
    检查请求是否携带正确的 API 密钥。
    如果未配置密钥则放行所有请求。
    """
    required_key = cfg.get("api_key", "")
    if not required_key:
        return None

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
        if token == required_key:
            return None

    body_key = None
    if request.is_json:
        body_key = (request.json or {}).get("api_key")

    query_key = request.args.get("api_key")

    if body_key == required_key or query_key == required_key:
        return None

    return jsonify({"error": {"message": "Invalid API key", "type": "auth_error", "code": "invalid_key"}}), 401


# ==================== WebUI 页面路由 ====================
@app.route("/")
def index():
    """返回 WebUI 管理页面"""
    return send_from_directory("static", "index.html")


# ==================== OpenAI 兼容 API ====================
@app.route("/v1/chat/completions", methods=["POST", "OPTIONS"])
def chat_completions():
    """
    OpenAI 兼容的 Chat Completions 接口。
    外部程序调用此接口发送消息，后端等待人工回复后返回。
    """
    if request.method == "OPTIONS":
        return "", 200

    # 鉴权
    auth_err = check_api_key()
    if auth_err:
        return auth_err

    # 参数校验
    try:
        data = request.json
        if not data:
            return jsonify({"error": {"message": "请求体为空", "type": "invalid_request"}}), 400
    except Exception:
        return jsonify({"error": {"message": "无效的 JSON 格式", "type": "invalid_request"}}), 400

    model = data.get("model", "gpt-3.5-turbo")
    messages = data.get("messages", [])
    stream = data.get("stream", False)

    if not isinstance(messages, list) or len(messages) == 0:
        return jsonify({"error": {"message": "messages 不能为空", "type": "invalid_request"}}), 400

    # 从消息中提取客户端标识（可选，用最后一条 user 消息作为提示）
    user_query = ""
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "user":
            user_query = msg.get("content", "")
            break

    # 心跳自动回复：匹配关键词则直接返回，不创建会话
    if cfg.get("heartbeat_enabled", True):
        patterns = cfg.get("heartbeat_patterns", [])
        if user_query.strip().lower() in [p.lower() for p in patterns]:
            logger.info(f"[心跳] 自动回复: {user_query}")
            heartbeat_reply = user_query.strip()
            if stream:
                def generate_heartbeat():
                    yield f"data: {json.dumps(build_openai_response('heartbeat', model, heartbeat_reply, stream=True))}\n\n"
                    end_chunk = {"id": "chatcmpl-heartbeat", "object": "chat.completion.chunk", "created": int(time.time()), "model": model, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
                    yield f"data: {json.dumps(end_chunk)}\n\n"
                    yield "data: [DONE]\n\n"
                return Response(generate_heartbeat(), mimetype="text/event-stream")
            return jsonify(build_openai_response("heartbeat", model, heartbeat_reply))

    # 去重：如果已有相同模型+相同提问的等待中会话，直接复用
    with sessions_lock:
        for sid, s in sessions.items():
            if s["status"] == "waiting" and s["model"] == model:
                existing_query = ""
                for msg in reversed(s.get("messages", [])):
                    if isinstance(msg, dict) and msg.get("role") == "user":
                        existing_query = msg.get("content", "")
                        break
                if existing_query == user_query:
                    logger.info(f"[去重] 复用会话 {sid} | 模型: {model} | 提问: {user_query[:80]}")
                    event = s["request_event"]
                    session_id = sid
                    break
        else:
            session_id = generate_session_id()
            event = __import__("threading").Event()

            sessions[session_id] = {
                "id": session_id,
                "model": model,
                "messages": list(messages),
                "status": "waiting",
                "pending_message": messages[-1] if messages else {},
                "created_at": now_iso(),
                "updated_at": now_iso(),
                "request_event": event,
                "reply_content": None,
            }

            add_to_pending(session_id)
            logger.info(f"[新请求] 会话 {session_id} | 模型: {model} | 提问: {user_query[:80]}...")

            socketio.emit("new_request", {
                "session": serialize_session(sessions[session_id]),
                "query_preview": user_query[:200],
            })

            if cfg.get("ai_auto_host", False):
                _auto_host_thread(session_id)

    # 等待人工回复或超时
    timeout = cfg.get("timeout", 120)
    replied = event.wait(timeout=timeout)

    with sessions_lock:
        session = sessions.get(session_id)
        if not session:
            answer = cfg.get("timeout_reply", "抱歉，当前人工客服繁忙，请稍后再试。")
            remove_from_pending(session_id)
            logger.warning(f"[会话丢失] 会话 {session_id} 已被清理，返回超时回复")
            if stream:
                def generate_fallback():
                    yield f"data: {json.dumps(build_openai_response(session_id, model, answer, stream=True))}\n\n"
                    end_chunk = {"id": f"chatcmpl-{session_id}", "object": "chat.completion.chunk", "created": int(time.time()), "model": model, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
                    yield f"data: {json.dumps(end_chunk)}\n\n"
                    yield "data: [DONE]\n\n"
                return Response(generate_fallback(), mimetype="text/event-stream")
            return jsonify(build_openai_response(session_id, model, answer))

        if replied and session["reply_content"]:
            answer = session["reply_content"]
            session["status"] = "replied"
            session["messages"].append({"role": "assistant", "content": answer})
            session["updated_at"] = now_iso()
        else:
            answer = cfg.get("timeout_reply", "抱歉，当前人工客服繁忙，请稍后再试。")
            session["status"] = "timeout"
            session["messages"].append({"role": "assistant", "content": answer})
            session["updated_at"] = now_iso()
            logger.warning(f"[超时] 会话 {session_id} 超时未回复")

    remove_from_pending(session_id)

    # WebSocket 通知前端会话状态变更
    socketio.emit("session_updated", serialize_session(session))

    logger.info(f"[回复] 会话 {session_id} | 回复: {answer[:80]}...")

    # 返回 OpenAI 格式响应
    if stream:
        def generate():
            chunk_start = {
                "id": f"chatcmpl-{session_id}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]
            }
            yield f"data: {json.dumps(chunk_start)}\n\n"
            yield f"data: {json.dumps(build_openai_response(session_id, model, answer, stream=True))}\n\n"
            end_chunk = {
                "id": f"chatcmpl-{session_id}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
            }
            yield f"data: {json.dumps(end_chunk)}\n\n"
            yield "data: [DONE]\n\n"

        return Response(generate(), mimetype="text/event-stream")

    return jsonify(build_openai_response(session_id, model, answer))


@app.route("/v1/models", methods=["GET"])
def list_models():
    """返回可用模型列表（兼容 OpenAI 格式）"""
    return jsonify({
        "object": "list",
        "data": [
            # ========== OpenAI ==========
            {"id": "gpt-5.5", "object": "model"},
            {"id": "gpt-5.4", "object": "model"},
            {"id": "gpt-5.3", "object": "model"},
            {"id": "gpt-5.2", "object": "model"},
            {"id": "gpt-5.1", "object": "model"},
            {"id": "gpt-5", "object": "model"},
            {"id": "gpt-5-pro", "object": "model"},
            {"id": "gpt-4o", "object": "model"},
            {"id": "gpt-4-turbo", "object": "model"},
            {"id": "gpt-4", "object": "model"},
            {"id": "gpt-3.5-turbo", "object": "model"},
            {"id": "o3", "object": "model"},
            {"id": "o3-mini", "object": "model"},
            {"id": "o4-mini", "object": "model"},
            {"id": "o1", "object": "model"},
            {"id": "o1-mini", "object": "model"},
            {"id": "deep-research", "object": "model"},

            # ========== Anthropic Claude ==========
            {"id": "claude-opus-4.6", "object": "model"},
            {"id": "claude-sonnet-4.6", "object": "model"},
            {"id": "claude-haiku-4.5", "object": "model"},
            {"id": "claude-3-opus", "object": "model"},
            {"id": "claude-3-sonnet", "object": "model"},
            {"id": "claude-3-haiku", "object": "model"},
            {"id": "claude-3.5-sonnet", "object": "model"},
            {"id": "claude-3.7-sonnet", "object": "model"},

            # ========== Google Gemini ==========
            {"id": "gemini-3.1-pro", "object": "model"},
            {"id": "gemini-3.1-flash-lite", "object": "model"},
            {"id": "gemini-3-pro", "object": "model"},
            {"id": "gemini-3-flash", "object": "model"},
            {"id": "gemini-2.0-pro", "object": "model"},
            {"id": "gemini-2.0-flash", "object": "model"},
            {"id": "gemini-1.5-pro", "object": "model"},
            {"id": "gemini-1.5-flash", "object": "model"},
            {"id": "gemini-ultra", "object": "model"},
            {"id": "gemini-pro", "object": "model"},
            {"id": "gemini-flash", "object": "model"},
            {"id": "gemini-nano", "object": "model"},
            {"id": "gemma-2-27b", "object": "model"},
            {"id": "gemma-2-9b", "object": "model"},
            {"id": "gemma-2-2b", "object": "model"},

            # ========== Meta Llama ==========
            {"id": "llama-4", "object": "model"},
            {"id": "llama-3.3-70b", "object": "model"},
            {"id": "llama-3.2", "object": "model"},
            {"id": "llama-3.1-405b", "object": "model"},
            {"id": "llama-3.1-70b", "object": "model"},
            {"id": "llama-3.1-8b", "object": "model"},
            {"id": "llama-3-70b", "object": "model"},
            {"id": "llama-3-8b", "object": "model"},
            {"id": "llama-guard-3", "object": "model"},
            {"id": "mobile-llama", "object": "model"},

            # ========== DeepSeek ==========
            {"id": "deepseek-v4", "object": "model"},
            {"id": "deepseek-v3.2", "object": "model"},
            {"id": "deepseek-v3", "object": "model"},
            {"id": "deepseek-r1", "object": "model"},
            {"id": "deepseek-r1-0528", "object": "model"},
            {"id": "deepseek-chat", "object": "model"},
            {"id": "deepseek-coder", "object": "model"},

            # ========== 阿里巴巴 通义千问 ==========
            {"id": "qwen-3.5", "object": "model"},
            {"id": "qwen-3.5-max", "object": "model"},
            {"id": "qwen-3.5-plus", "object": "model"},
            {"id": "qwen-3.5-turbo", "object": "model"},
            {"id": "qwen-3.5-397b", "object": "model"},
            {"id": "qwen-3-max", "object": "model"},
            {"id": "qwen-3-plus", "object": "model"},
            {"id": "qwen-3-turbo", "object": "model"},
            {"id": "qwen-3-embedding", "object": "model"},
            {"id": "qwen-2.5-72b", "object": "model"},
            {"id": "qwen-2.5-32b", "object": "model"},
            {"id": "qwen-2.5-14b", "object": "model"},
            {"id": "qwen-2.5-7b", "object": "model"},
            {"id": "qwen-2.5-3b", "object": "model"},
            {"id": "qwen-2.5-1.5b", "object": "model"},
            {"id": "qwen-2.5-0.5b", "object": "model"},
            {"id": "qwen-2.5-coder", "object": "model"},
            {"id": "qwen-3-coder-next", "object": "model"},
            {"id": "qwen-3-coder", "object": "model"},
            {"id": "qwen-max", "object": "model"},
            {"id": "qwen-plus", "object": "model"},
            {"id": "qwen-turbo", "object": "model"},
            {"id": "qwen-omni", "object": "model"},

            # ========== 字节跳动 豆包 ==========
            {"id": "doubao-2.0", "object": "model"},
            {"id": "doubao-pro-256k", "object": "model"},
            {"id": "doubao-pro-32k", "object": "model"},
            {"id": "doubao-lite-32k", "object": "model"},
            {"id": "seedance-2.0", "object": "model"},
            {"id": "bitdance", "object": "model"},
            {"id": "concept-moe", "object": "model"},
            {"id": "doubao-vision", "object": "model"},
            {"id": "doubao-embedding", "object": "model"},
            {"id": "doubao-realtime", "object": "model"},
            {"id": "doubao-voice", "object": "model"},
            {"id": "x-agents", "object": "model"},
            {"id": "seed-music", "object": "model"},
            {"id": "seed-video", "object": "model"},
            {"id": "seed-tts", "object": "model"},
            {"id": "seed-ocr", "object": "model"},

            # ========== 智谱 GLM ==========
            {"id": "glm-5.1", "object": "model"},
            {"id": "glm-5", "object": "model"},
            {"id": "glm-4.7", "object": "model"},
            {"id": "glm-4-plus", "object": "model"},
            {"id": "glm-4-long", "object": "model"},
            {"id": "glm-4-flash", "object": "model"},
            {"id": "glm-4-air", "object": "model"},
            {"id": "glm-4v-plus", "object": "model"},
            {"id": "glm-z1-air", "object": "model"},
            {"id": "glm-3-turbo", "object": "model"},
            {"id": "glm-ocr", "object": "model"},
            {"id": "z-code", "object": "model"},

            # ========== 百度 文心 ==========
            {"id": "wenxin-5.0", "object": "model"},
            {"id": "wenxin-4.0-turbo", "object": "model"},
            {"id": "wenxin-4.0", "object": "model"},
            {"id": "wenxin-3.5", "object": "model"},
            {"id": "wenxin-lite", "object": "model"},
            {"id": "wenxin-embeddings", "object": "model"},
            {"id": "wenxin-ocr", "object": "model"},
            {"id": "wenxin-speech", "object": "model"},

            # ========== 月之暗面 Kimi ==========
            {"id": "kimi-k2.5", "object": "model"},
            {"id": "kimi-v2", "object": "model"},
            {"id": "moonshot-v1-128k", "object": "model"},
            {"id": "moonshot-v1-32k", "object": "model"},
            {"id": "moonshot-v1-8k", "object": "model"},
            {"id": "simple-seg", "object": "model"},
            {"id": "kimi-vision", "object": "model"},
            {"id": "kimi-voice", "object": "model"},

            # ========== MiniMax (稀宇科技) ==========
            {"id": "minimax-m2.7", "object": "model"},
            {"id": "minimax-m2.5", "object": "model"},
            {"id": "abab-7-chat", "object": "model"},
            {"id": "abab-6.5-chat", "object": "model"},
            {"id": "abab-5.5-chat", "object": "model"},
            {"id": "minimax-speech", "object": "model"},
            {"id": "minimax-voice-clone", "object": "model"},
            {"id": "minimax-video", "object": "model"},
            {"id": "minimax-embedding", "object": "model"},

            # ========== 零一万物 Yi ==========
            {"id": "yi-large-2", "object": "model"},
            {"id": "yi-large", "object": "model"},
            {"id": "yi-medium", "object": "model"},
            {"id": "yi-spark", "object": "model"},
            {"id": "yi-vision", "object": "model"},
            {"id": "yi-34b", "object": "model"},
            {"id": "yi-6b", "object": "model"},

            # ========== 腾讯 混元 ==========
            {"id": "hunyuan-hy3-preview", "object": "model"},
            {"id": "hunyuan-3.0", "object": "model"},
            {"id": "hunyuan-pro", "object": "model"},
            {"id": "hunyuan-standard", "object": "model"},
            {"id": "hunyuan-lite", "object": "model"},
            {"id": "hunyuan-1.8b-2bit", "object": "model"},
            {"id": "hpc-ops", "object": "model"},

            # ========== 科大讯飞 星火 ==========
            {"id": "spark-x2-flash", "object": "model"},
            {"id": "spark-x2", "object": "model"},
            {"id": "spark-v4.0", "object": "model"},
            {"id": "spark-v3.5", "object": "model"},
            {"id": "spark-lite", "object": "model"},
            {"id": "spark-tts", "object": "model"},
            {"id": "spark-ocr", "object": "model"},
            {"id": "spark-review", "object": "model"},

            # ========== 360 智脑 ==========
            {"id": "360gpt-pro", "object": "model"},
            {"id": "360gpt-turbo", "object": "model"},
            {"id": "360gpt", "object": "model"},

            # ========== 商汤 日日新 ==========
            {"id": "sensetime-nova", "object": "model"},
            {"id": "sensetime-pro", "object": "model"},
            {"id": "sensetime-lite", "object": "model"},
            {"id": "sensetime-vision", "object": "model"},
            {"id": "sensetime-vega", "object": "model"},

            # ========== 昆仑万维 Skywork ==========
            {"id": "skywork-13b", "object": "model"},
            {"id": "skywork-7b", "object": "model"},
            {"id": "skywork-3b", "object": "model"},
            {"id": "skywork-code", "object": "model"},
            {"id": "skywork-voice", "object": "model"},

            # ========== 小米 Mimo ==========
            {"id": "mimo-v2-flash-0204", "object": "model"},
            {"id": "mimo-v2", "object": "model"},
            {"id": "mimo-embed", "object": "model"},
            {"id": "mimo-vision", "object": "model"},
            {"id": "mimo-voice", "object": "model"},
            {"id": "mimo-agent", "object": "model"},
            {"id": "mimo-code", "object": "model"},
            {"id": "mimo-translate", "object": "model"},
            {"id": "mimo-summary", "object": "model"},
            {"id": "mimo-recommend", "object": "model"},
            {"id": "mimo-search", "object": "model"},

            # ========== 阶跃星辰 Step ==========
            {"id": "step-3.5-flash", "object": "model"},
            {"id": "step-3.5", "object": "model"},
            {"id": "step-3", "object": "model"},
            {"id": "step-2", "object": "model"},
            {"id": "step-vision", "object": "model"},
            {"id": "step-audio", "object": "model"},

            # ========== 蚂蚁集团 Ant Group ==========
            {"id": "ling-2.5-1t", "object": "model"},
            {"id": "ring-2.5-1t", "object": "model"},
            {"id": "llada-2.1", "object": "model"},
            {"id": "ming-flash-omni-2.0", "object": "model"},
            {"id": "ming-omni-tts", "object": "model"},
            {"id": "ant-llm", "object": "model"},
            {"id": "ant-vision", "object": "model"},
            {"id": "ant-embedding", "object": "model"},
            {"id": "ant-rag", "object": "model"},

            # ========== 京东 JoyAI ==========
            {"id": "joyai-llm-flash", "object": "model"},
            {"id": "joyai-pro", "object": "model"},
            {"id": "joyai-lite", "object": "model"},
            {"id": "joyai-code", "object": "model"},
            {"id": "joyai-chat", "object": "model"},
            {"id": "joyai-vision", "object": "model"},
            {"id": "joyai-voice", "object": "model"},
            {"id": "joyai-embedding", "object": "model"},
            {"id": "joyai-agent", "object": "model"},
            {"id": "joyai-recommend", "object": "model"},
            {"id": "joyai-search", "object": "model"},
            {"id": "joyai-summary", "object": "model"},
            {"id": "joyai-translate", "object": "model"},
            {"id": "joyai-summary-agent", "object": "model"},

            # ========== 美团 LongCat ==========
            {"id": "longcat-flash-lite", "object": "model"},
            {"id": "longcat-pro", "object": "model"},
            {"id": "longcat-lite", "object": "model"},
            {"id": "longcat-chat", "object": "model"},
            {"id": "longcat-agent", "object": "model"},

            # ========== 小红书 FireRed ==========
            {"id": "rednote-fireasr-2s", "object": "model"},
            {"id": "rednote-image-edit", "object": "model"},
            {"id": "rednote-nlp", "object": "model"},
            {"id": "rednote-cv", "object": "model"},
            {"id": "rednote-voice", "object": "model"},
            {"id": "rednote-translate", "object": "model"},
            {"id": "rednote-summary", "object": "model"},
            {"id": "rednote-rag", "object": "model"},
            {"id": "rednote-agent", "object": "model"},
            {"id": "rednote-rec", "object": "model"},
            {"id": "rednote-chat", "object": "model"},
            {"id": "rednote-search", "object": "model"},
            {"id": "rednote-recommend", "object": "model"},
            {"id": "rednote-embedding", "object": "model"},
            {"id": "rednote-vision", "object": "model"},
            {"id": "rednote-metric", "object": "model"},
            {"id": "rednote-training", "object": "model"},
            {"id": "firefly-llm", "object": "model"},
            {"id": "firefly-embedding", "object": "model"},
            {"id": "firefly-search", "object": "model"},
            {"id": "firefly-recommend", "object": "model"},
            {"id": "firefly-nlp-base", "object": "model"},
            {"id": "firefly-cv-base", "object": "model"},
            {"id": "firefly-agent", "object": "model"},
            {"id": "firefly-chat", "object": "model"},

            # ========== 快手 Kling ==========
            {"id": "kling-3.0", "object": "model"},
            {"id": "kling-2.5", "object": "model"},
            {"id": "kling-pro", "object": "model"},
            {"id": "kling-standard", "object": "model"},
            {"id": "kling-vision", "object": "model"},
            {"id": "kling-audio", "object": "model"},
            {"id": "kling-embedding", "object": "model"},
            {"id": "kling-agent", "object": "model"},
            {"id": "kling-code", "object": "model"},
            {"id": "kling-translate", "object": "model"},
            {"id": "kling-summary", "object": "model"},
            {"id": "kling-recommend", "object": "model"},
            {"id": "kling-search", "object": "model"},
            {"id": "kwai-llm", "object": "model"},
            {"id": "kwai-chat", "object": "model"},

            # ========== 荣耀 Honor AI ==========
            {"id": "honor-ai", "object": "model"},
            {"id": "honor-vision", "object": "model"},
            {"id": "honor-voice", "object": "model"},
            {"id": "honor-embedding", "object": "model"},
            {"id": "honor-agent", "object": "model"},
            {"id": "honor-chat", "object": "model"},
            {"id": "honor-summary", "object": "model"},
            {"id": "honor-translate", "object": "model"},
            {"id": "honor-recommend", "object": "model"},
            {"id": "honor-search", "object": "model"},
            {"id": "honor-magic", "object": "model"},
            {"id": "honor-magic-pro", "object": "model"},
            {"id": "honor-magic-lite", "object": "model"},

            # ========== 中兴 ZTE AI ==========
            {"id": "zte-ai", "object": "model"},
            {"id": "zte-ai-pro", "object": "model"},
            {"id": "zte-ai-lite", "object": "model"},

            # ========== OPPO AI ==========
            {"id": "oppo-ai", "object": "model"},
            {"id": "oppo-ai-pro", "object": "model"},
            {"id": "oppo-ai-lite", "object": "model"},
            {"id": "oppo-ai-agent", "object": "model"},
            {"id": "oppo-ai-chat", "object": "model"},
            {"id": "oppo-ai-vision", "object": "model"},

            # ========== vivo AI ==========
            {"id": "vivo-ai", "object": "model"},
            {"id": "vivo-ai-pro", "object": "model"},
            {"id": "vivo-ai-lite", "object": "model"},
            {"id": "vivo-ai-chat", "object": "model"},
            {"id": "vivo-ai-agent", "object": "model"},
            {"id": "vivo-ai-vision", "object": "model"},
            {"id": "vivo-ai-voice", "object": "model"},
            {"id": "vivo-ai-embedding", "object": "model"},
            {"id": "vivo-ai-summary", "object": "model"},
            {"id": "vivo-ai-recommend", "object": "model"},
            {"id": "vivo-ai-search", "object": "model"},
            {"id": "vivo-ai-translate", "object": "model"},
            {"id": "vivo-ai-code", "object": "model"},
            {"id": "vivo-ai-agent-pro", "object": "model"},
            {"id": "vivo-ai-chat-pro", "object": "model"},
            {"id": "vivo-ai-vision-pro", "object": "model"},
            {"id": "vivo-ai-voice-pro", "object": "model"},
            {"id": "vivo-ai-embedding-pro", "object": "model"},
            {"id": "vivo-ai-summary-pro", "object": "model"},
            {"id": "vivo-ai-recommend-pro", "object": "model"},
            {"id": "vivo-ai-search-pro", "object": "model"},
            {"id": "vivo-ai-translate-pro", "object": "model"},
            {"id": "vivo-ai-code-pro", "object": "model"},

            # ========== xAI Grok ==========
            {"id": "grok-4.2", "object": "model"},
            {"id": "grok-4.1", "object": "model"},
            {"id": "grok-4", "object": "model"},
            {"id": "grok-3", "object": "model"},
            {"id": "grok-2.5", "object": "model"},
            {"id": "grok-2", "object": "model"},
            {"id": "grok-imagine-1.0", "object": "model"},
            {"id": "grok-vision", "object": "model"},
            {"id": "grok-audio", "object": "model"},
            {"id": "grok-embedding", "object": "model"},
            {"id": "grok-agent", "object": "model"},
            {"id": "grok-summary", "object": "model"},
            {"id": "grok-recommend", "object": "model"},
            {"id": "grok-search", "object": "model"},
            {"id": "grok-translate", "object": "model"},
            {"id": "grok-chat", "object": "model"},
            {"id": "grok-code", "object": "model"},
            {"id": "grok-reasoning", "object": "model"},
            {"id": "grok-multiagent", "object": "model"},
            {"id": "grok-pro", "object": "model"},
            {"id": "grok-lite", "object": "model"},
            {"id": "grok-realtime", "object": "model"},
            {"id": "grok-voice", "object": "model"},
            {"id": "grok-ocr", "object": "model"},
            {"id": "grok-rag", "object": "model"},
            {"id": "grok-funcall", "object": "model"},
            {"id": "grok-web", "object": "model"},

            # ========== Mistral AI ==========
            {"id": "mistral-large-3", "object": "model"},
            {"id": "mistral-large-2", "object": "model"},
            {"id": "mistral-medium", "object": "model"},
            {"id": "mistral-small-3", "object": "model"},
            {"id": "mistral-small", "object": "model"},
            {"id": "mixtral-8x22b", "object": "model"},
            {"id": "mixtral-8x7b", "object": "model"},
            {"id": "codestral", "object": "model"},
            {"id": "codestral-mamba", "object": "model"},
            {"id": "voxtral-mini-4b", "object": "model"},
            {"id": "voxtral", "object": "model"},
            {"id": "mathstral", "object": "model"},
            {"id": "pixtral", "object": "model"},
            {"id": "mistral-embed", "object": "model"},
            {"id": "mistral-vision", "object": "model"},
            {"id": "mistral-funcall", "object": "model"},
            {"id": "mistral-agent", "object": "model"},
            {"id": "mistral-rag", "object": "model"},
            {"id": "mistral-ocr", "object": "model"},
            {"id": "mistral-summary", "object": "model"},
            {"id": "mistral-search", "object": "model"},

            # ========== Cohere ==========
            {"id": "command-r-plus", "object": "model"},
            {"id": "command-r", "object": "model"},
            {"id": "command-a-vision", "object": "model"},
            {"id": "command", "object": "model"},
            {"id": "tiny-aya", "object": "model"},
            {"id": "cohere-embed", "object": "model"},
            {"id": "cohere-summary", "object": "model"},
            {"id": "cohere-generate", "object": "model"},
            {"id": "cohere-chat", "object": "model"},
            {"id": "cohere-classify", "object": "model"},
            {"id": "cohere-rag", "object": "model"},
            {"id": "cohere-agent", "object": "model"},
            {"id": "cohere-tool-use", "object": "model"},

            # ========== 其他国际模型 ==========
            {"id": "phi-4", "object": "model"},
            {"id": "phi-3-medium", "object": "model"},
            {"id": "phi-3-small", "object": "model"},
            {"id": "phi-3-mini", "object": "model"},
            {"id": "phi-2", "object": "model"},
            {"id": "jamba-1.5", "object": "model"},
            {"id": "jamba-1.5-large", "object": "model"},
            {"id": "jamba-1.5-mini", "object": "model"},
            {"id": "arcee-trinity-large", "object": "model"},
            {"id": "arcee-trinity-mini", "object": "model"},
            {"id": "arcee-trinity-nano", "object": "model"},
            {"id": "sarvam-30b", "object": "model"},
            {"id": "sarvam-105b", "object": "model"},
            {"id": "sera-14b", "object": "model"},
            {"id": "intern-s1-pro", "object": "model"},
            {"id": "intern-s1", "object": "model"},
            {"id": "ace-step-1.5", "object": "model"},
            {"id": "minicpm-o-4.5", "object": "model"},
            {"id": "minicpm-sala", "object": "model"},
            {"id": "minicpm-llama3", "object": "model"},
            {"id": "minicpm-moe", "object": "model"},
            {"id": "ovis2.6-30b", "object": "model"},
            {"id": "nanbeige-4.1-3b", "object": "model"},
            {"id": "nanbeige-4.1", "object": "model"},
            {"id": "lyria-3", "object": "model"},
            {"id": "lyria-2", "object": "model"},
            {"id": "soulx-singer", "object": "model"},
            {"id": "soulx", "object": "model"},
            {"id": "moss-tts", "object": "model"},
            {"id": "moss-agent", "object": "model"},
            {"id": "moss-rag", "object": "model"},
            {"id": "moss-embed", "object": "model"},
            {"id": "moss-classify", "object": "model"},
            {"id": "moss-generate", "object": "model"},
            {"id": "moss-summary", "object": "model"},
            {"id": "thinker", "object": "model"},
            {"id": "thinker-express", "object": "model"},
            {"id": "fantasyworld", "object": "model"},
            {"id": "fantasyworld-pro", "object": "model"},
            {"id": "fantasyworld-embed", "object": "model"},
            {"id": "fantasyworld-agent", "object": "model"},
            {"id": "fantasyworld-summary", "object": "model"},
            {"id": "fantasyworld-recommend", "object": "model"},
            {"id": "fantasyworld-search", "object": "model"},
            {"id": "fantasyworld-translate", "object": "model"},
            {"id": "fantasyworld-code", "object": "model"},
            {"id": "fantasyworld-chat", "object": "model"},
            {"id": "fantasyworld-vision", "object": "model"},
            {"id": "fantasyworld-audio", "object": "model"},
            {"id": "fantasyworld-video", "object": "model"},
            {"id": "fantasyworld-3d", "object": "model"},
            {"id": "fantasyworld-realtime", "object": "model"},

            # ========== 更多国内模型 ==========
            {"id": "qoder", "object": "model"},
            {"id": "qoder-plus", "object": "model"},
            {"id": "qoder-pro", "object": "model"},
            {"id": "qoder-lite", "object": "model"},
            {"id": "qoder-code", "object": "model"},
            {"id": "qoder-chat", "object": "model"},
            {"id": "qoder-vision", "object": "model"},
            {"id": "qoder-embed", "object": "model"},
            {"id": "qoder-summary", "object": "model"},
            {"id": "qoder-recommend", "object": "model"},
            {"id": "qoder-search", "object": "model"},
            {"id": "qoder-translate", "object": "model"},
            {"id": "qoder-agent", "object": "model"},
            {"id": "qoder-rag", "object": "model"},
            {"id": "qoder-funcall", "object": "model"},
            {"id": "qoder-web", "object": "model"},
            {"id": "qoder-operation", "object": "model"},
            {"id": "qoder-management", "object": "model"},
            {"id": "qoder-leadership", "object": "model"},
            {"id": "qoder-team", "object": "model"},
            {"id": "qoder-project", "object": "model"},
            {"id": "qoder-product", "object": "model"},
            {"id": "qoder-design", "object": "model"},
            {"id": "qoder-ux", "object": "model"},
            {"id": "qoder-ui", "object": "model"},
        ]
    })


@app.route("/api/chat", methods=["POST", "OPTIONS"])
def api_chat_compat():
    """
    兼容旧版 /api/chat 接口。
    内部转发到 /v1/chat/completions 逻辑。
    """
    if request.method == "OPTIONS":
        return "", 200
    return chat_completions()


# ==================== 管理后台 API ====================
@app.route("/api/admin/sessions", methods=["GET"])
def admin_list_sessions():
    """获取所有会话列表"""
    with sessions_lock:
        result = [serialize_session(s) for s in sessions.values()]
    result.sort(key=lambda x: x["created_at"], reverse=True)
    return jsonify({"sessions": result, "total": len(result), "pending": get_pending_count()})


@app.route("/api/admin/sessions/<session_id>", methods=["GET"])
def admin_get_session(session_id):
    """获取单个会话详情（含完整消息历史）"""
    with sessions_lock:
        session = sessions.get(session_id)
        if not session:
            return jsonify({"error": "会话不存在"}), 404
        return jsonify(serialize_session(session))


@app.route("/api/admin/sessions/<session_id>/messages", methods=["GET"])
def admin_get_messages(session_id):
    """获取会话的消息列表"""
    with sessions_lock:
        session = sessions.get(session_id)
        if not session:
            return jsonify({"error": "会话不存在"}), 404
        return jsonify({"messages": session["messages"], "session_id": session_id})


@app.route("/api/admin/reply", methods=["POST"])
def admin_reply():
    """
    管理员通过 WebUI 提交回复。
    请求体：{"session_id": "xxx", "content": "回复内容"}
    """
    try:
        data = request.json
        if not data:
            return jsonify({"error": "请求体为空"}), 400
    except Exception:
        return jsonify({"error": "无效的 JSON 格式"}), 400

    session_id = data.get("session_id", "")
    content = data.get("content", "").strip()

    if not session_id:
        return jsonify({"error": "session_id 不能为空"}), 400
    if not content:
        return jsonify({"error": "回复内容不能为空"}), 400

    with sessions_lock:
        session = sessions.get(session_id)
        if not session:
            return jsonify({"error": "会话不存在"}), 404
        if session["status"] != "waiting":
            return jsonify({"error": f"该会话状态为 {session['status']}，无法回复"}), 400

        session["reply_content"] = content
        session["status"] = "replied"
        session["updated_at"] = now_iso()
        session["request_event"].set()

    remove_from_pending(session_id)
    logger.info(f"[WebUI回复] 会话 {session_id} | 回复: {content[:80]}...")

    socketio.emit("session_updated", serialize_session(session))

    return jsonify({"success": True, "session_id": session_id})


@app.route("/api/admin/ai_reply", methods=["POST"])
def admin_ai_reply():
    """
    管理员通过 WebUI 触发 AI 自动回复。
    请求体：{"session_id": "xxx"}
    """
    try:
        data = request.json
        if not data:
            return jsonify({"error": "请求体为空"}), 400
    except Exception:
        return jsonify({"error": "无效的 JSON 格式"}), 400

    session_id = data.get("session_id", "")
    if not session_id:
        return jsonify({"error": "session_id 不能为空"}), 400

    with sessions_lock:
        session = sessions.get(session_id)
        if not session:
            return jsonify({"error": "会话不存在"}), 404
        if session["status"] != "waiting":
            return jsonify({"error": f"该会话状态为 {session['status']}，无法回复"}), 400
        messages_copy = list(session["messages"])

    logger.info(f"[AI回复] 会话 {session_id} | 正在调用 AI API...")

    success, ai_content = call_ai_api(messages_copy)

    if not success:
        logger.error(f"[AI回复失败] 会话 {session_id} | {ai_content}")
        return jsonify({"error": ai_content}), 500

    with sessions_lock:
        session = sessions.get(session_id)
        if not session or session["status"] != "waiting":
            return jsonify({"error": "会话状态已变更"}), 400

        session["reply_content"] = ai_content
        session["status"] = "replied"
        session["messages"].append({"role": "assistant", "content": ai_content})
        session["updated_at"] = now_iso()
        session["request_event"].set()

    remove_from_pending(session_id)
    logger.info(f"[AI回复成功] 会话 {session_id} | 回复: {ai_content[:80]}...")

    socketio.emit("session_updated", serialize_session(session))

    return jsonify({"success": True, "session_id": session_id, "content": ai_content})


@app.route("/api/admin/stats", methods=["GET"])
def admin_stats():
    """获取统计数据"""
    with sessions_lock:
        total = len(sessions)
        waiting = sum(1 for s in sessions.values() if s["status"] == "waiting")
        replied = sum(1 for s in sessions.values() if s["status"] == "replied")
        timeout = sum(1 for s in sessions.values() if s["status"] == "timeout")
    return jsonify({
        "total_sessions": total,
        "waiting": waiting,
        "replied": replied,
        "timeout": timeout,
        "pending_queue": get_pending_count(),
    })


@app.route("/api/admin/config", methods=["GET"])
def admin_get_config():
    """获取当前配置"""
    all_cfg = cfg.get_all()
    if all_cfg.get("api_key"):
        all_cfg["api_key"] = "***"
    if all_cfg.get("ai_api_key"):
        all_cfg["ai_api_key"] = "***"
    return jsonify(all_cfg)


@app.route("/api/admin/config", methods=["POST"])
def admin_update_config():
    """更新配置"""
    try:
        data = request.json
        if not data:
            return jsonify({"error": "请求体为空"}), 400
    except Exception:
        return jsonify({"error": "无效的 JSON 格式"}), 400

    allowed_keys = {"api_key", "timeout", "timeout_reply", "host", "port",
                    "ai_enabled", "ai_api_url", "ai_api_key", "ai_model", "ai_system_prompt", "ai_auto_host",
                    "heartbeat_enabled", "heartbeat_patterns"}
    updates = {k: v for k, v in data.items() if k in allowed_keys and v is not None}

    if "timeout" in updates:
        try:
            updates["timeout"] = int(updates["timeout"])
            if updates["timeout"] < 10:
                return jsonify({"error": "超时时间不能小于 10 秒"}), 400
        except (ValueError, TypeError):
            return jsonify({"error": "超时时间必须是整数"}), 400

    if "port" in updates:
        try:
            updates["port"] = int(updates["port"])
        except (ValueError, TypeError):
            return jsonify({"error": "端口必须是整数"}), 400

    cfg.update_config(updates)
    logger.info(f"[配置更新] {list(updates.keys())}")
    return jsonify({"success": True, "updated": list(updates.keys())})


@app.route("/api/admin/clear", methods=["POST"])
def admin_clear_sessions():
    """清空所有会话历史"""
    with sessions_lock:
        for sid, s in sessions.items():
            if s["status"] == "waiting" and s.get("request_event"):
                s["reply_content"] = cfg.get("timeout_reply", "抱歉，当前人工客服繁忙，请稍后再试。")
                s["status"] = "timeout"
                s["request_event"].set()
        sessions.clear()
    with pending_lock:
        pending_queue.clear()
    logger.info("[清空] 所有会话已清空")
    return jsonify({"success": True})


# ==================== WebSocket 事件 ====================
@socketio.on("connect")
def handle_connect():
    """客户端连接时发送当前状态"""
    logger.info(f"[WS] 客户端连接: {request.sid}")
    cleanup_expired_sessions()
    with sessions_lock:
        all_sessions = [serialize_session(s) for s in sessions.values()]
    emit("init_data", {
        "sessions": all_sessions,
        "pending_count": get_pending_count(),
        "config": cfg.get_all(),
    })


@socketio.on("disconnect")
def handle_disconnect():
    logger.info(f"[WS] 客户端断开: {request.sid}")


@socketio.on("request_sessions")
def handle_request_sessions():
    """客户端请求最新的会话列表"""
    with sessions_lock:
        all_sessions = [serialize_session(s) for s in sessions.values()]
    emit("sessions_list", {"sessions": all_sessions})


@socketio.on("request_messages")
def handle_request_messages(data):
    """客户端请求指定会话的消息历史"""
    session_id = data.get("session_id", "")
    with sessions_lock:
        session = sessions.get(session_id)
        if session:
            emit("messages_data", {"session_id": session_id, "messages": session["messages"]})
        else:
            emit("error", {"message": "会话不存在"})


# ==================== 入口 ====================
def start_cleanup_timer():
    """启动定时清理过期会话的后台线程"""
    import threading

    def _cleanup_loop():
        while True:
            time.sleep(600)
            cleanup_expired_sessions()

    t = threading.Thread(target=_cleanup_loop, daemon=True)
    t.start()
    logger.info("[后台] 会话清理线程已启动（每10分钟清理一次）")


if __name__ == "__main__":
    host = cfg.get("host", "0.0.0.0")
    port = cfg.get("port", 5000)

    print("=" * 50)
    print("  Human-API Server")
    print("  https://github.com/wfqefwqf/human-api")
    print("=" * 50)
    print(f"  WebUI 管理界面: http://127.0.0.1:{port}")
    print(f"  API 端点:       http://{host}:{port}/v1/chat/completions")
    print(f"  超时时间:       {cfg.get('timeout')}秒")
    api_key = cfg.get("api_key", "")
    print(f"  接口鉴权:       {'已启用' if api_key else '未启用（任何人可调用）'}")
    print("=" * 50)

    start_cleanup_timer()
    socketio.run(app, host=host, port=port, debug=False, allow_unsafe_werkzeug=True)
