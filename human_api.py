from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import requests
import threading
import time
import json
from datetime import datetime
from itertools import count

app = Flask(__name__)
CORS(app)  # 允许所有跨域请求

# ================= 配置 =================
PUSH_URL = None
session_counter = count(1)

# 存储会话
sessions = {}

# ================= 颜色定义 =================
COLOR_USER = "\033[37m"      # 白色
COLOR_AI = "\033[34m"        # 蓝色
COLOR_PURPLE = "\033[35m"    # 紫色
COLOR_RESET = "\033[0m"

# ================= 显示完整对话历史 =================
def print_conversation(session_id):
    """打印整个会话的完整对话历史（user 和 AI 交错）"""
    session = sessions[session_id]
    history = session.get('history', [])
    
    print()  # 空行
    
    # 显示系统提示词（如果有）
    system_prompt = session.get('system_prompt', '')
    if system_prompt:
        print(f"[系统提示词]\n{system_prompt}\n")
    
    # 显示对话历史
    print("[对话历史]")
    for entry in history:
        role = entry.get('role')
        content = entry.get('content')
        if role == 'user':
            print(f"  {COLOR_USER}[user]{COLOR_RESET} {content}")
        elif role == 'assistant':
            print(f"  {COLOR_AI}[AI]{COLOR_RESET} {content}")
    
    # 显示会话 ID 和模型
    print(f"\n会话 ID: {session_id}")
    print(f"模型: {session['model']}")
    print(f"回复 API: POST /reply  {{\"id\":{session_id}, \"answer\":\"...\"}}")
    print(f"{'='*60}\n")

# ================= 手机推送 =================
def push_to_phone(session_id, messages, model):
    """推送新请求到手机，显示完整对话历史"""
    system_prompt = ""
    user_messages = []
    assistant_messages = []
    
    for msg in messages:
        role = msg.get('role', 'unknown')
        content = msg.get('content', '')
        if role == 'system' and not system_prompt:
            system_prompt = content
        elif role == 'user':
            user_messages.append(content)
        elif role == 'assistant':
            assistant_messages.append(content)
    
    sessions[session_id]['system_prompt'] = system_prompt
    
    existing_history = sessions[session_id].get('history', [])
    existing_contents = [(h['role'], h['content']) for h in existing_history]
    
    for msg in user_messages:
        if ('user', msg) not in existing_contents:
            sessions[session_id]['history'].append({'role': 'user', 'content': msg})
    
    for msg in assistant_messages:
        if ('assistant', msg) not in existing_contents:
            sessions[session_id]['history'].append({'role': 'assistant', 'content': msg})
    
    print_conversation(session_id)

# ================= OpenAI 标准流式响应 =================
def generate_stream(session_id, model):
    """生成标准的 OpenAI SSE 流式响应"""
    chunk1 = {
        'id': f'chatcmpl-{session_id}',
        'object': 'chat.completion.chunk',
        'created': int(time.time()),
        'model': model,
        'choices': [{
            'index': 0,
            'delta': {'role': 'assistant'},
            'finish_reason': None
        }]
    }
    yield f"data: {json.dumps(chunk1)}\n\n"
    
    for _ in range(120):
        if sessions[session_id]['reply'] is not None:
            answer = sessions[session_id]['reply']
            chunk2 = {
                'id': f'chatcmpl-{session_id}',
                'object': 'chat.completion.chunk',
                'created': int(time.time()),
                'model': model,
                'choices': [{
                    'index': 0,
                    'delta': {'content': answer},
                    'finish_reason': None
                }]
            }
            yield f"data: {json.dumps(chunk2)}\n\n"
            end_chunk = {
                'id': f'chatcmpl-{session_id}',
                'object': 'chat.completion.chunk',
                'created': int(time.time()),
                'model': model,
                'choices': [{
                    'index': 0,
                    'delta': {},
                    'finish_reason': 'stop'
                }]
            }
            yield f"data: {json.dumps(end_chunk)}\n\n"
            yield "data: [DONE]\n\n"
            return
        time.sleep(1)
    
    yield "data: [DONE]\n\n"

# ================= 核心处理逻辑 =================
def process_chat_request(model, messages, stream):
    session_id = str(next(session_counter))
    
    sessions[session_id] = {
        'model': model,
        'messages': messages,
        'history': [],
        'system_prompt': '',
        'reply': None,
        'created_at': datetime.now().isoformat(),
        'status': 'waiting'
    }
    
    push_to_phone(session_id, messages, model)
    
    if stream:
        return Response(generate_stream(session_id, model), mimetype='text/event-stream')
    else:
        for _ in range(120):
            if sessions[session_id]['reply'] is not None:
                answer = sessions[session_id]['reply']
                sessions[session_id]['history'].append({'role': 'assistant', 'content': answer})
                return jsonify({
                    'id': f'chatcmpl-{session_id}',
                    'object': 'chat.completion',
                    'created': int(time.time()),
                    'model': model,
                    'choices': [{
                        'index': 0,
                        'message': {
                            'role': 'assistant',
                            'content': answer
                        },
                        'finish_reason': 'stop'
                    }],
                    'usage': {
                        'prompt_tokens': 0,
                        'completion_tokens': 0,
                        'total_tokens': 0
                    }
                })
            time.sleep(1)
        return jsonify({'error': '人工回复超时，请稍后重试'}), 504

