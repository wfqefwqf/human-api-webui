# -*- coding: utf-8 -*-
"""
Human-API 配置管理模块

负责管理所有运行时配置项，支持：
- 配置文件持久化（JSON 格式存储到 data/config.json）
- 运行时热更新配置
- 默认值兜底
- 线程安全读写
"""

import json
import os
import threading

# 配置文件存储路径
CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

# ==================== 默认配置 ====================
DEFAULT_CONFIG = {
    "api_key": "",              # API 访问密钥，为空则不鉴权
    "timeout": 120,             # 人工回复超时时间（秒），超时自动回复默认消息
    "timeout_reply": "抱歉，当前人工客服繁忙，请稍后再试。",  # 超时默认回复
    "host": "0.0.0.0",         # 监听地址
    "port": 5000,              # 监听端口
    "max_sessions": 500,       # 最大会话数量
    "max_history_per_session": 200,  # 每个会话最大历史消息数
}

# 线程锁，保证配置读写安全
_lock = threading.Lock()

# 当前运行时配置（内存缓存）
_current_config = {}


def _ensure_dir():
    """确保配置目录存在"""
    os.makedirs(CONFIG_DIR, exist_ok=True)


def _load_from_file() -> dict:
    """从文件加载配置，文件不存在则返回空字典"""
    if not os.path.exists(CONFIG_FILE):
        return {}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def _save_to_file(config: dict):
    """将配置写入文件"""
    _ensure_dir()
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def load_config() -> dict:
    """
    加载配置，合并默认值和文件存储的值。

    优先级：文件中的值 > 默认值
    """
    global _current_config
    with _lock:
        file_config = _load_from_file()
        merged = {**DEFAULT_CONFIG, **file_config}
        _current_config = merged
        _save_to_file(merged)
    return dict(_current_config)


def get(key: str, default=None):
    """获取单个配置项"""
    with _lock:
        return _current_config.get(key, default)


def update_config(updates: dict) -> dict:
    """
    批量更新配置项并持久化。

    Args:
        updates: 需要更新的键值对字典

    Returns:
        更新后的完整配置
    """
    global _current_config
    with _lock:
        _current_config.update(updates)
        _save_to_file(_current_config)
    return dict(_current_config)


def get_all() -> dict:
    """获取当前全部配置（副本）"""
    with _lock:
        return dict(_current_config)


def reset_to_default() -> dict:
    """重置所有配置为默认值"""
    global _current_config
    with _lock:
        _current_config = dict(DEFAULT_CONFIG)
        _save_to_file(_current_config)
    return dict(_current_config)


# 模块加载时自动读取配置
load_config()
