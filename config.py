"""
配置文件 —— 所有可调参数集中在这里，也可以直接写在 .env 里覆盖。
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _env(key: str, default: str = "") -> str:
    return (os.getenv(key) or default).strip()


def _env_int_set(key: str) -> set[int]:
    raw = _env(key)
    if not raw:
        return set()
    return {int(x) for x in raw.replace(";", ",").split(",") if x.strip().lstrip("-").isdigit()}


def _env_bool(key: str, default: bool = False) -> bool:
    raw = _env(key)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on", "y"}


# ---------------------------------------------------------------- 基础配置
# 机器人 Token（在 @BotFather 处申请）
BOT_TOKEN: str = _env("BOT_TOKEN")

# Telegram API 根地址。默认官方接口（单个文件最大 20MB 下载）。
# 若你自建了本地 Bot API server（解除 2GB/4GB 限制），改成例如 http://127.0.0.1:8081
API_BASE_URL: str = _env("API_BASE_URL", "https://api.telegram.org").rstrip("/")


def _detect_proxy() -> str:
    """代理探测顺序：.env 显式配置 → Windows 系统代理（注册表）→ 环境变量。"""
    explicit = _env("PROXY_URL")
    if explicit:
        return explicit.rstrip("/")

    def from_registry() -> str:
        if os.name != "nt":
            return ""
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            )
            try:
                enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
                if not enable:
                    return ""
                server, _ = winreg.QueryValueEx(key, "ProxyServer")
            finally:
                winreg.CloseKey(key)
            if server:
                if "=" in server:  # 形如 http=127.0.0.1:7890;https=127.0.0.1:7890
                    parts = [p.split("=", 1)[1] for p in server.split(";") if p.strip()]
                    server = next((p for p in parts if p), "")
                if server and not server.startswith("http"):
                    server = "http://" + server
                return server
        except Exception:
            pass
        return ""

    registry = from_registry()
    if registry:
        return registry

    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        val = os.getenv(var)
        if val:
            return val.strip().rstrip("/")

    return ""


# 访问 Telegram 用的代理（国内网络必需）。留空 = 自动探测
PROXY_URL: str = _detect_proxy()

# 文件保存目录
DOWNLOAD_DIR: Path = Path(_env("DOWNLOAD_DIR") or (BASE_DIR / "downloads")).expanduser()
# 索引/元数据目录
DATA_DIR: Path = Path(_env("DATA_DIR") or (BASE_DIR / "data")).expanduser()

# 白名单：留空 = 所有人可用；填了则只有这些用户能用（用 /id 查询自己的 ID）
ALLOWED_USER_IDS: set[int] = _env_int_set("ALLOWED_USER_IDS")
# 管理员：可用 /stats、/del 等命令
ADMIN_IDS: set[int] = _env_int_set("ADMIN_IDS")

# 所有者：授权体系的最高权限。留空 = 重启后第一个 /start 的人自动成为所有者
_owner_raw = _env("OWNER_ID")
OWNER_ID: int | None = int(_owner_raw) if _owner_raw.isdigit() else None

# 只在这些会话里工作，留空 = 所有会话（私聊+群组）
ALLOWED_CHAT_IDS: set[int] = _env_int_set("ALLOWED_CHAT_IDS")

# 单个直链下载的体积上限（MB），防止被塞满磁盘
MAX_URL_SIZE_MB: int = int(_env("MAX_URL_SIZE_MB", "2048"))

# 进度条刷新间隔（秒），太短会被 Telegram 限流
PROGRESS_INTERVAL: float = float(_env("PROGRESS_INTERVAL", "3"))

# 相册（多图/多文件）合并等待时间（秒）
ALBUM_WAIT: float = float(_env("ALBUM_WAIT", "1.6"))

# 保存成功后自动删除用户发来的原消息（群组里比较有用）
DELETE_AFTER_SAVE: bool = _env_bool("DELETE_AFTER_SAVE", False)

# 单个用户磁盘配额（MB），0 = 不限制
USER_QUOTA_MB: int = int(_env("USER_QUOTA_MB", "0"))

# 日志级别
LOG_LEVEL: str = _env("LOG_LEVEL", "INFO").upper()

# Telethon 用户账号（解析 t.me/频道/123 消息链接用）。
# 到 https://my.telegram.org 免费申请，填 API ID 和 API Hash。留空 = 不启用该功能
API_ID: int = int(_env("API_ID") or "0")
API_HASH: str = _env("API_HASH")

DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