# ================= API 路由 =================
@app.route('/v1/chat/completions', methods=['POST', 'OPTIONS'])
def chat_completions():
    if request.method == 'OPTIONS':
        return '', 200
    data = request.json
    model = data.get('model', 'gpt-3.5-turbo')
    messages = data.get('messages', [])
    stream = data.get('stream', False)
    return process_chat_request(model, messages, stream)

@app.route('/', defaults={'path': ''}, methods=['POST', 'OPTIONS'])
@app.route('/<path:path>', methods=['POST', 'OPTIONS'])
def catch_all_completions(path):
    if request.method == 'OPTIONS':
        return '', 200
    try:
        data = request.json
        if data and 'messages' in data:
            model = data.get('model', 'gpt-3.5-turbo')
            messages = data.get('messages', [])
            stream = data.get('stream', False)
            return process_chat_request(model, messages, stream)
        else:
            return jsonify({'error': 'Invalid request format'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/v1/models', methods=['GET'])
def list_models():
    return jsonify({
        'object': 'list',
        'data': [
            # ========== OpenAI ==========
            {'id': 'gpt-5.5', 'object': 'model'},
            {'id': 'gpt-5.4', 'object': 'model'},
            {'id': 'gpt-5.3', 'object': 'model'},
            {'id': 'gpt-5.2', 'object': 'model'},
            {'id': 'gpt-5.1', 'object': 'model'},
            {'id': 'gpt-5', 'object': 'model'},
            {'id': 'gpt-5-pro', 'object': 'model'},
            {'id': 'gpt-4o', 'object': 'model'},
            {'id': 'gpt-4-turbo', 'object': 'model'},
            {'id': 'gpt-4', 'object': 'model'},
            {'id': 'gpt-3.5-turbo', 'object': 'model'},
            {'id': 'o3', 'object': 'model'},
            {'id': 'o3-mini', 'object': 'model'},
            {'id': 'o4-mini', 'object': 'model'},
            {'id': 'o1', 'object': 'model'},
            {'id': 'o1-mini', 'object': 'model'},
            {'id': 'deep-research', 'object': 'model'},

            # ========== Anthropic Claude ==========
            {'id': 'claude-opus-4.6', 'object': 'model'},
            {'id': 'claude-sonnet-4.6', 'object': 'model'},
            {'id': 'claude-haiku-4.5', 'object': 'model'},
            {'id': 'claude-3-opus', 'object': 'model'},
            {'id': 'claude-3-sonnet', 'object': 'model'},
            {'id': 'claude-3-haiku', 'object': 'model'},
            {'id': 'claude-3.5-sonnet', 'object': 'model'},
            {'id': 'claude-3.7-sonnet', 'object': 'model'},

            # ========== Google Gemini ==========
            {'id': 'gemini-3.1-pro', 'object': 'model'},
            {'id': 'gemini-3.1-flash-lite', 'object': 'model'},
            {'id': 'gemini-3-pro', 'object': 'model'},
            {'id': 'gemini-3-flash', 'object': 'model'},
            {'id': 'gemini-2.0-pro', 'object': 'model'},
            {'id': 'gemini-2.0-flash', 'object': 'model'},
            {'id': 'gemini-1.5-pro', 'object': 'model'},
            {'id': 'gemini-1.5-flash', 'object': 'model'},
            {'id': 'gemini-ultra', 'object': 'model'},
            {'id': 'gemini-pro', 'object': 'model'},
            {'id': 'gemini-flash', 'object': 'model'},
            {'id': 'gemini-nano', 'object': 'model'},
            {'id': 'gemma-2-27b', 'object': 'model'},
            {'id': 'gemma-2-9b', 'object': 'model'},
            {'id': 'gemma-2-2b', 'object': 'model'},

            # ========== Meta Llama ==========
            {'id': 'llama-4', 'object': 'model'},
            {'id': 'llama-3.3-70b', 'object': 'model'},
            {'id': 'llama-3.2', 'object': 'model'},
            {'id': 'llama-3.1-405b', 'object': 'model'},
            {'id': 'llama-3.1-70b', 'object': 'model'},
            {'id': 'llama-3.1-8b', 'object': 'model'},
            {'id': 'llama-3-70b', 'object': 'model'},
            {'id': 'llama-3-8b', 'object': 'model'},
            {'id': 'llama-guard-3', 'object': 'model'},
            {'id': 'mobile-llama', 'object': 'model'},

            # ========== DeepSeek ==========
            {'id': 'deepseek-v4', 'object': 'model'},
            {'id': 'deepseek-v3.2', 'object': 'model'},
            {'id': 'deepseek-v3', 'object': 'model'},
            {'id': 'deepseek-r1', 'object': 'model'},
            {'id': 'deepseek-r1-0528', 'object': 'model'},
            {'id': 'deepseek-chat', 'object': 'model'},
            {'id': 'deepseek-coder', 'object': 'model'},

            # ========== 阿里巴巴 通义千问 ==========
            {'id': 'qwen-3.5', 'object': 'model'},
            {'id': 'qwen-3.5-max', 'object': 'model'},
            {'id': 'qwen-3.5-plus', 'object': 'model'},
            {'id': 'qwen-3.5-turbo', 'object': 'model'},
            {'id': 'qwen-3.5-397b', 'object': 'model'},
            {'id': 'qwen-3-max', 'object': 'model'},
            {'id': 'qwen-3-plus', 'object': 'model'},
            {'id': 'qwen-3-turbo', 'object': 'model'},
            {'id': 'qwen-3-embedding', 'object': 'model'},
            {'id': 'qwen-2.5-72b', 'object': 'model'},
            {'id': 'qwen-2.5-32b', 'object': 'model'},
            {'id': 'qwen-2.5-14b', 'object': 'model'},
            {'id': 'qwen-2.5-7b', 'object': 'model'},
            {'id': 'qwen-2.5-3b', 'object': 'model'},
            {'id': 'qwen-2.5-1.5b', 'object': 'model'},
            {'id': 'qwen-2.5-0.5b', 'object': 'model'},
            {'id': 'qwen-2.5-coder', 'object': 'model'},
            {'id': 'qwen-3-coder-next', 'object': 'model'},
            {'id': 'qwen-3-coder', 'object': 'model'},
            {'id': 'qwen-max', 'object': 'model'},
            {'id': 'qwen-plus', 'object': 'model'},
            {'id': 'qwen-turbo', 'object': 'model'},
            {'id': 'qwen-omni', 'object': 'model'},

            # ========== 字节跳动 豆包 ==========
            {'id': 'doubao-2.0', 'object': 'model'},
            {'id': 'doubao-pro-256k', 'object': 'model'},
            {'id': 'doubao-pro-32k', 'object': 'model'},
            {'id': 'doubao-lite-32k', 'object': 'model'},
            {'id': 'seedance-2.0', 'object': 'model'},
            {'id': 'bitdance', 'object': 'model'},
            {'id': 'concept-moe', 'object': 'model'},
            {'id': 'doubao-vision', 'object': 'model'},
            {'id': 'doubao-embedding', 'object': 'model'},
            {'id': 'doubao-realtime', 'object': 'model'},
            {'id': 'doubao-voice', 'object': 'model'},
            {'id': 'x-agents', 'object': 'model'},
            {'id': 'seed-music', 'object': 'model'},
            {'id': 'seed-video', 'object': 'model'},
            {'id': 'seed-tts', 'object': 'model'},
            {'id': 'seed-ocr', 'object': 'model'},

            # ========== 智谱 GLM ==========
            {'id': 'glm-5.1', 'object': 'model'},
            {'id': 'glm-5', 'object': 'model'},
            {'id': 'glm-4.7', 'object': 'model'},
            {'id': 'glm-4-plus', 'object': 'model'},
            {'id': 'glm-4-long', 'object': 'model'},
            {'id': 'glm-4-flash', 'object': 'model'},
            {'id': 'glm-4-air', 'object': 'model'},
            {'id': 'glm-4v-plus', 'object': 'model'},
            {'id': 'glm-z1-air', 'object': 'model'},
            {'id': 'glm-3-turbo', 'object': 'model'},
            {'id': 'glm-ocr', 'object': 'model'},
            {'id': 'z-code', 'object': 'model'},

            # ========== 百度 文心 ==========
            {'id': 'wenxin-5.0', 'object': 'model'},
            {'id': 'wenxin-4.0-turbo', 'object': 'model'},
            {'id': 'wenxin-4.0', 'object': 'model'},
            {'id': 'wenxin-3.5', 'object': 'model'},
            {'id': 'wenxin-lite', 'object': 'model'},
            {'id': 'wenxin-embeddings', 'object': 'model'},
            {'id': 'wenxin-ocr', 'object': 'model'},
            {'id': 'wenxin-speech', 'object': 'model'},

            # ========== 月之暗面 Kimi ==========
            {'id': 'kimi-k2.5', 'object': 'model'},
            {'id': 'kimi-v2', 'object': 'model'},
            {'id': 'moonshot-v1-128k', 'object': 'model'},
            {'id': 'moonshot-v1-32k', 'object': 'model'},
            {'id': 'moonshot-v1-8k', 'object': 'model'},
            {'id': 'simple-seg', 'object': 'model'},
            {'id': 'kimi-vision', 'object': 'model'},
            {'id': 'kimi-voice', 'object': 'model'},

            # ========== MiniMax (稀宇科技) ==========
            {'id': 'minimax-m2.7', 'object': 'model'},
            {'id': 'minimax-m2.5', 'object': 'model'},
            {'id': 'abab-7-chat', 'object': 'model'},
            {'id': 'abab-6.5-chat', 'object': 'model'},
            {'id': 'abab-5.5-chat', 'object': 'model'},
            {'id': 'minimax-speech', 'object': 'model'},
            {'id': 'minimax-voice-clone', 'object': 'model'},
            {'id': 'minimax-video', 'object': 'model'},
            {'id': 'minimax-embedding', 'object': 'model'},

            # ========== 零一万物 Yi ==========
            {'id': 'yi-large-2', 'object': 'model'},
            {'id': 'yi-large', 'object': 'model'},
            {'id': 'yi-medium', 'object': 'model'},
            {'id': 'yi-spark', 'object': 'model'},
            {'id': 'yi-vision', 'object': 'model'},
            {'id': 'yi-34b', 'object': 'model'},
            {'id': 'yi-6b', 'object': 'model'},

            # ========== 腾讯 混元 ==========
            {'id': 'hunyuan-hy3-preview', 'object': 'model'},
            {'id': 'hunyuan-3.0', 'object': 'model'},
            {'id': 'hunyuan-pro', 'object': 'model'},
            {'id': 'hunyuan-standard', 'object': 'model'},
            {'id': 'hunyuan-lite', 'object': 'model'},
            {'id': 'hunyuan-1.8b-2bit', 'object': 'model'},
            {'id': 'hpc-ops', 'object': 'model'},

            # ========== 科大讯飞 星火 ==========
            {'id': 'spark-x2-flash', 'object': 'model'},
            {'id': 'spark-x2', 'object': 'model'},
            {'id': 'spark-v4.0', 'object': 'model'},
            {'id': 'spark-v3.5', 'object': 'model'},
            {'id': 'spark-lite', 'object': 'model'},
            {'id': 'spark-tts', 'object': 'model'},
            {'id': 'spark-ocr', 'object': 'model'},
            {'id': 'spark-review', 'object': 'model'},

            # ========== 360 智脑 ==========
            {'id': '360gpt-pro', 'object': 'model'},
            {'id': '360gpt-turbo', 'object': 'model'},
            {'id': '360gpt', 'object': 'model'},

            # ========== 商汤 日日新 ==========
            {'id': 'sensetime-nova', 'object': 'model'},
            {'id': 'sensetime-pro', 'object': 'model'},
            {'id': 'sensetime-lite', 'object': 'model'},
            {'id': 'sensetime-vision', 'object': 'model'},
            {'id': 'sensetime-vega', 'object': 'model'},

            # ========== 昆仑万维 Skywork ==========
            {'id': 'skywork-13b', 'object': 'model'},
            {'id': 'skywork-7b', 'object': 'model'},
            {'id': 'skywork-3b', 'object': 'model'},
            {'id': 'skywork-code', 'object': 'model'},
            {'id': 'skywork-voice', 'object': 'model'},

            # ========== 小米 Mimo ==========
            {'id': 'mimo-v2-flash-0204', 'object': 'model'},
            {'id': 'mimo-v2', 'object': 'model'},
            {'id': 'mimo-embed', 'object': 'model'},
            {'id': 'mimo-vision', 'object': 'model'},
            {'id': 'mimo-voice', 'object': 'model'},
            {'id': 'mimo-agent', 'object': 'model'},
            {'id': 'mimo-code', 'object': 'model'},
            {'id': 'mimo-translate', 'object': 'model'},
            {'id': 'mimo-summary', 'object': 'model'},
            {'id': 'mimo-recommend', 'object': 'model'},
            {'id': 'mimo-search', 'object': 'model'},

            # ========== 阶跃星辰 Step ==========
            {'id': 'step-3.5-flash', 'object': 'model'},
            {'id': 'step-3.5', 'object': 'model'},
            {'id': 'step-3', 'object': 'model'},
            {'id': 'step-2', 'object': 'model'},
            {'id': 'step-vision', 'object': 'model'},
            {'id': 'step-audio', 'object': 'model'},

            # ========== 蚂蚁集团 Ant Group ==========
            {'id': 'ling-2.5-1t', 'object': 'model'},
            {'id': 'ring-2.5-1t', 'object': 'model'},
            {'id': 'llada-2.1', 'object': 'model'},
            {'id': 'ming-flash-omni-2.0', 'object': 'model'},
            {'id': 'ming-omni-tts', 'object': 'model'},
            {'id': 'ant-llm', 'object': 'model'},
            {'id': 'ant-vision', 'object': 'model'},
            {'id': 'ant-embedding', 'object': 'model'},
            {'id': 'ant-rag', 'object': 'model'},

            # ========== 京东 JoyAI ==========
            {'id': 'joyai-llm-flash', 'object': 'model'},
            {'id': 'joyai-pro', 'object': 'model'},
            {'id': 'joyai-lite', 'object': 'model'},
            {'id': 'joyai-code', 'object': 'model'},
            {'id': 'joyai-chat', 'object': 'model'},
            {'id': 'joyai-vision', 'object': 'model'},
            {'id': 'joyai-voice', 'object': 'model'},
            {'id': 'joyai-embedding', 'object': 'model'},
            {'id': 'joyai-agent', 'object': 'model'},
            {'id': 'joyai-recommend', 'object': 'model'},
            {'id': 'joyai-search', 'object': 'model'},
            {'id': 'joyai-summary', 'object': 'model'},
            {'id': 'joyai-translate', 'object': 'model'},
            {'id': 'joyai-summary-agent', 'object': 'model'},

            # ========== 美团 LongCat ==========
            {'id': 'longcat-flash-lite', 'object': 'model'},
            {'id': 'longcat-pro', 'object': 'model'},
            {'id': 'longcat-lite', 'object': 'model'},
            {'id': 'longcat-chat', 'object': 'model'},
            {'id': 'longcat-agent', 'object': 'model'},

            # ========== 小红书 FireRed ==========
            {'id': 'rednote-fireasr-2s', 'object': 'model'},
            {'id': 'rednote-image-edit', 'object': 'model'},
            {'id': 'rednote-nlp', 'object': 'model'},
            {'id': 'rednote-cv', 'object': 'model'},
            {'id': 'rednote-voice', 'object': 'model'},
            {'id': 'rednote-translate', 'object': 'model'},
            {'id': 'rednote-summary', 'object': 'model'},
            {'id': 'rednote-rag', 'object': 'model'},
            {'id': 'rednote-agent', 'object': 'model'},
            {'id': 'rednote-rec', 'object': 'model'},
            {'id': 'rednote-chat', 'object': 'model'},
            {'id': 'rednote-search', 'object': 'model'},
            {'id': 'rednote-recommend', 'object': 'model'},
            {'id': 'rednote-embedding', 'object': 'model'},
            {'id': 'rednote-vision', 'object': 'model'},
            {'id': 'rednote-metric', 'object': 'model'},
            {'id': 'rednote-training', 'object': 'model'},
            {'id': 'firefly-llm', 'object': 'model'},
            {'id': 'firefly-embedding', 'object': 'model'},
            {'id': 'firefly-search', 'object': 'model'},
            {'id': 'firefly-recommend', 'object': 'model'},
            {'id': 'firefly-nlp-base', 'object': 'model'},
            {'id': 'firefly-cv-base', 'object': 'model'},
            {'id': 'firefly-agent', 'object': 'model'},
            {'id': 'firefly-chat', 'object': 'model'},

            # ========== 快手 Kling ==========
            {'id': 'kling-3.0', 'object': 'model'},
            {'id': 'kling-2.5', 'object': 'model'},
            {'id': 'kling-pro', 'object': 'model'},
            {'id': 'kling-standard', 'object': 'model'},
            {'id': 'kling-vision', 'object': 'model'},
            {'id': 'kling-audio', 'object': 'model'},
            {'id': 'kling-embedding', 'object': 'model'},
            {'id': 'kling-agent', 'object': 'model'},
            {'id': 'kling-code', 'object': 'model'},
            {'id': 'kling-translate', 'object': 'model'},
            {'id': 'kling-summary', 'object': 'model'},
            {'id': 'kling-recommend', 'object': 'model'},
            {'id': 'kling-search', 'object': 'model'},
            {'id': 'kwai-llm', 'object': 'model'},
            {'id': 'kwai-chat', 'object': 'model'},

            # ========== 荣耀 Honor AI ==========
            {'id': 'honor-ai', 'object': 'model'},
            {'id': 'honor-vision', 'object': 'model'},
            {'id': 'honor-voice', 'object': 'model'},
            {'id': 'honor-embedding', 'object': 'model'},
            {'id': 'honor-agent', 'object': 'model'},
            {'id': 'honor-chat', 'object': 'model'},
            {'id': 'honor-summary', 'object': 'model'},
            {'id': 'honor-translate', 'object': 'model'},
            {'id': 'honor-recommend', 'object': 'model'},
            {'id': 'honor-search', 'object': 'model'},
            {'id': 'honor-magic', 'object': 'model'},
            {'id': 'honor-magic-pro', 'object': 'model'},
            {'id': 'honor-magic-lite', 'object': 'model'},

            # ========== 中兴 ZTE AI ==========
            {'id': 'zte-ai', 'object': 'model'},
            {'id': 'zte-ai-pro', 'object': 'model'},
            {'id': 'zte-ai-lite', 'object': 'model'},

            # ========== OPPO AI ==========
            {'id': 'oppo-ai', 'object': 'model'},
            {'id': 'oppo-ai-pro', 'object': 'model'},
            {'id': 'oppo-ai-lite', 'object': 'model'},
            {'id': 'oppo-ai-agent', 'object': 'model'},
            {'id': 'oppo-ai-chat', 'object': 'model'},
            {'id': 'oppo-ai-vision', 'object': 'model'},

            # ========== vivo AI ==========
            {'id': 'vivo-ai', 'object': 'model'},
            {'id': 'vivo-ai-pro', 'object': 'model'},
            {'id': 'vivo-ai-lite', 'object': 'model'},
            {'id': 'vivo-ai-chat', 'object': 'model'},
            {'id': 'vivo-ai-agent', 'object': 'model'},
            {'id': 'vivo-ai-vision', 'object': 'model'},
            {'id': 'vivo-ai-voice', 'object': 'model'},
            {'id': 'vivo-ai-embedding', 'object': 'model'},
            {'id': 'vivo-ai-summary', 'object': 'model'},
            {'id': 'vivo-ai-recommend', 'object': 'model'},
            {'id': 'vivo-ai-search', 'object': 'model'},
            {'id': 'vivo-ai-translate', 'object': 'model'},
            {'id': 'vivo-ai-code', 'object': 'model'},
            {'id': 'vivo-ai-agent-pro', 'object': 'model'},
            {'id': 'vivo-ai-chat-pro', 'object': 'model'},
            {'id': 'vivo-ai-vision-pro', 'object': 'model'},
            {'id': 'vivo-ai-voice-pro', 'object': 'model'},
            {'id': 'vivo-ai-embedding-pro', 'object': 'model'},
            {'id': 'vivo-ai-summary-pro', 'object': 'model'},
            {'id': 'vivo-ai-recommend-pro', 'object': 'model'},
            {'id': 'vivo-ai-search-pro', 'object': 'model'},
            {'id': 'vivo-ai-translate-pro', 'object': 'model'},
            {'id': 'vivo-ai-code-pro', 'object': 'model'},

            # ========== xAI Grok ==========
            {'id': 'grok-4.2', 'object': 'model'},
            {'id': 'grok-4.1', 'object': 'model'},
            {'id': 'grok-4', 'object': 'model'},
            {'id': 'grok-3', 'object': 'model'},
            {'id': 'grok-2.5', 'object': 'model'},
            {'id': 'grok-2', 'object': 'model'},
            {'id': 'grok-imagine-1.0', 'object': 'model'},
            {'id': 'grok-vision', 'object': 'model'},
            {'id': 'grok-audio', 'object': 'model'},
            {'id': 'grok-embedding', 'object': 'model'},
            {'id': 'grok-agent', 'object': 'model'},
            {'id': 'grok-summary', 'object': 'model'},
            {'id': 'grok-recommend', 'object': 'model'},
            {'id': 'grok-search', 'object': 'model'},
            {'id': 'grok-translate', 'object': 'model'},
            {'id': 'grok-chat', 'object': 'model'},
            {'id': 'grok-code', 'object': 'model'},
            {'id': 'grok-reasoning', 'object': 'model'},
            {'id': 'grok-multiagent', 'object': 'model'},
            {'id': 'grok-pro', 'object': 'model'},
            {'id': 'grok-lite', 'object': 'model'},
            {'id': 'grok-realtime', 'object': 'model'},
            {'id': 'grok-voice', 'object': 'model'},
            {'id': 'grok-ocr', 'object': 'model'},
            {'id': 'grok-rag', 'object': 'model'},
            {'id': 'grok-funcall', 'object': 'model'},
            {'id': 'grok-web', 'object': 'model'},

            # ========== Mistral AI ==========
            {'id': 'mistral-large-3', 'object': 'model'},
            {'id': 'mistral-large-2', 'object': 'model'},
            {'id': 'mistral-medium', 'object': 'model'},
            {'id': 'mistral-small-3', 'object': 'model'},
            {'id': 'mistral-small', 'object': 'model'},
            {'id': 'mixtral-8x22b', 'object': 'model'},
            {'id': 'mixtral-8x7b', 'object': 'model'},
            {'id': 'codestral', 'object': 'model'},
            {'id': 'codestral-mamba', 'object': 'model'},
            {'id': 'voxtral-mini-4b', 'object': 'model'},
            {'id': 'voxtral', 'object': 'model'},
            {'id': 'mathstral', 'object': 'model'},
            {'id': 'pixtral', 'object': 'model'},
            {'id': 'mistral-embed', 'object': 'model'},
            {'id': 'mistral-vision', 'object': 'model'},
            {'id': 'mistral-funcall', 'object': 'model'},
            {'id': 'mistral-agent', 'object': 'model'},
            {'id': 'mistral-rag', 'object': 'model'},
            {'id': 'mistral-ocr', 'object': 'model'},
            {'id': 'mistral-summary', 'object': 'model'},
            {'id': 'mistral-search', 'object': 'model'},

            # ========== Cohere ==========
            {'id': 'command-r-plus', 'object': 'model'},
            {'id': 'command-r', 'object': 'model'},
            {'id': 'command-a-vision', 'object': 'model'},
            {'id': 'command', 'object': 'model'},
            {'id': 'tiny-aya', 'object': 'model'},
            {'id': 'cohere-embed', 'object': 'model'},
            {'id': 'cohere-summary', 'object': 'model'},
            {'id': 'cohere-generate', 'object': 'model'},
            {'id': 'cohere-chat', 'object': 'model'},
            {'id': 'cohere-classify', 'object': 'model'},
            {'id': 'cohere-rag', 'object': 'model'},
            {'id': 'cohere-agent', 'object': 'model'},
            {'id': 'cohere-tool-use', 'object': 'model'},

            # ========== 其他国际模型 ==========
            {'id': 'phi-4', 'object': 'model'},
            {'id': 'phi-3-medium', 'object': 'model'},
            {'id': 'phi-3-small', 'object': 'model'},
            {'id': 'phi-3-mini', 'object': 'model'},
            {'id': 'phi-2', 'object': 'model'},
            {'id': 'jamba-1.5', 'object': 'model'},
            {'id': 'jamba-1.5-large', 'object': 'model'},
            {'id': 'jamba-1.5-mini', 'object': 'model'},
            {'id': 'arcee-trinity-large', 'object': 'model'},
            {'id': 'arcee-trinity-mini', 'object': 'model'},
            {'id': 'arcee-trinity-nano', 'object': 'model'},
            {'id': 'sarvam-30b', 'object': 'model'},
            {'id': 'sarvam-105b', 'object': 'model'},
            {'id': 'step-3.5-flash', 'object': 'model'},
            {'id': 'step-3', 'object': 'model'},
            {'id': 'step-2', 'object': 'model'},
            {'id': 'step-1', 'object': 'model'},
            {'id': 'sera-14b', 'object': 'model'},
            {'id': 'intern-s1-pro', 'object': 'model'},
            {'id': 'intern-s1', 'object': 'model'},
            {'id': 'ace-step-1.5', 'object': 'model'},
            {'id': 'minicpm-o-4.5', 'object': 'model'},
            {'id': 'minicpm-sala', 'object': 'model'},
            {'id': 'minicpm-llama3', 'object': 'model'},
            {'id': 'minicpm-moe', 'object': 'model'},
            {'id': 'ovis2.6-30b', 'object': 'model'},
            {'id': 'nanbeige-4.1-3b', 'object': 'model'},
            {'id': 'nanbeige-4.1', 'object': 'model'},
            {'id': 'lyria-3', 'object': 'model'},
            {'id': 'lyria-2', 'object': 'model'},
            {'id': 'soulx-singer', 'object': 'model'},
            {'id': 'soulx', 'object': 'model'},
            {'id': 'moss-tts', 'object': 'model'},
            {'id': 'moss-agent', 'object': 'model'},
            {'id': 'moss-rag', 'object': 'model'},
            {'id': 'moss-embed', 'object': 'model'},
            {'id': 'moss-classify', 'object': 'model'},
            {'id': 'moss-generate', 'object': 'model'},
            {'id': 'moss-summary', 'object': 'model'},
            {'id': 'thinker', 'object': 'model'},
            {'id': 'thinker-express', 'object': 'model'},
            {'id': 'fantasyworld', 'object': 'model'},
            {'id': 'fantasyworld-pro', 'object': 'model'},
            {'id': 'fantasyworld-embed', 'object': 'model'},
            {'id': 'fantasyworld-agent', 'object': 'model'},
            {'id': 'fantasyworld-summary', 'object': 'model'},
            {'id': 'fantasyworld-recommend', 'object': 'model'},
            {'id': 'fantasyworld-search', 'object': 'model'},
            {'id': 'fantasyworld-translate', 'object': 'model'},
            {'id': 'fantasyworld-code', 'object': 'model'},
            {'id': 'fantasyworld-chat', 'object': 'model'},
            {'id': 'fantasyworld-vision', 'object': 'model'},
            {'id': 'fantasyworld-audio', 'object': 'model'},
            {'id': 'fantasyworld-video', 'object': 'model'},
            {'id': 'fantasyworld-3d', 'object': 'model'},
            {'id': 'fantasyworld-realtime', 'object': 'model'},

            # ========== 更多国内模型 ==========
            {'id': 'qoder', 'object': 'model'},
            {'id': 'qoder-plus', 'object': 'model'},
            {'id': 'qoder-pro', 'object': 'model'},
            {'id': 'qoder-lite', 'object': 'model'},
            {'id': 'qoder-code', 'object': 'model'},
            {'id': 'qoder-chat', 'object': 'model'},
            {'id': 'qoder-vision', 'object': 'model'},
            {'id': 'qoder-embed', 'object': 'model'},
            {'id': 'qoder-summary', 'object': 'model'},
            {'id': 'qoder-recommend', 'object': 'model'},
            {'id': 'qoder-search', 'object': 'model'},
            {'id': 'qoder-translate', 'object': 'model'},
            {'id': 'qoder-agent', 'object': 'model'},
            {'id': 'qoder-rag', 'object': 'model'},
            {'id': 'qoder-funcall', 'object': 'model'},
            {'id': 'qoder-web', 'object': 'model'},
            {'id': 'qoder-voice', 'object': 'model'},
            {'id': 'qoder-ocr', 'object': 'model'},
            {'id': 'qoder-realtime', 'object': 'model'},
            {'id': 'qoder-audio', 'object': 'model'},
            {'id': 'qoder-video', 'object': 'model'},
            {'id': 'qoder-3d', 'object': 'model'},
            {'id': 'qoder-science', 'object': 'model'},
            {'id': 'qoder-math', 'object': 'model'},
            {'id': 'qoder-code-review', 'object': 'model'},
            {'id': 'qoder-debug', 'object': 'model'},
            {'id': 'qoder-test', 'object': 'model'},
            {'id': 'qoder-deploy', 'object': 'model'},
            {'id': 'qoder-monitor', 'object': 'model'},
            {'id': 'qoder-log', 'object': 'model'},
            {'id': 'qoder-alert', 'object': 'model'},
            {'id': 'qoder-notify', 'object': 'model'},
            {'id': 'qoder-schedule', 'object': 'model'},
            {'id': 'qoder-workflow', 'object': 'model'},
            {'id': 'qoder-pipeline', 'object': 'model'},
            {'id': 'qoder-ci', 'object': 'model'},
            {'id': 'qoder-cd', 'object': 'model'},
            {'id': 'qoder-devops', 'object': 'model'},
            {'id': 'qoder-sre', 'object': 'model'},
            {'id': 'qoder-security', 'object': 'model'},
            {'id': 'qoder-compliance', 'object': 'model'},
            {'id': 'qoder-audit', 'object': 'model'},
            {'id': 'qoder-legal', 'object': 'model'},
            {'id': 'qoder-trade', 'object': 'model'},
            {'id': 'qoder-finance', 'object': 'model'},
            {'id': 'qoder-healthcare', 'object': 'model'},
            {'id': 'qoder-education', 'object': 'model'},
            {'id': 'qoder-retail', 'object': 'model'},
            {'id': 'qoder-manufacturing', 'object': 'model'},
            {'id': 'qoder-logistics', 'object': 'model'},
            {'id': 'qoder-energy', 'object': 'model'},
            {'id': 'qoder-agriculture', 'object': 'model'},
            {'id': 'qoder-construction', 'object': 'model'},
            {'id': 'qoder-realestate', 'object': 'model'},
            {'id': 'qoder-tourism', 'object': 'model'},
            {'id': 'qoder-hospitality', 'object': 'model'},
            {'id': 'qoder-entertainment', 'object': 'model'},
            {'id': 'qoder-gaming', 'object': 'model'},
            {'id': 'qoder-social', 'object': 'model'},
            {'id': 'qoder-communication', 'object': 'model'},
            {'id': 'qoder-media', 'object': 'model'},
            {'id': 'qoder-content', 'object': 'model'},
            {'id': 'qoder-marketing', 'object': 'model'},
            {'id': 'qoder-sales', 'object': 'model'},
            {'id': 'qoder-service', 'object': 'model'},
            {'id': 'qoder-support', 'object': 'model'},
            {'id': 'qoder-operation', 'object': 'model'},
            {'id': 'qoder-management', 'object': 'model'},
            {'id': 'qoder-leadership', 'object': 'model'},
            {'id': 'qoder-team', 'object': 'model'},
            {'id': 'qoder-project', 'object': 'model'},
            {'id': 'qoder-product', 'object': 'model'},
            {'id': 'qoder-design', 'object': 'model'},
            {'id': 'qoder-ux', 'object': 'model'},
            {'id': 'qoder-ui', 'object': 'model'},
        ]
    })
    
@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'alive', 'sessions': len(sessions)})

