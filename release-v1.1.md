# Human-API WebUI v1.1 发布

## 🎉 新增功能

### 1. AI 自动托管
- 新消息进来后自动调用配置的 AI API 生成回复
- 无需人工干预，完全自动化运行
- 支持在设置面板一键开启/关闭

### 2. 心跳自动回复
- 匹配关键词（如 `hi`, `hello`, `ping`）的请求自动原样回复
- 不进入会话列表，不占用人工资源
- 可自定义心跳关键词列表

## 🐛 修复

- 修复清空会话时未通知等待中的 API 请求导致 500 错误
- 修复会话去重逻辑，相同模型相同提问复用会话

## 📦 下载

### Windows
- [human-api-webui-v1.1-windows.zip](https://github.com/wfqefwqf/human-api-webui/releases/download/v1.1/human-api-webui-v1.1-windows.zip)

### Linux
- [human-api-webui-v1.1-linux.tar.gz](https://github.com/wfqefwqf/human-api-webui/releases/download/v1.1/human-api-webui-v1.1-linux.tar.gz)

## 🚀 使用

```bash
# 解压后运行
pip install -r requirements.txt
python app.py
```

或双击 `start.bat`（Windows）/ `start.sh`（Linux）