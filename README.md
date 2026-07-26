# onebot_qq

`onebot_qq` 是一个基于 `OneBot v11` 的 QQ 机器人网关，负责把 NapCat 推送的消息转发给 `GenericAgent`，并把 Agent 回复发送回 QQ。
本项目的github仓库位于[GA_onebot_qq](https://github.com/jlu005807/GA_onebot_qq.git),对于安装并且已经配置了GenericAgent的用户,可以直接克隆项目到GenericAgent的temp文件夹下，或者让GA仔细阅读[部署教程—SOP](https://fudankw.cn/sophub/sops/69f9806ba1d45b6c2d5f2fd0)进行完成项目克隆，配置并且安装napcat

## 目录

1. [功能概览](#1-功能概览)
2. [项目结构](#2-项目结构)
3. [运行前要求](#3-运行前要求)
4. [快速开始](#4-快速开始)
5. [NapCat 配置说明（重点）](#5-napcat-配置说明重点)
6. [.env 配置详解](#6-env-配置详解)
7. [附件处理规则](#7-附件处理规则)
8. [权限策略](#8-权限策略) · [聊天命令](#81-聊天命令)
9. [运行与验证](#9-运行与验证)
10. [常见问题排查](#10-常见问题排查)
11. [备注](#11-备注)
12. [运行测试](#12-运行测试)
13. [已知限制与安全须知](#13-已知限制与安全须知) ← **上线到公开群聊前请务必读这一节**

## 1. 功能概览

- 支持私聊与群聊，可配置群聊触发模式（必须 `@` / 任意消息 / 触发词）。
- 支持图片/语音/文件附件保存，并把附件类型与路径传给 Agent；附件按保留期自动清理。
- 支持管理员与普通用户身份区分，并可按需注入权限策略提示词。
- 按用户串行排队处理消息，同一用户的消息不会并发交给 Agent。
- 支持把触发消息前的最近 `n` 条消息作为上下文传给 GA（`n=0` 关闭）。
- 支持断线自动重连（指数退避）、token 鉴权、Ctrl+C 优雅退出。
- 支持 Windows / Linux 双平台运行（含 `.venv` 路径自动兼容）。
- 自带零依赖单元测试（标准库 `unittest`）。

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
│  ├─ onebot_state.py       # 运行时状态、排队消息结构、消息去重
│  └─ onebot_paths.py       # GenericAgent 路径注入
├─ tests/                   # 单元测试（标准库 unittest，无额外依赖）
├─ data/                    # 附件落盘目录（运行时创建，已 gitignore）
├─ temp/                    # 运行日志（每次启动一个文件，已 gitignore）
├─ requirements.txt
├─ .env.example
├─ .env.example-en
├─ .env
└─ README.md
```

## 3. 运行前要求

1. 必须放在 `GenericAgent/temp` 目录下运行（依赖 `agentmain.py` 路径注入）。
2. 本项目源码兼容 Python 3.8+，但实际以父项目 GenericAgent 的要求为准（`pyproject.toml` 要求 `>=3.10,<3.14`）。
3. 支持系统：Windows / Linux（代码已兼容两端路径差异）。
4. 安装依赖：注意可以和GenericAgent使用同一个虚拟环境则不再需要安装额外的依赖即可以跳过下面的一步，但是如果发现运行缺失库需要手动或者让GA进行安装

```bash
pip install -r requirements.txt
```

## 4. 快速开始

1. 进入项目目录：

```bash
# Windows
cd D:\GenericAgent\temp\onebot_qq

# Linux
cd ~/GenericAgent/temp/onebot_qq
```

2. 复制配置模板：

```bash
# Windows
copy .env.example .env

# Linux
cp .env.example .env
```

如需英文注释模板，可使用：

```bash
# Windows
copy .env.example-en .env

# Linux
cp .env.example-en .env
```

3. 修改 `.env`（至少确认 `ONEBOT_WS_URL`；建议确认 `ONEBOT_GROUP_REQUIRE_AT`、`ONEBOT_GROUP_TRIGGER_WORDS`、`ONEBOT_CONTEXT_MESSAGES`）。

4. 在 NapCat 中启用 OneBot v11 WebSocket（见下一节）。

5. 启动：

```bash
# Windows
python src/main.py

# Linux（若系统默认 python 指向 Python2，请使用 python3）
python3 src/main.py
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
- 群里发一句话，按你的触发策略验证可回复（`@` / 触发词 / 任意消息）。  
- 发一张图/一段语音/一个文件，确认 `data/image|record|file` 有落盘。  
- 若失败，先看 `temp/onebot_YYYYMMDD_HHMMSS.log` 是否出现 `retcode=1403` 或连接拒绝（每次启动会生成新的时间戳日志，默认只保留最近 10 个）。  
- 连不上时日志里的重连间隔会逐步拉长（5s → 10s → … → 60s），这是正常的退避行为。  

## 6. .env 配置详解

取值规则（与 `python-dotenv` 一致）：

- 环境变量优先于 `.env` 文件。
- 未加引号的值会被**行尾注释**截断，`#` 前必须有空格或制表符：
  - `ONEBOT_MAX_MSG_LENGTH=500   # 注释` → `500`
  - `ONEBOT_ACCESS_TOKEN=abc#def` → `abc#def`（紧贴的 `#` 属于值本身）
- 值里含 `#`、或需要保留首尾空格时**必须加引号**，否则会被截断且没有任何提示：
  - `ONEBOT_PLAIN_TEXT_HINT="请用纯文本回复 #不要用markdown"`
- 支持 `export KEY=value` 写法；文件可带 BOM。

| 键 | 默认值 | 必填 | 说明 |
|---|---|---|---|
| `ONEBOT_WS_URL` | `ws://127.0.0.1:8080/onebot/v11/ws` | 是 | NapCat OneBot WS 地址。若只填到端口，程序会自动补 `/onebot/v11/ws`。 |
| `ONEBOT_ADMIN_QQ` | 空 | 建议 | 管理员 QQ，多个用逗号分隔。 |
| `ONEBOT_ALLOWED_USERS` | `*` | 否 | 允许使用的用户列表；`*` 表示全部。管理员总是放行。 |
| `ONEBOT_ALLOW_GROUP` | `1` | 否 | 是否启用群聊消息处理。 |
| `ONEBOT_ALLOWED_GROUPS` | `*` | 否 | 允许处理的群号列表；`*` 表示全部群。 |
| `ONEBOT_GROUP_REQUIRE_AT` | 空 | 否 | 群聊触发模式。`1/true`=需 `@`（触发词也可触发）；`0/false/空`=任意消息可触发。 |
| `ONEBOT_GROUP_TRIGGER_WORDS` | 空 | 否 | 群聊触发词，逗号分隔，命中任一即可触发回复。 |
| `ONEBOT_CONTEXT_MESSAGES` | `0` | 否 | 向 GA 传递触发前历史消息条数。`0` 关闭，最大 `20`。 |
| `ONEBOT_ACCESS_TOKEN` | 空 | 按需 | NapCat WS token。设置后会同时用于 Header 与 URL query。 |
| `ONEBOT_PLAIN_TEXT_HINT` | 内置提示 | 否 | 附加给 Agent 的文本提示。设为 `0/false/off` 可关闭。 |
| `ONEBOT_MAX_MSG_LENGTH` | `500` | 否 | **收到的**单条消息文本长度上限，超出会回一条提示并忽略该消息（不限制回复长度，回复长度看 `ONEBOT_SPLIT_LIMIT`）。 |
| `ONEBOT_MAX_QUEUE_SIZE` | `5` | 否 | 每个用户的待处理队列上限，排满时会回 `Queue is full` 提示。 |
| `ONEBOT_DATA_DIR` | `data` | 否 | 附件保存目录。相对路径基于**项目根目录**解析（与启动时的工作目录无关）。 |
| `ONEBOT_MAX_FILE_BYTES` | `10485760` | 否 | 单附件最大字节数（默认 10MB）。 |
| `ONEBOT_ATTACHMENT_TTL_HOURS` | `24` | 否 | 附件保留小时数，后台定期清理超期文件。`0`=不清理。 |
| `ONEBOT_LOCAL_SOURCE_DIRS` | 空 | 否 | 允许作为本地附件来源的目录白名单，逗号分隔。留空=不限制。 |
| `ONEBOT_SPLIT_LIMIT` | `1500` | 否 | 单条回复的拆分长度上限。拆分不会切断 `[CQ:...]` 段；单个段本身超长时该分片会略微超出上限。 |
| `ONEBOT_LOCK_PORT` | `19529` | 否 | 单实例互斥端口。同机跑第二个实例时需改。 |
| `ONEBOT_LOG_FILE` | `onebot.log` | 否 | 日志基名，实际文件带启动时间戳，落在 `temp/`。 |
| `ONEBOT_LOG_KEEP` | `10` | 否 | 保留最近多少个启动日志。`0`=不清理。 |

### 6.1 常用配置组合（可直接参考）

1. 仅 `@机器人` 才回复（群内更克制）  
   - `ONEBOT_GROUP_REQUIRE_AT=1`  
   - `ONEBOT_GROUP_TRIGGER_WORDS=`  
   - `ONEBOT_CONTEXT_MESSAGES=0`

2. 任意群消息都可触发（测试联调常用）  
   - `ONEBOT_GROUP_REQUIRE_AT=`（留空）  
   - `ONEBOT_GROUP_TRIGGER_WORDS=`  
   - `ONEBOT_CONTEXT_MESSAGES=0`

3. `@` 或触发词触发，并附带最近 3 条上下文（推荐）  
   - `ONEBOT_GROUP_REQUIRE_AT=1`  
   - `ONEBOT_GROUP_TRIGGER_WORDS=小宇,助手,bot`  
   - `ONEBOT_CONTEXT_MESSAGES=3`

## 7. 附件处理规则

- 支持段类型：`image`、`record`、`file`。
- 保存路径：
  - `data/image`
  - `data/record`
  - `data/file`
- 下载源优先级：
  1. `data.url`（**仅** http/https，且不跟随跨协议重定向）
  2. 本地 `data.path` 或本地 `data.file`（可用 `ONEBOT_LOCAL_SOURCE_DIRS` 限定目录）
  3. `base64://...` 或 `data:...;base64,...`
- 群文件/私聊文件若上报里没有可用来源，会先通过 `get_group_file_url` / `get_private_file_url` / `get_file`
  这几个 OneBot 动作补齐直链，再进入上面的下载流程。
- 超过 `ONEBOT_MAX_FILE_BYTES` 的文件会被拒绝并回一条 `Attachment save failed`。
- 落盘文件名会做安全清洗（去掉路径分隔符等），但保留中文等原始文字，并追加一段随机后缀避免重名。
- 附件默认保留 24 小时后由后台任务清理，见 `ONEBOT_ATTACHMENT_TTL_HOURS`。
- 成功保存后会把附件信息注入提示词，格式如下：

```text
attachment1: type=image path=D:\...\data\image\xxx.jpg size=123KB
# Linux 示例：
# attachment1: type=image path=/home/you/GenericAgent/temp/onebot_qq/data/image/xxx.jpg size=123KB
```

## 8. 权限策略

- 当配置了 `ONEBOT_ADMIN_QQ` 时，网关会识别 `管理员/非管理员` 身份并写入提示词。
- 当 `ONEBOT_ADMIN_QQ` 为空时，不向 GA 注入管理员相关策略提示词。
- 非管理员请求文件级/进程级/硬件级操作时，期望 Agent 拒绝并提示联系管理员。
- 入口层不做危险关键词硬拦截，避免误伤正常对话。

> 注意：这里的「非管理员禁止」是**写进提示词交给模型自觉遵守**的，不是网关层的硬约束。
> 详见第 13 节。

### 8.1 聊天命令

直接在私聊或群聊里发送即可（命令由 GenericAgent 的公共前端层处理）。

| 命令 | 权限 | 说明 |
|---|---|---|
| `/help` | 所有人 | 显示命令帮助。 |
| `/status` | 所有人 | 查看 Agent 是否在运行、当前 LLM。 |
| `/btw <内容>` | 所有人 | 不打断当前任务的旁路提问。 |
| `/review` | 所有人 | 触发一次 review 任务。 |
| `/stop` | 仅管理员 | 中止当前任务。 |
| `/new` | 仅管理员 | 清空会话上下文。 |
| `/continue` | 仅管理员 | 继续上一轮任务。 |
| `/restore` | 仅管理员 | 从历史记录恢复上下文。 |
| `/llm [n]` | 仅管理员 | 列出或切换 LLM 后端。 |

> `ONEBOT_ADMIN_QQ` 留空时**没有任何人**是管理员，上表的管理员命令对所有人都不可用。
> 需要用这些命令就必须配置至少一个管理员 QQ。
>
> `/llm`、`/new`、`/stop`、`/restore` 影响的是**整个进程的共享 Agent**，会作用到所有会话，
> 因此限定管理员使用。

## 9. 运行与验证

启动后建议按顺序验证：

1. 私聊发送文本，确认可回复。
2. 群聊发送文本，按当前触发配置验证可回复。
3. 发送图片/语音/文件，确认 `data/*` 有落盘文件。
4. 查看回复内容，确认 Agent 能看到附件路径信息。

## 10. 常见问题排查

### Q1: 连不上 NapCat（connection refused）
- 检查 NapCat 是否启动并开启 OneBot WS。
- 检查 `ONEBOT_WS_URL` 的 host/port/path。

### Q2: 日志出现 token 验证失败（retcode=1403）
- 检查 NapCat token 与 `ONEBOT_ACCESS_TOKEN` 是否一致。

### Q3: 群聊不回复
- 若 `ONEBOT_GROUP_REQUIRE_AT=1`，确认消息有 `@机器人` 或命中触发词。
- 若希望任意群消息都触发，设置 `ONEBOT_GROUP_REQUIRE_AT=`（留空）或 `0`。
- 确认 `ONEBOT_ALLOW_GROUP=1`。
- 确认群号在 `ONEBOT_ALLOWED_GROUPS`（或使用 `*`）。

### Q4: 收到消息但没有附件
- 检查 NapCat 上报段是否包含 `url/path/file/base64` 中至少一种可用来源。
- 检查 `ONEBOT_MAX_FILE_BYTES` 是否过小。

### Q5: 启动报 websockets 参数错误
- 已兼容 `additional_headers/extra_headers` 差异。
- 仍有问题时，建议升级 `websockets` 到较新版本后重试。

### Q6: 为什么看不到“管理员/非管理员”策略提示？
- 若 `ONEBOT_ADMIN_QQ` 留空，网关不会向 GA 注入管理员策略提示词（这是当前设计）。
- 如需启用该策略，请给 `ONEBOT_ADMIN_QQ` 配置至少一个管理员 QQ。

### Q7: Linux 下启动后提示找不到依赖或模块？
- 优先确认是否在 `GenericAgent` 的虚拟环境中运行。
- 已兼容 `.venv/Lib/site-packages`（Windows）和 `.venv/lib/python*/site-packages`（Linux）自动注入。

### Q8: 提示端口被占用 / 说已有实例在运行？
- 单实例互斥默认占用 `19529`。确认没有残留进程，或改 `ONEBOT_LOCK_PORT` 换一个端口。

### Q9: `data/` 里的附件不见了？
- 默认保留 24 小时，超期会被后台清理。需要长期留存请调大或关闭 `ONEBOT_ATTACHMENT_TTL_HOURS`。

### Q10: 管理员命令（`/stop`、`/new`、`/llm`）说没有权限？
- 确认 `ONEBOT_ADMIN_QQ` 已配置且包含你的 QQ。留空时没有任何人是管理员。

### Q11: 回复内容像是少了一段？
- 网关会过滤掉状态行、工具调用模板和代码围栏标记，规则见第 13.4 节。
- 若确认是正文被误删，把误删的原文贴出来对照 `onebot_message.TOOL_CALL_RE` 排查。

### Q12: 附件下载总是失败？
- 只允许 `http(s)` 直链，且不跟随跨协议重定向。
- 若配了 `ONEBOT_LOCAL_SOURCE_DIRS`，本地来源必须落在白名单目录内。
  被白名单挡掉时回复里会明确写 `local source blocked by ONEBOT_LOCAL_SOURCE_DIRS: <路径>`，
  与「确实没有可用来源」区分开。
- 也检查 `ONEBOT_MAX_FILE_BYTES` 是否小于实际文件。

## 11. 备注

- 本项目与 GenericAgent 紧耦合，建议固定在 `GenericAgent/temp/onebot_qq` 下运行。
- 若需二次开发，优先在 `onebot_message.py`（提示词策略）和 `onebot_attachment.py`（附件策略）扩展。

## 12. 运行测试

测试只用标准库，**不需要安装任何额外依赖**：

```bash
# Windows / Linux 通用
python -m unittest discover -s tests -t .

# 已安装 pytest 的话也可以
python -m pytest tests
```

覆盖范围与是否需要父项目：

| 测试模块 | 被测对象 | 需要 GenericAgent |
|---|---|---|
| `test_onebot_ws.py` | WS URL 规范化、token 注入、日志脱敏 | 否 |
| `test_onebot_message.py` | 出站过滤、CQ 解析与转义、回复拆分、提示词拼装 | 否 |
| `test_onebot_attachment.py` | 文件名清洗、大小限制、来源白名单、附件清理 | 否 |
| `test_onebot_config.py` | `.env` 解析、各配置项解析与默认值 | 否 |
| `test_onebot_state.py` | 消息去重、排队消息结构 | 否 |
| `test_docs_alignment.py` | 配置项/命令表/项目结构在代码与文档间是否一致 | 否 |
| `test_onebot_app_queue.py` | 每用户队列所有权协议、后台任务生命周期 | 是（缺失时自动跳过） |

`test_docs_alignment.py` 会强制以下几件事保持同步，改了一处忘了另一处就会测试失败：

- `onebot_config.py` 里读取的每个配置键，都必须出现在 README 配置表和两个 `.env` 模板里（反之亦然）。
- README 写的默认值必须与代码中的 `DEFAULT_*` 常量一致。
- README 命令表的权限列必须与 `onebot_app.py` 的 `ADMIN_COMMANDS` 完全对应。
- `src/` 与 `tests/` 下的每个模块都必须在 README 里被提到。

## 13. 已知限制与安全须知

上线到公开群聊前请务必了解以下几点。

### 13.1 会话上下文在所有用户之间共享

整个进程只有**一个** GenericAgent 实例，也就只有**一份**对话历史。
网关做的「按用户排队」只保证同一时刻只有一个任务在跑，**并不隔离上下文**：

- 用户 A 让 Agent 读了某个文件，随后用户 B 在群里问「刚才说了啥」，模型可能把 A 的内容说出来。
- 管理员的历史轮次仍留在上下文里，非管理员可以借此诱导模型越权。

这是 GenericAgent 公共前端层（`agentmain.py` / `chatapp_common.py`）的设计，不在本项目内，
本项目无法单独修复。因此：

- **不要**在有陌生人的群里开放本机器人；用 `ONEBOT_ALLOWED_USERS` / `ONEBOT_ALLOWED_GROUPS` 收紧白名单。
- 默认模板里的 `*`（放行全部）**仅适合本地自测**。
- 切换话题时用 `/new` 清空上下文。

### 13.2 权限策略靠提示词，不是硬约束

`ONEBOT_ADMIN_QQ` 的作用是往提示词里写一行 `policy: user is NOT admin ...`，
真正拒绝越权操作的是模型本身。工具层不做鉴权，所以：

- 不要把它当成安全边界，而应视为「降低误操作概率」的措施。
- 群成员的消息内容、昵称、被 @ 的人、历史消息都会进提示词，存在提示词注入风险。
- 真正敏感的机器不要以高权限账号运行本程序。

### 13.3 附件来源

- 附件由 OneBot 实现（NapCat）上报，网关会按 `url` / 本地 `path` / `base64` 顺序取用。
- 下载只允许 `http(s)`，并阻断跨协议重定向；仍建议 NapCat 与本程序同机、监听 `127.0.0.1`。
- 若 OneBot 端不完全可信，用 `ONEBOT_LOCAL_SOURCE_DIRS` 限定本地来源目录，
  避免 `data.path` 指向任意本地文件被复制出来并交给 Agent。

### 13.4 回复内容会被过滤

Agent 的原始输出不是直接发到 QQ 的，`onebot_message.normalize_outgoing_content`
会先做一轮清理。了解这点有助于排查「回复内容少了一段」的疑惑：

会被删掉的内容：

- 进度/状态占位行：`思考中...`、`⏳ 还在处理中，请稍等...`、`LLM Running (Turn N)...`。
- 工具调用模板块：从形如 `工具名(` 的行开始，到出现以 `)`、`})`、`])` 结尾的行为止。
  识别的工具名为 `code_run`、`file_read`、`file_write`、`file_patch`、`shell_command`、
  `apply_patch`，`web_` 系列（`scan`/`search`/`open`/`click`/`fetch`/`find`/`query`/`screenshot`），
  以及 `functions.` / `web.` / `multi_tool_use.` 前缀形式（见 `onebot_message.TOOL_CALL_RE`）。
- Markdown 代码块的 ``` 围栏标记（**围栏里的代码本身会保留**）。
- 连续空行会被压缩成一个。

为避免误伤，未闭合的工具调用块最多只吞掉 40 行（`TOOL_BLOCK_MAX_LINES`）：
正文里恰好有一行形似工具调用时，超出上限即判定为误判并把内容原样还回去。

同时会给 Agent 注入「只用纯文本、不要 Markdown」的提示词
（见 `ONEBOT_PLAIN_TEXT_HINT` 与 `build_agent_prompt` 里的 `output_rules`），
因为 QQ 不渲染 Markdown。

### 13.5 磁盘占用

- 附件默认保留 24 小时（`ONEBOT_ATTACHMENT_TTL_HOURS`），启动后由后台任务定期清理。
- 启动日志默认保留最近 10 个（`ONEBOT_LOG_KEEP`）。
- 两者设为 `0` 会关闭清理，磁盘将无上限增长。
