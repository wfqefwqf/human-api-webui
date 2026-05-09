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
import threading
from datetime import datetime
from threading import Lock

from flask import Flask, request, jsonify, Response, send_from_directory
from flask_socketio import SocketIO, emit
import requests as http_requests

import config as cfg
import database as db
from models_data import MODELS_CACHE

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

db.init_db()

# ==================== 会话存储 ====================
sessions = {}
sessions_lock = Lock()

pending_queue = set()
pending_lock = Lock()


def get_pending_count():
    with pending_lock:
        return len(pending_queue)


def add_to_pending(session_id):
    with pending_lock:
        pending_queue.add(session_id)


def remove_from_pending(session_id):
    with pending_lock:
        pending_queue.discard(session_id)


# ==================== 辅助函数 ====================
def generate_session_id():
    return f"sess-{uuid.uuid4().hex[:12]}"


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def cleanup_expired_sessions(max_age_hours=24):
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
    if stream:
        return {
            "id": f"chatcmpl-{session_id}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}]
        }
    return {
        "id": f"chatcmpl-{session_id}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    }


def serialize_session(session):
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

    payload = {"model": ai_model, "messages": req_messages, "stream": False}

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
            session["updated_at"] = now_iso()
            session["request_event"].set()

        remove_from_pending(session_id)
        db.update_session_status(session_id, "replied", session["updated_at"])
        db.save_message(session_id, "assistant", ai_content, session["updated_at"])
        logger.info(f"[AI自动托管成功] 会话 {session_id} | 回复: {ai_content[:80]}...")
        socketio.emit("session_updated", serialize_session(sessions.get(session_id)))

    t = threading.Thread(target=_do_auto_host, daemon=True)
    t.start()


def _sse_response(session_id, model, content):
    def generate():
        cid = f"chatcmpl-{session_id}"
        ts = int(time.time())
        yield f"data: {json.dumps({'id': cid, 'object': 'chat.completion.chunk', 'created': ts, 'model': model, 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]})}\n\n"
        yield f"data: {json.dumps({'id': cid, 'object': 'chat.completion.chunk', 'created': ts, 'model': model, 'choices': [{'index': 0, 'delta': {'content': content}, 'finish_reason': None}]})}\n\n"
        yield f"data: {json.dumps({'id': cid, 'object': 'chat.completion.chunk', 'created': ts, 'model': model, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
        yield "data: [DONE]\n\n"
    return Response(generate(), mimetype="text/event-stream")


# ==================== 鉴权中间层 ====================
def check_api_key():
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
    return send_from_directory("static", "index.html")


# ==================== OpenAI 兼容 API ====================
@app.route("/v1/chat/completions", methods=["POST", "OPTIONS"])
def chat_completions():
    if request.method == "OPTIONS":
        return "", 200

    auth_err = check_api_key()
    if auth_err:
        return auth_err

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

    user_query = ""
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "user":
            user_query = msg.get("content", "")
            break

    if cfg.get("heartbeat_enabled", True):
        patterns = cfg.get("heartbeat_patterns", [])
        if user_query.strip().lower() in [p.lower() for p in patterns]:
            logger.info(f"[心跳] 自动回复: {user_query}")
            heartbeat_reply = user_query.strip()
            if stream:
                return _sse_response("heartbeat", model, heartbeat_reply)
            return jsonify(build_openai_response("heartbeat", model, heartbeat_reply))

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
            event = threading.Event()

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
            db.save_session(sessions[session_id])
            db.save_messages_bulk(session_id, list(messages))
            logger.info(f"[新请求] 会话 {session_id} | 模型: {model} | 提问: {user_query[:80]}...")

            socketio.emit("new_request", {
                "session": serialize_session(sessions[session_id]),
                "query_preview": user_query[:200],
            })

            if cfg.get("ai_auto_host", False):
                _auto_host_thread(session_id)

    timeout = cfg.get("timeout", 120)
    replied = event.wait(timeout=timeout)

    with sessions_lock:
        session = sessions.get(session_id)
        if not session:
            answer = cfg.get("timeout_reply", "抱歉，当前人工客服繁忙，请稍后再试。")
            remove_from_pending(session_id)
            logger.warning(f"[会话丢失] 会话 {session_id} 已被清理，返回超时回复")
            if stream:
                return _sse_response(session_id, model, answer)
            return jsonify(build_openai_response(session_id, model, answer))

        if replied and session["reply_content"]:
            answer = session["reply_content"]
            session["messages"].append({"role": "assistant", "content": answer})
            if session["status"] != "replied":
                session["status"] = "replied"
                session["updated_at"] = now_iso()
                db.update_session_status(session_id, "replied", session["updated_at"])
                db.save_message(session_id, "assistant", answer, session["updated_at"])
        else:
            answer = cfg.get("timeout_reply", "抱歉，当前人工客服繁忙，请稍后再试。")
            session["status"] = "timeout"
            session["messages"].append({"role": "assistant", "content": answer})
            session["updated_at"] = now_iso()
            db.update_session_status(session_id, "timeout", session["updated_at"])
            db.save_message(session_id, "assistant", answer, session["updated_at"])
            logger.warning(f"[超时] 会话 {session_id} 超时未回复")

    remove_from_pending(session_id)
    socketio.emit("session_updated", serialize_session(session))
    logger.info(f"[回复] 会话 {session_id} | 回复: {answer[:80]}...")

    if stream:
        return _sse_response(session_id, model, answer)

    return jsonify(build_openai_response(session_id, model, answer))


@app.route("/v1/models", methods=["GET"])
def list_models():
    return jsonify({"object": "list", "data": MODELS_CACHE})


@app.route("/api/chat", methods=["POST", "OPTIONS"])
def api_chat_compat():
    if request.method == "OPTIONS":
        return "", 200
    return chat_completions()


# ==================== 管理后台 API ====================
@app.route("/api/admin/sessions", methods=["GET"])
def admin_list_sessions():
    with sessions_lock:
        result = [serialize_session(s) for s in sessions.values()]
    result.sort(key=lambda x: x["created_at"], reverse=True)
    return jsonify({"sessions": result, "total": len(result), "pending": get_pending_count()})


@app.route("/api/admin/sessions/<session_id>", methods=["GET"])
def admin_get_session(session_id):
    with sessions_lock:
        session = sessions.get(session_id)
        if not session:
            return jsonify({"error": "会话不存在"}), 404
        return jsonify(serialize_session(session))


@app.route("/api/admin/sessions/<session_id>/messages", methods=["GET"])
def admin_get_messages(session_id):
    with sessions_lock:
        session = sessions.get(session_id)
        if not session:
            return jsonify({"error": "会话不存在"}), 404
        return jsonify({"messages": session["messages"], "session_id": session_id})


@app.route("/api/admin/reply", methods=["POST"])
def admin_reply():
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
    db.update_session_status(session_id, "replied", session["updated_at"])
    db.save_message(session_id, "assistant", content, session["updated_at"])
    logger.info(f"[WebUI回复] 会话 {session_id} | 回复: {content[:80]}...")

    socketio.emit("session_updated", serialize_session(session))

    return jsonify({"success": True, "session_id": session_id})


@app.route("/api/admin/ai_reply", methods=["POST"])
def admin_ai_reply():
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
        session["updated_at"] = now_iso()
        session["request_event"].set()

    remove_from_pending(session_id)
    db.update_session_status(session_id, "replied", session["updated_at"])
    db.save_message(session_id, "assistant", ai_content, session["updated_at"])
    logger.info(f"[AI回复成功] 会话 {session_id} | 回复: {ai_content[:80]}...")

    socketio.emit("session_updated", serialize_session(session))

    return jsonify({"success": True, "session_id": session_id, "content": ai_content})


@app.route("/api/admin/stats", methods=["GET"])
def admin_stats():
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
    all_cfg = cfg.get_all()
    if all_cfg.get("api_key"):
        all_cfg["api_key"] = "***"
    if all_cfg.get("ai_api_key"):
        all_cfg["ai_api_key"] = "***"
    return jsonify(all_cfg)


@app.route("/api/admin/config", methods=["POST"])
def admin_update_config():
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
    with sessions_lock:
        for sid, s in sessions.items():
            if s["status"] == "waiting" and s.get("request_event"):
                s["reply_content"] = cfg.get("timeout_reply", "抱歉，当前人工客服繁忙，请稍后再试。")
                s["status"] = "timeout"
                s["request_event"].set()
        sessions.clear()
    with pending_lock:
        pending_queue.clear()
    db.clear_all()
    logger.info("[清空] 所有会话已清空")
    return jsonify({"success": True})


@app.route("/api/admin/export/<session_id>", methods=["GET"])
def admin_export_session(session_id):
    session, messages = db.export_session_messages(session_id)
    if not session:
        return jsonify({"error": "会话不存在"}), 404
    data = {"session": session, "messages": messages}
    content = json.dumps(data, ensure_ascii=False, indent=2)
    return Response(
        content,
        mimetype="application/json",
        headers={"Content-Disposition": f"attachment; filename={session_id}.json"}
    )


@app.route("/api/admin/export", methods=["GET"])
def admin_export_all():
    fmt = request.args.get("format", "json")
    all_data = db.export_all_messages()
    if not all_data:
        return jsonify({"error": "没有可导出的会话"}), 404
    if fmt == "txt":
        lines = []
        for item in all_data:
            s = item["session"]
            lines.append(f"{'='*60}")
            lines.append(f"会话: {s['id']}  模型: {s['model']}  状态: {s['status']}")
            lines.append(f"创建时间: {s['created_at']}  更新时间: {s['updated_at']}")
            lines.append(f"{'-'*60}")
            for m in item["messages"]:
                label = {"user": "用户", "assistant": "AI", "system": "系统"}.get(m["role"], m["role"])
                lines.append(f"[{label}] {m['content']}")
            lines.append("")
        content = "\n".join(lines)
        return Response(
            content,
            mimetype="text/plain; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=human-api-export.txt"}
        )
    else:
        content = json.dumps(all_data, ensure_ascii=False, indent=2)
        return Response(
            content,
            mimetype="application/json",
            headers={"Content-Disposition": "attachment; filename=human-api-export.json"}
        )


