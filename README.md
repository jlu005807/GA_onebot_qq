# OneBot QQ Bot

## 简介
基于 GeneraticAgent 的 OneBot v11 WebSocket 适配器，支持私聊与群聊（群聊需 @ 机器人）。

## 部署要求

> **重要：本项目必须部署在 GenericAgent 项目的 `temp` 文件夹下运行。**

### 目录结构要求
```
D:\GenericAgent\                  ← GA 项目根目录
├── agentmain.py
├── temp\                          ← 放在这里
│   └── onebot_qq\                 ← 本项目
│       ├── src/
│       ├── .env
│       └── ...
```

### Python 环境
直接使用 GenericAgent 的 Python 环境即可，无需额外配置虚拟环境。

```
cd D:\GenericAgent\temp\onebot_qq
python src/main.py
```

## 目录结构
- src/main.py: 启动入口
- src/onebot_app.py: OneBot 事件处理与消息收发
- src/onebot_config.py: 配置与 .env 读取
- src/onebot_state.py: 运行期状态
- src/onebot_paths.py: 路径注入（自动向上查找 GA 根目录）

## 依赖
- Python 3.8+
- websockets

安装依赖（使用 GA 环境）:
```
pip install websockets
```

## 快速开始
1) 将项目放到 GA 的 `temp` 目录下:
```
cd D:\GenericAgent\temp
git clone <仓库地址> onebot_qq
```
2) 复制配置模板:
```
copy .env.example .env
```
3) 修改 .env 中的管理员 QQ 与 OneBot 地址
4) 在 NapCat 中启用 OneBot v11 正向 WS
5) 启动程序:
```
python src/main.py
```

## 配置
在项目根目录创建 .env（可直接复制 .env.example），配置项优先级:
1) 系统环境变量
2) .env
3) mykeys 兜底（仅 ws_url 和 allowed_users）

可用配置项:
```
ONEBOT_WS_URL=ws://127.0.0.1:8080
ONEBOT_ADMIN_QQ=123456789,987654321
ONEBOT_ALLOWED_USERS=*
ONEBOT_ALLOW_GROUP=1
ONEBOT_ALLOWED_GROUPS=*
ONEBOT_ACCESS_TOKEN=
ONEBOT_PLAIN_TEXT_HINT=请用纯文本回复，不要使用Markdown格式。
ONEBOT_MAX_MSG_LENGTH=500
ONEBOT_MAX_QUEUE_SIZE=5
ONEBOT_DATA_DIR=data
ONEBOT_MAX_FILE_BYTES=10485760
```

复制示例配置:
```
copy .env.example .env
```

关闭纯文本提示词（可选）:
```
ONEBOT_PLAIN_TEXT_HINT=0
```

## NapCat 配置
- 启用 OneBot v11 WebSocket 服务（正向 WS）
- 监听地址与端口需与 `ONEBOT_WS_URL` 一致（默认 127.0.0.1:8080）
- 路径设置为 `/onebot/v11/ws`
- 如配置了访问令牌，需与 `ONEBOT_ACCESS_TOKEN` 保持一致
- 确保机器人账号已登录，否则无法接收消息
- 附件需提供可访问的 `url` 字段，否则会提示“附件未保存”

## 启动
```
python src/main.py
```

## 运行说明
- 群聊必须 @ 机器人才会回复
- 单条消息长度上限为 500 字
- 管理员命令仅管理员可用
- 自动忽略机器人自身消息，文本会移除 CQ 码
- 消息长度与队列长度可在 .env 中配置
- 群聊可在 .env 开关，并支持群聊白名单
- 图片/语音/文件会保存到 data/ 下，单文件上限 10MB
- NapCat 会在消息段提供 url（或 file 为 URL），无 URL 时会跳过并提示

## 消息流程
- 接收事件并去重（message_id）
- 群聊检查：开关、白名单、必须 @ 机器人
- 权限检查：管理员自动放行，普通用户需在允许列表
- 命令优先处理；普通消息进行敏感关键词拦截
- 下载附件并入队，队列 worker 串行处理

## 发送给 GA 的内容格式
顺序固定为：上下文行 → 文本 → 附件路径列表 → 纯文本提示词。

示例：
```
上下文: 群聊=1 管理员=0

请帮我看看这张图

附件1: type=image path=D:\\...\\data\\image\\pic_ab12cd.jpg size=512KB

请用纯文本回复，不要使用Markdown格式。
```

## 权限与群聊策略
- 管理员集合由 `ONEBOT_ADMIN_QQ` 配置
- 用户允许列表由 `ONEBOT_ALLOWED_USERS` 配置（`*` 表示全部）
- 群聊开关：`ONEBOT_ALLOW_GROUP`，群白名单：`ONEBOT_ALLOWED_GROUPS`
- 群聊消息必须 @ 机器人

## 附件与存储
- 目录：`data/image`、`data/record`、`data/file`
- 默认单文件 10MB（`ONEBOT_MAX_FILE_BYTES` 可调）
- 仅当消息段包含可访问 `url` 时才下载

## 日志
- 每次启动会清空并重新记录
- data/message.log: 记录消息（时间、用户、群号、管理员、文本）
- data/audit.log: 记录附件（时间、用户、群号、类型、路径、大小）