@app.route('/reply', methods=['POST'])
def reply():
    data = request.json
    session_id = str(data.get('id'))
    answer = data.get('answer')
    if session_id not in sessions:
        return jsonify({'error': 'session not found'}), 404
    sessions[session_id]['reply'] = answer
    sessions[session_id]['status'] = 'replied'
    
    print(f"{COLOR_PURPLE}{'─'*60}{COLOR_RESET}")
    print(f"{COLOR_PURPLE}回复成功 (会话 {session_id}){COLOR_RESET}")
    print(f"{COLOR_PURPLE}{'─'*60}{COLOR_RESET}\n")
    
    return jsonify({'status': 'ok'})

@app.route('/v1/chat/completions/<session_id>', methods=['GET'])
def get_completion(session_id):
    if session_id not in sessions:
        return jsonify({'error': 'session not found'}), 404
    session = sessions[session_id]
    if session['reply'] is not None:
        answer = session['reply']
        if not any(e['role'] == 'assistant' and e['content'] == answer for e in session['history']):
            session['history'].append({'role': 'assistant', 'content': answer})
        return jsonify({
            'id': f'chatcmpl-{session_id}',
            'object': 'chat.completion',
            'created': int(time.time()),
            'model': session['model'],
            'choices': [{
                'index': 0,
                'message': {'role': 'assistant', 'content': answer},
                'finish_reason': 'stop'
            }],
            'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}
        })
    return jsonify({'status': 'waiting'}), 202

