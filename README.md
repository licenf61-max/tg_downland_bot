# Telegram 文件下载机器人

把 Telegram 上的文件自动下载到本地磁盘，并支持直链下载、搜索和管理。

## 功能

| 功能 | 说明 |
| --- | --- |
| 自动下载 | 文档 / 视频 / 音频 / 语音 / 动图 / 图片 / 贴纸，发过来就存盘 |
| 相册合并 | 一次发多张图，合并成一条进度消息，不会刷屏 |
| 直链下载 | 直接发 http(s) 链接，机器人帮你把文件拉到服务器 |
| 进度显示 | 实时进度条（含百分比、已下载 / 总大小） |
| 文件管理 | `/list` `/search` `/get` `/del` `/mine` |
| 统计 | `/stats` 文件数、占用空间、上传排行 |
| 权限 | 白名单、管理员、单用户配额、直链体积上限 |
| 大文件 | 可对接自建本地 Bot API server，突破 20MB 限制 |

## 目录结构

```
telegram-file-bot/
├── bot.py            # 主程序
├── config.py         # 配置读取
├── requirements.txt
├── .env              # 你的真实配置（含 Token，已被 .gitignore 忽略）
├── .env.example      # 配置模板
├── run.bat           # Windows 一键启动
├── downloads/        # 下载的文件（按用户 ID 分目录）
└── data/index.json   # 文件索引
```

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 填配置
cp .env.example .env      # 然后把 BOT_TOKEN 填进去

# 3. 启动
python bot.py
```

Windows 上直接双击 `run.bat` 也行。

启动成功后，去 Telegram 找到你的机器人，发 `/start`，然后随便丢个文件过去试试。

## 命令

| 命令 | 作用 |
| --- | --- |
| `/start` | 开始 |
| `/help` | 帮助 |
| `/list [数量]` | 最近的文件（默认 20，最多 100） |
| `/mine` | 我上传的文件 |
| `/search 关键字` | 按文件名或上传者搜索 |
| `/get 文件ID` | 把文件重新发回聊天 |
| `/del 文件ID` | 删除文件（有二次确认） |
| `/stats` | 统计信息（管理员） |
| `/id` | 查看自己的用户 ID |
| `/dir` | 查看服务器保存路径 |

## 配置项（.env）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `BOT_TOKEN` | 必填 | BotFather 给的 Token |
| `PROXY_URL` | 自动探测 | 访问 Telegram 的代理，如 `http://127.0.0.1:7892`。留空按「Windows 系统代理 → 环境变量」自动探测 |
| `ALLOWED_USER_IDS` | 空 | 白名单，空 = 所有人可用，逗号分隔 |
| `ADMIN_IDS` | 空 | 管理员，可用 `/stats`、删任何人的文件 |
| `ALLOWED_CHAT_IDS` | 空 | 只在这些会话工作 |
| `API_BASE_URL` | `https://api.telegram.org` | 自建本地 Bot API server 地址 |
| `MAX_URL_SIZE_MB` | 2048 | 直链下载体积上限，0 = 不限 |
| `USER_QUOTA_MB` | 0 | 单用户磁盘配额，0 = 不限 |
| `DELETE_AFTER_SAVE` | false | 下载后删掉用户的原消息 |
| `PROGRESS_INTERVAL` | 3 | 进度条刷新间隔（秒），太短会被限流 |
| `LOG_LEVEL` | INFO | 日志级别 |

> 建议一开始先填 `ALLOWED_USER_IDS`，不然任何人搜到你的机器人都能往你硬盘里塞东西。

## 授权机制（默认谁都不能用）

机器人是**默认拒绝**的：所有者没有授权过的人，发什么都会被拒并记录申请。

- **所有者**：重启后第一个发 `/start` 的人自动成为所有者（也可以在 `.env` 里写死 `OWNER_ID`）
- 陌生人给机器人发消息 → 收到"未授权"提示，申请自动记录，**所有者会收到私信通知**
- 所有者审批：

| 命令 | 作用 |
| --- | --- |
| `/requests` | 查看待审批申请 |
| `/auth 用户ID` | 授权某人（对方会收到通知） |
| `/deny 用户ID` | 拒绝申请 |
| `/unauth 用户ID` | 取消已发的授权 |
| `/authlist` | 已授权列表 |

授权数据存在 `data/authorized.json`，重启不丢。

> ⚠️ 部署后请**立刻自己第一个 `/start`**，否则别人抢先一步就成为所有者了。
> 保险起见可以在 `.env` 里写死 `OWNER_ID=你的ID`。

## 网络与代理（国内必读）

国内网络直连 `api.telegram.org` 是不通的，机器人**必须走代理**，否则启动报 `TimedOut`、
下载报 `ProxyError / NetworkError`。

- 机器人启动时自动按 `.env` 的 `PROXY_URL` → Windows 系统代理 → 环境变量 的顺序找代理，
  并在日志里打印 `使用代理：http://...`。没找到会打警告 `未检测到代理`。
- 代理软件（Clash / v2rayN 等）换了监听端口，要同步改 `.env`。
- 连接失败会自动重试（5 秒一次），不用手动重启；网络恢复后机器人自己会上线。
- 运行日志写在 `data/bot.log`，出问题先看这里。

## 关于 20MB 限制

官方 Bot API 只允许机器人下载 20MB 以内的文件。想下大文件（最大 2GB / 4GB）需要自建本地 Bot API server：

```bash
# 1. 编译 telegram-bot-api（见 https://github.com/tdlib/telegram-bot-api）
# 2. 用你自己的 api_id / api_hash 启动
telegram-bot-api --api-id=xxx --api-hash=yyy --local

# 3. .env 里改地址
API_BASE_URL=http://127.0.0.1:8081
```

注意：本地 server 需要配合 `--local` 参数，且必须和机器人跑在同一台机器（或内网可访问）。

## 常见问题

**Q：下载的文件打不开？**

先确认你发的是什么：

| 你发的 | 机器人会做什么 |
| --- | --- |
| 直接发文件（拖进聊天框） | ✅ 正常下载，能打开 |
| 文件的**真实下载直链**（如 `https://xxx.com/a/b.mp4`） | ✅ 正常下载，能打开 |
| **t.me 频道消息链接**（如 `https://t.me/频道名/333`） | ⚠️ 这不是文件，是个网页。机器人现在会直接提示你，不会下载 |
| 网盘分享页 / 需要登录的页面 | ⚠️ 返回的是 HTML 网页，机器人会拒绝并提示 |

想拿频道里的文件，正确做法是**把那条消息转发给机器人**，它会自动下载转发内容。

> 为什么机器人不能直接解析 t.me 链接？因为 Bot API 没有"按链接取消息"的接口，
> 只有**用户账号**（MTProto，如 Telethon）才有这个能力，且需要 api_id/api_hash 授权登录。

**Q：文件没有扩展名？**

机器人会根据服务器返回的 Content-Type 自动补扩展名；如果对方服务器什么都不给，
文件可能确实没有类型信息。

## 安全提醒

- `.env` 里存着 Token，不要提交到 Git，不要发群里。**如果 Token 泄露过，去 @BotFather 用 `/revoke` 换一个新的。**
- 群组里建议配 `ALLOWED_USER_IDS`，否则等于开了个公共网盘。
- 磁盘写满会导致机器人崩溃，生产环境建议配 `USER_QUOTA_MB`。
