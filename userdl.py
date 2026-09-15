"""
t.me 消息链接下载模块（Telethon 用户账号）。

Bot API 天生不能"按链接取消息"，要解析 t.me/频道/123 这种链接，
需要一个登录过的用户账号。本模块封装了：

1. lazy 建立与 Telegram 的 MTProto 连接（走和机器人相同的代理）
2. 解析公开频道（t.me/xxx/123）与私有频道（t.me/c/1234567/123）链接
3. 媒体识别、原始文件名提取、进度回调、整组相册抓取

配置方法见 bot.py 里 download_tme 的提示文案，登录用 login_tg.bat。
"""
from __future__ import annotations

import asyncio
import re
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Optional

from config import API_HASH, API_ID, DATA_DIR, PROXY_URL

try:
    from telethon import TelegramClient
    from telethon.tl.types import (
        DocumentAttributeAudio,
        DocumentAttributeFilename,
        DocumentAttributeVideo,
    )

    _HAS_TELETHON = True
except ImportError:  # pragma: no cover - 未装 telethon 时机器人其余功能不受影响
    _HAS_TELETHON = False

SESSION_PATH = DATA_DIR / "tg_user"  # 生成 data/tg_user.session
TME_MSG_RE = re.compile(
    r"https?://(?:www\.)?(?:t\.me|telegram\.me)/(?:(c)/)?([A-Za-z0-9_]+)(?:/(\d+))?",
    re.I,
)

# telethon 文档类型没有文件名时，按 mime 补扩展名
_MIME_EXT = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif",
    "video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov",
    "audio/mpeg": ".mp3", "audio/ogg": ".ogg", "audio/opus": ".opus", "audio/x-m4a": ".m4a",
    "application/pdf": ".pdf", "application/zip": ".zip",
    "application/vnd.android.package-archive": ".apk",
}


class NotLoggedIn(Exception):
    """用户账号还没登录（需要先跑一次 login_tg.bat）。"""


_client: Optional[Any] = None
_lock = asyncio.Lock()


def available() -> bool:
    """Telethon 已安装且 API_ID/API_HASH 已配置。"""
    return _HAS_TELETHON and bool(API_ID) and bool(API_HASH)


def proxy_tuple() -> Optional[tuple]:
    """把 PROXY_URL 转成 Telethon 需要的 (类型, 主机, 端口) 三元组。"""
    if not PROXY_URL:
        return None
    u = urllib.parse.urlparse(PROXY_URL)
    scheme = (u.scheme or "http").lower()
    try:
        from python_socks import ProxyType

        ptype = (
            ProxyType.SOCKS5 if "socks5" in scheme
            else ProxyType.SOCKS4 if "socks4" in scheme
            else ProxyType.HTTP
        )
    except Exception:
        ptype = 2 if "socks5" in scheme else (1 if "socks4" in scheme else 3)
    host = u.hostname or "127.0.0.1"
    port = u.port or (1080 if "socks" in scheme else 8080)
    return (ptype, host, port)


async def get_client() -> Any:
    """lazy 建立并复用用户账号客户端。"""
    global _client
    if not _HAS_TELETHON:
        raise RuntimeError("服务器未安装 telethon，无法解析 t.me 链接")
    if not API_ID or not API_HASH:
        raise RuntimeError("未配置 API_ID / API_HASH，无法解析 t.me 链接")
    if _client is None:
        async with _lock:
            if _client is None:
                _client = TelegramClient(
                    str(SESSION_PATH), API_ID, API_HASH,
                    proxy=proxy_tuple(), device_model="file-bot",
                )
                await _client.connect()
    return _client


def parse_tme(url: str) -> tuple[Any, int]:
    """解析 t.me 链接 → (entity, 消息ID)。支持公开频道与 /c/ 私有频道。"""
    m = TME_MSG_RE.search(url)
    if not m:
        raise ValueError("无法识别这个 Telegram 链接")
    is_private, ref, mid = m.group(1), m.group(2), m.group(3)
    if not mid:
        raise ValueError("链接里没有消息编号。要形如 t.me/频道名/123（消息右键→复制消息链接）")
    if is_private:
        entity = int(f"-100{ref}")
    else:
        entity = ref
    return entity, int(mid)


async def fetch_messages(url: str) -> list:
    """按链接取消息；若属于相册（grouped media），把整组一起取回来。"""
    client = await get_client()
    if not await client.is_user_authorized():
        raise NotLoggedIn()
    entity, msg_id = parse_tme(url)
    msgs = await client.get_messages(entity, ids=msg_id)
    if msgs is None:
        raise ValueError(
            "链接指向的消息不存在，或你的账号看不到它（私有频道/群需要先用这个账号加入）"
        )
    first: Any = msgs[0] if isinstance(msgs, list) else msgs
    if not first.media:
        raise ValueError("这条消息里没有文件（纯文字/投票等），没什么可下载的")
    out = [first]
    if first.grouped_id:
        around = await client.get_messages(
            entity, ids=list(range(max(1, msg_id - 19), msg_id + 20))
        )
        group = [m for m in around if m and m.grouped_id == first.grouped_id and m.media]
        if len(group) > 1:
            out = sorted(group, key=lambda m: m.id)
    return out


def media_name(msg: Any) -> str:
    """从 Telethon 消息里提取原始文件名（没有就生成一个）。"""
    if msg.document is not None:
        for attr in msg.document.attributes or []:
            fn = getattr(attr, "file_name", None)
            if fn:
                return fn
        doc_id = msg.document.id
        mime = (msg.document.mime_type or "").lower()
        ext = _MIME_EXT.get(mime, "")
        if msg.voice:
            return f"voice_{doc_id}.ogg"
        if msg.video_note:
            return f"video_note_{doc_id}.mp4"
        if msg.gif:
            return f"animation_{doc_id}.mp4"
        if msg.sticker:
            return f"sticker_{doc_id}.webp"
        return f"file_{doc_id}{ext}"
    if msg.photo is not None:
        return f"photo_{msg.id}.jpg"
    return f"file_{msg.id}"


def media_size(msg: Any) -> int:
    """消息里媒体的字节数（拿不准就 0）。"""
    if msg.document is not None:
        return msg.document.size or 0
    if msg.photo is not None:
        sizes = [getattr(s, "size", 0) or 0 for s in msg.photo.sizes or []]
        return max(sizes) if sizes else 0
    return 0


async def download(msg: Any, dest: Path, progress: Optional[Callable[[int, int], None]] = None) -> int:
    """下载一条消息的媒体到指定路径，返回写入字节数。progress(done, total) 会被同步回调。"""
    client = await get_client()
    path = await client.download_media(msg, file=str(dest), progress_callback=progress)
    p = Path(path) if path else dest
    return p.stat().st_size if p.exists() else 0
