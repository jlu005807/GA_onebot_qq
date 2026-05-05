# onebot_qq

`onebot_qq` 是一个基于 `OneBot v11` 的 QQ 机器人网关，负责把 NapCat 推送的消息转发给 `GenericAgent`，并把 Agent 回复发送回 QQ。
本项目的github仓库位于[GA_onebot_qq](https://github.com/jlu005807/GA_onebot_qq.git),对于安装并且已经配置了GenericAgent的用户,可以直接克隆项目到GenericAgent的temp文件夹下，或者让GA仔细阅读[部署教程—SOP](https://fudankw.cn/sophub/sops/69f9806ba1d45b6c2d5f2fd0)进行完成项目克隆，配置并且安装napcat

## 1. 功能概览

- 支持私聊与群聊（群聊需 `@机器人` 才响应）。
- 支持图片/语音/文件附件保存，并把附件类型与路径传给 Agent。
- 支持管理员与普通用户身份区分，并将权限策略写入提示词。
- 支持按用户排队处理消息，避免并发上下文串扰。
- 支持断线自动重连，支持 token 鉴权。

## 2. 项目结构

```text
onebot_qq/
├─ src/
│  ├─ main.py               # 启动入口
│  ├─ onebot_app.py         # 主流程编排（连接、事件分发、队列）
│  ├─ onebot_message.py     # 消息解析、@处理、提示词构建
│  ├─ onebot_attachment.py  # 附件下载/保存（image, record, file）
│  ├─ onebot_ws.py          # WS URL 规范化、token URL 拼接
│  ├─ onebot_async.py       # Python 3.8+ 线程兼容工具
│  ├─ onebot_config.py      # .env 与运行配置加载
│  ├─ onebot_state.py       # 运行时状态
│  └─ onebot_paths.py       # GenericAgent 路径注入
├─ .env.example
├─ .env
└─ README.md
```

## 3. 运行前要求

1. 必须放在 `GenericAgent/temp` 目录下运行（依赖 `agentmain.py` 路径注入）。
2. Python 3.8+。
3. 安装依赖：注意可以和GenericAgent使用同一个虚拟环境则不再需要安装额外的依赖即可以跳过下面的一步，但是如果发现运行缺失库需要手动或者让GA进行安装

```bash
pip install -r requirements.txt
```

## 4. 快速开始

1. 进入项目目录：

```bash
cd D:\GenericAgent\temp\onebot_qq
```

2. 复制配置模板：

```bash
copy .env.example .env
```

3. 修改 `.env`（至少确认 `ONEBOT_WS_URL`、`ONEBOT_ADMIN_QQ`、`ONEBOT_ACCESS_TOKEN`）。

4. 在 NapCat 中启用 OneBot v11 WebSocket（见下一节）。

5. 启动：

```bash
python src/main.py
```

## 5. NapCat 配置说明（重点）

建议使用 **OneBot v11 正向 WS**（NapCat 作为 WS 服务端，本项目作为客户端连接）。

- 推荐地址：`ws://127.0.0.1:8080/onebot/v11/ws`
- 若你在 NapCat 配置了 `access_token`，请保证与 `.env` 中 `ONEBOT_ACCESS_TOKEN` 完全一致。
- 建议消息格式设为 `array`（本项目对 `string/array` 都兼容，但 `array` 更稳定）。

如果你看到日志提示 token 验证失败（如 `retcode=1403`），优先检查 token 是否一致。

### 5.1 NapCat WebUI 配置教程（一步步）

以下以 **正向 WS** 为例（推荐）：

1. 启动 NapCat，并登录你的 QQ 机器人账号。  
2. 打开 NapCat WebUI（通常是 `http://127.0.0.1:6099`，也可能以控制台输出为准）。  
3. 进入网络/适配器配置，找到 **OneBot v11**。  
4. 启用 **WebSocket 服务端（正向 WS）**。  
5. 配置监听参数：
   - `host`: `127.0.0.1`
   - `port`: `8080`（或你自定义端口）
   - `path`: `/onebot/v11/ws`
6. 配置 `access_token`（可选）：
   - 如果设置了 token，必须与 `.env` 的 `ONEBOT_ACCESS_TOKEN` 完全一致。
   - 如果不设置 token，`.env` 里也留空。
7. 消息格式建议设为 `array`。  
8. 保存配置并确认 OneBot 服务已启用。  
9. 回到本项目，确保 `.env`：
   - `ONEBOT_WS_URL=ws://127.0.0.1:8080/onebot/v11/ws`
   - `ONEBOT_ACCESS_TOKEN=<与你在 NapCat 填的一致>`
10. 启动 `python src/main.py`

### 5.2 快速自检清单

- 私聊机器人一句话，确认有回复。  
- 群里 `@机器人` 发一句话，确认有回复。  
- 发一张图/一段语音/一个文件，确认 `data/image|record|file` 有落盘。  
- 若失败，先看 `temp/onebot.log` 是否出现 `retcode=1403` 或连接拒绝。  

## 6. .env 配置详解

| 键 | 默认值 | 必填 | 说明 |
|---|---|---|---|
| `ONEBOT_WS_URL` | `ws://127.0.0.1:8080/onebot/v11/ws` | 是 | NapCat OneBot WS 地址。若只填到端口，程序会自动补 `/onebot/v11/ws`。 |
| `ONEBOT_ADMIN_QQ` | 空 | 建议 | 管理员 QQ，多个用逗号分隔。 |
| `ONEBOT_ALLOWED_USERS` | `*` | 否 | 允许使用的用户列表；`*` 表示全部。管理员总是放行。 |
| `ONEBOT_ALLOW_GROUP` | `1` | 否 | 是否启用群聊消息处理。 |
| `ONEBOT_ALLOWED_GROUPS` | `*` | 否 | 允许处理的群号列表；`*` 表示全部群。 |
| `ONEBOT_ACCESS_TOKEN` | 空 | 按需 | NapCat WS token。设置后会同时用于 Header 与 URL query。 |
| `ONEBOT_PLAIN_TEXT_HINT` | 内置提示 | 否 | 附加给 Agent 的文本提示。设为 `0/false/off` 可关闭。 |
| `ONEBOT_MAX_MSG_LENGTH` | `500` | 否 | 单条消息最大长度。 |
| `ONEBOT_MAX_QUEUE_SIZE` | `5` | 否 | 每个用户的待处理队列上限。 |
| `ONEBOT_DATA_DIR` | `data` | 否 | 附件保存目录。相对路径基于**当前运行目录**解析。 |
| `ONEBOT_MAX_FILE_BYTES` | `10485760` | 否 | 单附件最大字节数（默认 10MB）。 |

## 7. 附件处理规则

- 支持段类型：`image`、`record`、`file`。
- 保存路径：
  - `data/image`
  - `data/record`
  - `data/file`
- 下载源优先级：
  1. `data.url`（http/https）
  2. 本地 `data.path` 或本地 `data.file`
  3. `base64://...` 或 `data:...;base64,...`
- 成功保存后会把附件信息注入提示词，格式如下：

```text
attachment1: type=image path=D:\...\data\image\xxx.jpg size=123KB
```

## 8. 权限策略

- 网关会识别 `管理员/非管理员` 身份并写入提示词。
- 非管理员请求文件级/进程级/硬件级操作时，期望 Agent 拒绝并提示联系管理员。
- 入口层不做危险关键词硬拦截，避免误伤正常对话。

## 9. 运行与验证

启动后建议按顺序验证：

1. 私聊发送文本，确认可回复。
2. 群聊 `@机器人` 发送文本，确认可回复。
3. 发送图片/语音/文件，确认 `data/*` 有落盘文件。
4. 查看回复内容，确认 Agent 能看到附件路径信息。

## 10. 常见问题排查

### Q1: 连不上 NapCat（connection refused）
- 检查 NapCat 是否启动并开启 OneBot WS。
- 检查 `ONEBOT_WS_URL` 的 host/port/path。

### Q2: 日志出现 token 验证失败（retcode=1403）
- 检查 NapCat token 与 `ONEBOT_ACCESS_TOKEN` 是否一致。

### Q3: 群聊不回复
- 确认消息有 `@机器人`。
- 确认 `ONEBOT_ALLOW_GROUP=1`。
- 确认群号在 `ONEBOT_ALLOWED_GROUPS`（或使用 `*`）。

### Q4: 收到消息但没有附件
- 检查 NapCat 上报段是否包含 `url/path/file/base64` 中至少一种可用来源。
- 检查 `ONEBOT_MAX_FILE_BYTES` 是否过小。

### Q5: 启动报 websockets 参数错误
- 已兼容 `additional_headers/extra_headers` 差异。
- 仍有问题时，建议升级 `websockets` 到较新版本后重试。

## 11. 备注

- 本项目与 GenericAgent 紧耦合，建议固定在 `GenericAgent/temp/onebot_qq` 下运行。
- 若需二次开发，优先在 `onebot_message.py`（提示词策略）和 `onebot_attachment.py`（附件策略）扩展。
