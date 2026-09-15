# 部署教程：从零开始，在一台新电脑上跑起来

写给要在**别人电脑**（可能什么环境都没有）上部署的人。全程跟着做，大约 10 分钟。

---

## 开始前：搞清楚这两个问题

**1. 这台电脑连 Telegram 通吗？**
国内网络必须走代理。部署前先确认这台电脑上有能用的代理软件（Clash / v2rayN 等），
并且记下它的本地端口（比如 `7890`、`7892`），第 6 步要用。

**2. 用哪个 Token？**
⚠️ **同一个 Token 只能有一个实例在跑**。如果你自己电脑上的机器人还开着，
又拿同一个 Token 在别人电脑上启动，两边会互相报 Conflict、消息时好时坏。

- 给别人部署一个**独立的机器人** → 让对方找 @BotFather 用 `/newbot` 申请新 Token（推荐）
- 只是把服务**挪到另一台电脑** → 先关掉旧电脑上的实例，再在新电脑启动

---

## 第 1 步：安装 Python（约 2 分钟）

1. 打开 https://www.python.org/downloads/ 下载 Python 3.11 或更新版本
   （国内下载慢可以用华为镜像：https://mirrors.huaweicloud.com/python/ ）
2. 运行安装包，**第一个界面务必勾选 `Add python.exe to PATH`**，再点 Install Now
3. 验证：按 `Win + R` 输入 `cmd` 回车，在黑窗口里输入 `python --version`，
   显示 `Python 3.x.x` 就成功了

> 忘了勾 Add to PATH？卸载重装一次，勾上就行。
>
> ⚠️ **Windows 自带的「Python 占位程序」陷阱**：很多电脑（尤其是从来没装过 Python 的）
> 在 `C:\Users\你\AppData\Local\Microsoft\WindowsApps\` 下已经有一个
> **0 字节的 `python.exe`**。它不是真的 Python，输入 `python` 会弹出微软应用商店。
> 这种电脑上 `python --version` 会提示「Python 未找到」，请老老实实去 python.org 装一个。
> （`run.bat` 已会自动跳过这个占位程序，不会误用。）

## 第 2 步：拿到代码（不用装 Git）

1. 浏览器打开 https://github.com/licenf61-max/tg_downland_bot
2. 点绿色的 **`< > Code`** 按钮 → **Download ZIP**
3. 解压到一个**路径里没有中文和空格**的目录，比如 `D:\tg_bot\`

> 或者用 Git：`git clone https://github.com/licenf61-max/tg_downland_bot.git`

## 第 3 步：双击 run.bat（自动装环境）

双击解压目录里的 `run.bat`。第一次运行它会自动：

1. 找到刚装的 Python
2. 在目录里创建独立虚拟环境 `.venv`（不污染系统）
3. 从清华镜像下载依赖（国内不挂代理也能装）

看到它停在窗口里不报错，就说明装好了。**先按 `Ctrl + C`（或直接关窗口）停掉**，去做第 4 步。

> **run.bat 报错对照表**
>
> | 窗口里看到 | 原因 | 怎么办 |
> | --- | --- | --- |
> | `[ERROR] No usable Python 3 found` | 没有真 Python，或只装了商店占位程序 | 回第 1 步，去 python.org 装，勾 Add to PATH |
> | `[ERROR] Could not create .venv` | Python 找到了但装依赖失败 | 关掉杀毒软件重试，或手动在目录里执行 `python -m venv .venv` |
> | `[ERROR] pip install failed` | 网络/代理拦住了 pip | 检查能不能上网；关掉代理软件再试一次 |
> | 窗口一闪就没 | 没有走到 `pause` | 在项目目录地址栏输入 `cmd` 回车，手动敲 `run.bat`，这样报错不会消失 |
>
> `run.bat` 找不到 `.env` 时会自动从 `.env.example` 复制一份，并打开记事本让你填 Token。

## 第 4 步：配置 .env

1. 进入项目目录，把 `.env.example` 复制一份，重命名为 `.env`
   （注意：不是改名成 `.env.txt`。文件管理器里先开「查看 → 文件扩展名」）
2. 用记事本打开 `.env`，至少改这三行：

```ini
BOT_TOKEN=1234567890:AAxxxxxxxxxxxxxxxxxxxxx   ← BotFather 给的 Token

# 这台电脑的代理端口（在代理软件里能看到，常见 7890/7892/10809）
PROXY_URL=http://127.0.0.1:7892

# 【强烈建议】把所有者的 Telegram ID 写死，防止别人抢先认领
OWNER_ID=你的Telegram数字ID
```

> 不知道自己的 ID？先随便填一个能跑通的版本，给机器人发 `/id` 就能看到，
> 然后填进来重启一次。留空 `OWNER_ID` 的话，**重启后第一个发 /start 的人自动成为所有者**，
> 所以部署完要马上自己去发。

## 第 5 步：启动

再双击 `run.bat`。窗口里出现：

```
已登录：@你的机器人名 (id)
Application started
```

就是跑起来了。**窗口不能关**，关了机器人就下线。

## 第 6 步：认领所有权

用自己的 Telegram 给机器人发 `/start` —— 因为你填了 `OWNER_ID`（或者你是第一个 /start 的），
你会收到「👑 你已成为本机器人的所有者」。

然后测试：随便转发一条带文件的消息给机器人，应该看到进度条并落盘成功。

---

## 常见问题

| 现象 | 原因 / 解决 |
| --- | --- |
| 启动一直重试 `TimedOut` / `502` | 代理没配对。确认代理软件开着，`.env` 的 `PROXY_URL` 端口和它一致 |
| 日志提示 `未检测到代理` | 这台电脑没开代理软件，或没开系统代理。国内不挂代理连不上 Telegram |
| `Conflict: terminated by other getUpdates` | 同一个 Token 在别处还开着一个实例，把那边关掉 |
| 发 t.me 链接说下载失败 | 正常。t.me 是网页不是文件，正确做法是把那条消息**转发**给机器人 |
| 下载的文件打不开 | 发的不是文件直链。机器人会拒绝 HTML 网页并提示 |
| 想给别人开权限 | 对方给机器人发消息后，你收到申请通知，回复 `/auth 对方ID` 即可 |
| pip 装依赖很慢 | run.bat 已默认走清华镜像；仍慢就检查这台电脑的网 |

## 可选：开机自启

1. `Win + R` 输入 `shell:startup` 回车，打开启动文件夹
2. 右键 `run.bat` → 发送到 → 桌面快捷方式，把快捷方式剪切进启动文件夹
3. 重启电脑验证机器人自动上线

## 安全事项

- `.env` 里有 Token，**不要发给别人、不要传网盘**。泄露了就去 @BotFather `/revoke` 换新
- 部署在别人电脑上，对方能看到服务器上下载的所有文件，心里要有数
- 机器人是授权制的，没被 `/auth` 的人用不了；定期 `/authlist` 看看都授权了谁
