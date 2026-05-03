# OneBot QQ Bot

## 简介
基于 GeneraticAgent 的 OneBot v11 WebSocket 适配器，支持私聊与群聊（群聊需 @ 机器人）。

## 目录结构
- src/main.py: 启动入口
- src/onebot_app.py: OneBot 事件处理与消息收发
- src/onebot_config.py: 配置与 .env 读取
- src/onebot_state.py: 运行期状态
- src/onebot_paths.py: 路径注入

## 依赖
- Python 3.8+
- websockets

安装依赖:
```
pip install websockets
```

## 配置
在项目根目录创建 .env（已提供示例），配置项优先级:
1) 系统环境变量
2) .env
3) mykeys 兜底（仅 ws_url 和 allowed_users）

可用配置项:
```
ONEBOT_WS_URL=ws://127.0.0.1:8080/onebot/v11/ws
ONEBOT_ADMIN_QQ=123456789,987654321
ONEBOT_ALLOWED_USERS=*
ONEBOT_ACCESS_TOKEN=
```

## 启动
```
python src/main.py
```

## 运行说明
- 群聊必须 @ 机器人才会回复
- 单条消息长度上限为 500 字
- 管理员命令仅管理员可用