# ================= 控制台回复线程 =================
def console_replier():
    while True:
        try:
            session_id = input(f"\n{COLOR_PURPLE}请输入要回复的 session_id: {COLOR_RESET}").strip()
            if session_id not in sessions:
                print(f"{COLOR_PURPLE}找不到 {session_id}{COLOR_RESET}")
                continue
            
            print_conversation(session_id)
            
            answer = input(f"{COLOR_AI}[AI]{COLOR_RESET} ").strip()
            if not answer:
                print(f"{COLOR_PURPLE}回复不能为空{COLOR_RESET}")
                continue
            
            resp = requests.post('http://localhost:5000/reply', 
                                json={'id': session_id, 'answer': answer},
                                timeout=5)
            if resp.status_code == 200:
                sessions[session_id]['history'].append({'role': 'assistant', 'content': answer})
                print(f"{COLOR_PURPLE}已回复 session {session_id}{COLOR_RESET}")
            else:
                print(f"{COLOR_PURPLE}提交失败: {resp.text}{COLOR_RESET}")
        except KeyboardInterrupt:
            print(f"\n{COLOR_PURPLE}退出回复线程{COLOR_RESET}")
            break
        except Exception as e:
            print(f"{COLOR_PURPLE}错误: {e}{COLOR_RESET}")

# ================= 启动 =================
if __name__ == '__main__':
    t = threading.Thread(target=console_replier, daemon=True)
    t.start()
    
    print("human API by WIhee")  
    print("https://github.com/WIheee/human-api")
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)