# ==================== WebSocket 事件 ====================
@socketio.on("connect")
def handle_connect():
    logger.info(f"[WS] 客户端连接: {request.sid}")
    cleanup_expired_sessions()
    with sessions_lock:
        if not sessions:
            db_sessions = db.load_all_sessions()
            for ds in db_sessions:
                if ds["status"] == "waiting":
                    ds["status"] = "timeout"
                    db.update_session_status(ds["id"], "timeout")
                messages = db.load_messages(ds["id"])
                sessions[ds["id"]] = {
                    **ds,
                    "messages": messages,
                    "request_event": None,
                    "reply_content": None,
                    "pending_message": {},
                }
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
    with sessions_lock:
        all_sessions = [serialize_session(s) for s in sessions.values()]
    emit("sessions_list", {"sessions": all_sessions})


@socketio.on("request_messages")
def handle_request_messages(data):
    session_id = data.get("session_id", "")
    with sessions_lock:
        session = sessions.get(session_id)
        if session:
            emit("messages_data", {"session_id": session_id, "messages": session["messages"]})
        else:
            emit("error", {"message": "会话不存在"})


# ==================== 入口 ====================
def start_cleanup_timer():
    def _cleanup_loop():
        while True:
            time.sleep(600)
            cleanup_expired_sessions()

    t = threading.Thread(target=_cleanup_loop, daemon=True)
    t.start()
    logger.info("[后台] 会话清理线程已启动（每10分钟清理一次）")


if __name__ == "__main__":
    host = cfg.get("host", "0.0.0.0")
    port = int(os.environ.get("PORT", cfg.get("port", 5000)))

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
