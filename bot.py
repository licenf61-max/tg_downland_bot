"""
Telegram 文件下载机器人
=======================

功能一览
--------
* 把用户发来的 文档 / 视频 / 音频 / 语音 / 动图 / 图片 / 贴纸 自动下载到服务器本地磁盘
* 相册（一次发多张）合并处理，只发一条进度消息
* 直接发 http(s) 直链，机器人帮你把文件拉到服务器
* /list /search /stats /get /del 管理已下载的文件
* 白名单、管理员、单用户配额、磁盘保护
* 支持自建本地 Bot API server（突破 20MB 限制）

运行： python bot.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import httpx
import userdl
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest, Forbidden, NetworkError, TelegramError, TimedOut
from telegram.request import HTTPXRequest
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import (
    ADMIN_IDS,
    ALBUM_WAIT,
    ALLOWED_CHAT_IDS,
    ALLOWED_USER_IDS,
    API_BASE_URL,
    BOT_TOKEN,
    DATA_DIR,
    DELETE_AFTER_SAVE,
    DOWNLOAD_DIR,
    LOG_LEVEL,
    MAX_URL_SIZE_MB,
    PROGRESS_INTERVAL,
    PROXY_URL,
    OWNER_ID,
    USER_QUOTA_MB,
)

# ===================================================================== 基础设施

logging.basicConfig(
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(DATA_DIR / "bot.log", encoding="utf-8"),
    ],
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram.ext.ExtBot").setLevel(logging.WARNING)
log = logging.getLogger("file-bot")

INDEX_PATH = DATA_DIR / "index.json"
CHUNK = 256 * 1024
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
TME_RE = re.compile(r"https?://(?:www\.)?(?:t\.me|telegram\.me)/\S+", re.I)

# 根据 Content-Type 补全缺失的扩展名
CT_EXT = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp",
    "video/mp4": ".mp4", "video/webm": ".webm", "video/x-matroska": ".mkv", "video/quicktime": ".mov",
    "audio/mpeg": ".mp3", "audio/ogg": ".ogg", "audio/wav": ".wav", "audio/x-m4a": ".m4a",
    "application/pdf": ".pdf", "application/zip": ".zip", "application/x-7z-compressed": ".7z",
    "application/x-rar-compressed": ".rar", "text/plain": ".txt",
}

# 内存中的文件索引：list[dict]
FILES: list[dict[str, Any]] = []
_index_lock = asyncio.Lock()


def load_index() -> None:
    global FILES
    if INDEX_PATH.exists():
        try:
            FILES = json.loads(INDEX_PATH.read_text("utf-8"))
        except Exception as exc:  # noqa: BLE001
            log.warning("索引文件损坏，已重建：%s", exc)
            FILES = []


async def save_index() -> None:
    async with _index_lock:
        tmp = INDEX_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(FILES, ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(INDEX_PATH)


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024 or unit == "TB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.2f} {unit}"
        num /= 1024
    return f"{num:.2f} TB"


def progress_bar(done: int, total: int, width: int = 18) -> str:
    if total <= 0:
        return f"⬇️ 已接收 {human_size(done)}"
    pct = min(done / total, 1.0)
    filled = int(width * pct)
    return f"`{'█' * filled}{'░' * (width - filled)}` {pct * 100:5.1f}%  {human_size(done)} / {human_size(total)}"


def sanitize(name: str, fallback: str = "file") -> str:
    name = os.path.basename(name or "").strip()
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name)
    name = name.strip(". ")
    if not name:
        return fallback
    stem, dot, ext = name.rpartition(".")
    if dot and len(stem) > 100:
        return stem[:100] + "." + ext[:12]
    return name[:120] or fallback


# 文件名用发送者给的文字标签，方便索引。标签上限（不含扩展名）
LABEL_MAX = 80
_LABEL_TRIM = " \t\r\n:：-—–_,，。、;；|·"


def clean_label(text: Optional[str]) -> str:
    """把发送者提供的文字整理成标签：去掉换行、压掉多余空白和首尾分隔符。"""
    if not text:
        return ""
    return " ".join(text.split()).strip(_LABEL_TRIM)


def label_to_name(label: Optional[str], original: str, index: Optional[int] = None) -> str:
    """用文字标签当文件名，保留原始扩展名；标签为空则退回原文件名。

    标签本身已带同名扩展名时不重复追加（避免「报告.pdf.pdf」）。
    index 用于相册：多个文件共用一个标签时编号成「标签_1」「标签_2」。

    注意：这里不能用 sanitize()，因为它会先取 basename，
    把标签里「/」之前的内容整个丢掉（「2024/09 报告」会变成「09 报告」）。
    """
    label = clean_label(label)
    if not label:
        return original
    ext = Path(original).suffix
    if index is not None:
        label = f"{label}_{index}"
    label = label[:LABEL_MAX]
    # 逐个替换非法字符（不是截断），再去掉首尾的点和空格
    label = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", label).strip(". ")
    label = " ".join(label.split())
    if not label:
        return original
    if ext and label.lower().endswith(ext.lower()):
        return label[:120]
    return f"{label}{ext}"[:120]


def unique_path(folder: Path, filename: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / filename
    if not path.exists():
        return path
    stem, dot, ext = filename.rpartition(".")
    base, suffix = (stem, "." + ext) if dot else (filename, "")
    for i in range(1, 10000):
        path = folder / f"{base}_{i}{suffix}"
        if not path.exists():
            return path
    return folder / f"{base}_{int(time.time())}{suffix}"


def new_file_id() -> str:
    return f"{int(time.time() * 1000):x}{random.randint(0x100, 0xFFF):03x}"


# ===================================================================== 授权体系
# 默认拒绝所有人，由所有者（管理员）用 /auth 授权后才能使用。

AUTH_PATH = DATA_DIR / "authorized.json"
_auth: dict[str, Any] = {"owner": None, "authorized": {}, "pending": {}}
_auth_lock = asyncio.Lock()


def load_auth() -> None:
    global _auth
    data: dict[str, Any] = {"owner": None, "authorized": {}, "pending": {}}
    if AUTH_PATH.exists():
        try:
            data = json.loads(AUTH_PATH.read_text("utf-8"))
        except Exception as exc:  # noqa: BLE001
            log.warning("授权文件损坏，已重建：%s", exc)
    data.setdefault("authorized", {})
    data.setdefault("pending", {})
    data.setdefault("owner", OWNER_ID)
    _auth = data


async def save_auth() -> None:
    async with _auth_lock:
        tmp = AUTH_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(_auth, ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(AUTH_PATH)


def is_authorized(user_id: int) -> bool:
    return (
        _auth.get("owner") == user_id
        or user_id in ADMIN_IDS
        or user_id in ALLOWED_USER_IDS
        or str(user_id) in _auth["authorized"]
    )


def is_admin(user_id: int) -> bool:
    return _auth.get("owner") == user_id or user_id in ADMIN_IDS


def allowed(user_id: int, chat_id: int) -> bool:
    if ALLOWED_CHAT_IDS and chat_id not in ALLOWED_CHAT_IDS:
        return False
    # 初始化前（没有所有者、没有任何白名单）放行，等第一个 /start 认领所有者
    if _auth.get("owner") is None and not _auth["authorized"] and not ALLOWED_USER_IDS and not ADMIN_IDS:
        return True
    return is_authorized(user_id)


async def request_access(update: Update, context: ContextTypes.DEFAULT_TYPE, user) -> None:
    """未授权用户：记录申请；私聊里提示，并尝试私信通知所有者。"""
    uid_str = str(user.id)
    if uid_str not in _auth["pending"]:
        _auth["pending"][uid_str] = {
            "username": user.username or user.full_name,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        await save_auth()
        owner = _auth.get("owner")
        if owner:
            try:
                await context.bot.send_message(
                    owner,
                    "🔔 收到新的授权申请\n"
                    f"用户：{user.full_name}" + (f"  @{user.username}" if user.username else "") + "\n"
                    f"ID：`{user.id}`\n\n"
                    f"批准：/auth {user.id}\n"
                    f"拒绝：/deny {user.id}",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except TelegramError:
                pass
    if update.effective_chat.type == "private":
        await update.effective_message.reply_text(
            "⛔️ 你还没有被授权使用这个机器人。\n\n"
            f"你的用户 ID：`{user.id}`\n"
            "申请已记录，等管理员批准后就能用了。",
            parse_mode=ParseMode.MARKDOWN,
        )


def user_usage(user_id: int) -> int:
    return sum(f["size"] for f in FILES if f["user_id"] == user_id)


async def safe_edit(
    msg: Optional[Message],
    text: str,
    markup: Optional[InlineKeyboardMarkup] = None,
    parse_mode: Optional[str] = ParseMode.MARKDOWN,
) -> None:
    if msg is None:
        return
    try:
        await msg.edit_text(text, parse_mode=parse_mode, reply_markup=markup)
    except BadRequest as exc:
        if "not modified" not in str(exc).lower():
            log.debug("编辑消息失败: %s", exc)
    except TelegramError as exc:
        log.debug("编辑消息失败: %s", exc)


async def record(entry: dict[str, Any]) -> None:
    FILES.append(entry)
    await save_index()


# ===================================================================== 下载核心


async def stream_to_file(
    url: str,
    dest: Path,
    status: Optional[Message],
    label: str,
    expected_size: int = 0,
    size_limit: int = 0,
) -> int:
    """流式下载 url 到 dest，边下边刷新进度条。返回字节数。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    done = 0
    last_push = 0.0
    timeout = httpx.Timeout(connect=30.0, read=None, write=None, pool=None)

    proxy_kw = {"proxy": PROXY_URL} if PROXY_URL else {}

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, **proxy_kw) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("Content-Length") or 0) or expected_size
            if size_limit and total and total > size_limit:
                raise ValueError(f"文件超过上限 {human_size(size_limit)}")
            try:
                with open(tmp, "wb") as fh:
                    async for chunk in resp.aiter_bytes(CHUNK):
                        fh.write(chunk)
                        done += len(chunk)
                        if size_limit and done > size_limit:
                            raise ValueError(f"文件超过上限 {human_size(size_limit)}")
                        now = time.monotonic()
                        if status and now - last_push >= PROGRESS_INTERVAL:
                            last_push = now
                            await safe_edit(status, f"{label}\n{progress_bar(done, total)}")
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
    tmp.replace(dest)
    return done


def extract_media(msg: Message) -> tuple[Any, str]:
    """从消息里取出可下载的文件对象和默认文件名。"""
    if msg.document:
        doc = msg.document
        return doc, sanitize(doc.file_name or f"document_{doc.file_unique_id}")
    if msg.video:
        return msg.video, sanitize(msg.video.file_name or f"video_{msg.video.file_unique_id}.mp4")
    if msg.audio:
        return msg.audio, sanitize(msg.audio.file_name or f"audio_{msg.audio.file_unique_id}.mp3")
    if msg.voice:
        return msg.voice, f"voice_{msg.voice.file_unique_id}.ogg"
    if msg.animation:
        return msg.animation, sanitize(msg.animation.file_name or f"animation_{msg.animation.file_unique_id}.mp4")
    if msg.video_note:
        return msg.video_note, f"video_note_{msg.video_note.file_unique_id}.mp4"
    if msg.photo:
        photo = msg.photo[-1]
        return photo, f"photo_{photo.file_unique_id}.jpg"
    if msg.sticker:
        st = msg.sticker
        ext = ".tgs" if st.is_animated else (".webm" if st.is_video else ".webp")
        return st, f"sticker_{st.file_unique_id}{ext}"
    raise ValueError("这条消息里没有可下载的文件")


def replied_text(msg: Message) -> str:
    """若这条消息是「回复某条带文字的消息」发的，借那条文字当标签。

    支持两种习惯：先写标签再回复它发文件，或回复某个文件补一句说明。
    """
    replied = getattr(msg, "reply_to_message", None)
    if replied is None:
        return ""
    if replied.text:
        return replied.text
    return replied.caption or ""


async def save_one(
    msg: Message,
    context: ContextTypes.DEFAULT_TYPE,
    label: Optional[str] = None,
    index: Optional[int] = None,
) -> tuple[dict[str, Any], str]:
    """下载一条消息里的文件，返回 (索引记录, 结果文案)。

    文件名优先用发送者给的文字标签，取用顺序：
    显式传入的 label（相册共用标签）→ 消息自带的 caption → 回复的那条文字 → 原始文件名。
    """
    user = msg.from_user
    file_obj, filename = extract_media(msg)
    size = getattr(file_obj, "file_size", 0) or 0

    if USER_QUOTA_MB:
        used = user_usage(user.id)
        if used + size > USER_QUOTA_MB * 1024 * 1024:
            raise ValueError(
                f"你的磁盘配额已用完（{human_size(used)} / {USER_QUOTA_MB} MB），"
                f"请先用 /del 删除一些文件。"
            )

    if label is None:
        label = msg.caption or replied_text(msg)
    folder = DOWNLOAD_DIR / str(user.id)
    filename = label_to_name(label, filename, index)
    dest = unique_path(folder, filename)

    try:
        tg_file = await context.bot.get_file(file_obj.file_id)
    except TelegramError as exc:
        if "too big" in str(exc).lower():
            raise ValueError(
                "这个文件超过了官方 Bot API 的 20MB 下载上限。\n"
                "解决办法：自建本地 Bot API server，并把 .env 里的 API_BASE_URL 指向它。"
            ) from exc
        raise

    # file_path 可能是相对路径（documents/file_1.pdf）也可能是完整 URL，两种都要兼容
    fp = tg_file.file_path or ""
    if fp.startswith(("http://", "https://")):
        url = fp
    else:
        url = f"{API_BASE_URL}/file/bot{BOT_TOKEN}/{fp.lstrip('/')}"
    written = await stream_to_file(url, dest, None, "", expected_size=size)
    if not size:
        size = written

    entry = {
        "id": new_file_id(),
        "name": dest.name,
        "label": clean_label(label),
        "size": size,
        "user_id": user.id,
        "username": user.username or user.full_name,
        "chat_id": msg.chat_id,
        "tg_file_id": file_obj.file_id,
        "path": str(dest.relative_to(DOWNLOAD_DIR)),
        "source": "telegram",
        "url": f"https://t.me/c/{str(msg.chat_id).replace('-100', '')}/{msg.message_id}",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    await record(entry)
    return entry, f"`{dest.name}`  ·  {human_size(size)}  ·  ID `{entry['id']}`"


async def process_single(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    try:
        entry, line = await save_one(msg, context)
    except ValueError as exc:
        await msg.reply_text(f"⚠️ {exc}")
        return
    except Exception as exc:  # noqa: BLE001
        log.exception("下载失败")
        await msg.reply_text(f"❌ 下载失败：{exc}")
        return

    await msg.reply_text(
        f"✅ 已保存\n{line}\n📁 `{DOWNLOAD_DIR / entry['path']}`",
        parse_mode=ParseMode.MARKDOWN,
    )
    if DELETE_AFTER_SAVE:
        try:
            await msg.delete()
        except TelegramError:
            pass


# ------------------------------------------------------------------ 相册合并

_albums: dict[str, dict[str, Any]] = {}


async def handle_album(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    key = f"{msg.chat_id}:{msg.media_group_id}"
    bucket = _albums.get(key)
    if bucket is None:
        bucket = {"messages": [], "task": None}
        _albums[key] = bucket
    bucket["messages"].append(msg)
    if bucket["task"] is None:
        bucket["task"] = context.application.create_task(_flush_album(context, key))


async def _flush_album(context: ContextTypes.DEFAULT_TYPE, key: str) -> None:
    await asyncio.sleep(ALBUM_WAIT)
    bucket = _albums.pop(key, None)
    if not bucket or not bucket["messages"]:
        return
    messages: list[Message] = bucket["messages"]
    messages.sort(key=lambda m: m.message_id)
    # 相册的文字说明往往只挂在其中一条消息上，抽出来给整组共用
    group_label = next((t for t in (clean_label(m.caption) for m in messages) if t), "")
    numbering = len(messages) > 1

    status = await messages[0].reply_text(f"⏳ 收到 {len(messages)} 个文件，开始下载…")
    ok, total_bytes, failed = 0, 0, 0
    for idx, msg in enumerate(messages, 1):
        await safe_edit(status, f"⏳ 正在下载第 {idx}/{len(messages)} 个…\n{progress_bar(0, 0)}")
        label = clean_label(msg.caption) or group_label
        try:
            entry, _ = await save_one(
                msg, context, label=label, index=idx if (numbering and label) else None
            )
            ok += 1
            total_bytes += entry["size"]
        except Exception as exc:  # noqa: BLE001
            failed += 1
            log.warning("相册第 %s 个下载失败：%s", idx, exc)
    if group_label:
        text = f"✅ 相册下载完成：{ok}/{len(messages)} 个，共 {human_size(total_bytes)}\n🏷 标签 `{group_label}`"
    else:
        text = f"✅ 相册下载完成：{ok}/{len(messages)} 个，共 {human_size(total_bytes)}"
    if failed:
        text += f"\n⚠️ {failed} 个失败"
    await safe_edit(status, text)


# ------------------------------------------------------------------ 直链下载


def guess_name(url: str, resp: httpx.Response) -> str:
    cd = resp.headers.get("content-disposition", "")
    match = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)\"?", cd, re.I)
    if match:
        return urllib.parse.unquote(match.group(1))
    name = os.path.basename(urllib.parse.urlparse(str(resp.url)).path)
    return name or "download.bin"


TME_SETUP_TEXT = (
    "ℹ️ 解析 *t.me/频道/123* 这种链接需要一个登录过的用户账号（Bot API 做不到）。\n\n"
    "*一次性配置：*\n"
    "1️⃣ 到 my.telegram.org 申请 *API ID* 和 *API Hash*（免费）\n"
    "2️⃣ 填到 .env 的 `API_ID` / `API_HASH`\n"
    "3️⃣ 在项目目录双击 `login_tg.bat`，按提示登录你的 Telegram 账号（只需一次）\n\n"
    "配置好之前，也可以把频道消息 *长按转发* 给我，一样能下载。"
)


async def download_tme(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    url: str,
    tag: str,
) -> None:
    """解析 t.me/频道/123 消息链接并下载其中的媒体（Telethon 用户账号）。"""
    msg = update.effective_message
    user = update.effective_user

    if not userdl.available():
        await msg.reply_text(TME_SETUP_TEXT, parse_mode=ParseMode.MARKDOWN)
        return

    status = await msg.reply_text("🔗 正在解析 Telegram 消息链接…")
    try:
        msgs = await userdl.fetch_messages(url)
    except userdl.NotLoggedIn:
        await safe_edit(
            status,
            "⚠️ 用户账号还没登录。\n"
            "在服务器上双击 `login_tg.bat`，按提示登录一次（之后一直有效）。",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    except Exception as exc:  # noqa: BLE001
        log.warning("解析 t.me 链接失败: %s", exc)
        await safe_edit(status, f"❌ {exc}")
        return

    total = sum(userdl.media_size(m) for m in msgs)
    if USER_QUOTA_MB:
        used = user_usage(user.id)
        if used + total > USER_QUOTA_MB * 1024 * 1024:
            await safe_edit(
                status,
                f"⚠️ 你的磁盘配额已用完（{human_size(used)} / {USER_QUOTA_MB} MB），"
                "请先用 /del 删除一些文件。",
            )
            return

    # 标签：用户消息/回复的文字优先，其次用频道消息自带的 caption
    if not tag:
        tag = clean_label(msgs[0].caption or "")

    ok, failed, done_bytes = 0, 0, 0
    album = len(msgs) > 1
    for idx, tmsg in enumerate(msgs, 1):
        filename = label_to_name(tag, userdl.media_name(tmsg), idx if album else None)
        dest = unique_path(DOWNLOAD_DIR / str(user.id), filename)
        await safe_edit(
            status,
            f"⬇️ `{dest.name}`\n{progress_bar(0, total)}"
            + (f"\n第 {idx}/{len(msgs)} 个" if album else ""),
        )
        last_push = {"t": 0.0}

        # telethon 的进度回调是同步的，节流后丢回事件循环刷新进度条
        def on_prog(cur: int, tot: int, _s=status, _n=dest.name, _idx=idx, _album=album):
            now = time.monotonic()
            if now - last_push["t"] >= PROGRESS_INTERVAL:
                last_push["t"] = now
                suffix = f"\n第 {_idx}/{len(msgs)} 个" if _album else ""
                asyncio.get_running_loop().create_task(
                    safe_edit(_s, f"⬇️ `{_n}`\n{progress_bar(cur, tot or 0)}{suffix}")
                )

        try:
            written = await userdl.download(tmsg, dest, on_prog)
            ok += 1
            done_bytes += written
            entry = {
                "id": new_file_id(),
                "name": dest.name,
                "label": tag,
                "size": written,
                "user_id": user.id,
                "username": user.username or user.full_name,
                "chat_id": msg.chat_id,
                "tg_file_id": None,
                "path": str(dest.relative_to(DOWNLOAD_DIR)),
                "source": "tme",
                "url": url,
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            await record(entry)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            log.warning("t.me 文件下载失败: %s", exc)
            dest.unlink(missing_ok=True)

    head = f"✅ {'相册' if album else '文件'}下载完成：{ok}/{len(msgs)} 个，共 {human_size(done_bytes)}"
    if failed:
        head += f"\n⚠️ {failed} 个失败"
    if tag:
        head += f"\n🏷 标签：{tag}"
    await safe_edit(status, head)


async def download_url(update: Update, context: ContextTypes.DEFAULT_TYPE, label: str = "") -> None:
    msg = update.effective_message
    user = update.effective_user
    url = URL_RE.search(msg.text).group(0).rstrip(").,，。、")
    tag = clean_label(label) or clean_label(replied_text(msg))

    if TME_RE.match(url):
        await download_tme(update, context, url, tag)
        return

    status = await msg.reply_text(f"🔗 正在拉取直链…\n`{url}`", parse_mode=ParseMode.MARKDOWN)
    limit = MAX_URL_SIZE_MB * 1024 * 1024 if MAX_URL_SIZE_MB else 0

    try:
        timeout = httpx.Timeout(connect=30.0, read=None, write=None, pool=None)
        proxy_kw = {"proxy": PROXY_URL} if PROXY_URL else {}
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, **proxy_kw) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                if "text/html" in content_type and not urllib.parse.urlparse(url).path.lower().endswith(
                    (".html", ".htm")
                ):
                    raise ValueError(
                        "这个链接返回的是*网页*而不是文件（HTML）。\n"
                        "通常是网盘分享页、需要登录的页面或普通网页链接。\n"
                        "请找到文件的*真实下载直链*再发给我。"
                    )
                filename = sanitize(guess_name(url, resp))
                if not Path(filename).suffix:
                    filename += CT_EXT.get(content_type, "")
                # 链接旁边的文字就是标签，例如「合同 https://...」
                filename = label_to_name(tag, filename)
                total = int(resp.headers.get("Content-Length") or 0)
                if limit and total and total > limit:
                    raise ValueError(f"文件 {human_size(total)} 超过上限 {MAX_URL_SIZE_MB} MB")

                folder = DOWNLOAD_DIR / str(user.id)
                dest = unique_path(folder, filename)
                tmp = dest.with_name(dest.name + ".part")
                done, last_push = 0, 0.0
                try:
                    with open(tmp, "wb") as fh:
                        async for chunk in resp.aiter_bytes(CHUNK):
                            fh.write(chunk)
                            done += len(chunk)
                            if limit and done > limit:
                                raise ValueError(f"文件超过上限 {MAX_URL_SIZE_MB} MB")
                            now = time.monotonic()
                            if now - last_push >= PROGRESS_INTERVAL:
                                last_push = now
                                await safe_edit(
                                    status,
                                    f"⬇️ `{dest.name}`\n{progress_bar(done, total)}",
                                )
                except BaseException:
                    tmp.unlink(missing_ok=True)
                    raise
                tmp.replace(dest)
    except ValueError as exc:
        await safe_edit(status, f"⚠️ {exc}")
        return
    except Exception as exc:  # noqa: BLE001
        log.exception("直链下载失败")
        await safe_edit(status, f"❌ 直链下载失败：{exc}")
        return

    entry = {
        "id": new_file_id(),
        "name": dest.name,
        "label": tag,
        "size": done,
        "user_id": user.id,
        "username": user.username or user.full_name,
        "chat_id": msg.chat_id,
        "tg_file_id": "",
        "path": str(dest.relative_to(DOWNLOAD_DIR)),
        "source": "url",
        "url": url,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    await record(entry)
    await safe_edit(
        status,
        f"✅ 已保存\n`{dest.name}`  ·  {human_size(done)}  ·  ID `{entry['id']}`",
    )


# ===================================================================== 命令


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not allowed(user.id, update.effective_chat.id):
        await request_access(update, context, user)
        return

    owner_note = ""
    if _auth.get("owner") is None:
        _auth["owner"] = user.id
        await save_auth()
        owner_note = "\U0001f451 *你已成为本机器人的所有者*（第一个 /start 的人）\n用 /auth 给别人授权，/help 看管理员命令。\n\n"

    await update.message.reply_text(
        owner_note
        + "\U0001f44b 我是文件下载机器人。\n\n"
        "*直接把文件发给我* —— 文档、视频、音频、语音、图片、动图、贴纸都行，\n"
        "我会保存到服务器磁盘，并给你一个文件 ID。\n\n"
        "也可以直接丢一个 *http(s) 直链* 给我，我帮你把文件拉下来。\n\n"
        "常用命令：\n"
        "/list — 最近下载的文件\n"
        "/search 关键字 — 搜索文件\n"
        "/get 文件ID — 把文件重新发给你\n"
        "/del 文件ID — 删除文件\n"
        "/stats — 统计信息\n"
        "/id — 查看你的用户 ID\n"
        "/help — 完整帮助",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "*使用说明*\n\n"
        "1️⃣ 发文件给我 → 自动下载并入库\n"
        "2️⃣ 发多个文件（相册）→ 合并处理\n"
        "3️⃣ 发直链 → 机器人帮你下载到服务器\n\n"
        "*文件命名（方便索引）*\n"
        "我用你给的 *文字标签* 当文件名，取用顺序：\n"
        "① 文件自带的文字说明（caption）\n"
        "② 回复某句话再发文件 → 用那句话\n"
        "③ 发链接时写在链接旁边的文字\n"
        "相册共用标签并自动编号：`标签_1.jpg`、`标签_2.jpg`\n"
        "三种都没有时才用原始文件名。\n\n"
        "*命令列表*\n"
        "/list \\[数量\\] — 列出最近的文件（默认 20 条）\n"
        "/search 关键字 — 按文件名/标签/上传者搜索\n"
        "/get 文件ID — 重新获取文件\n"
        "/del 文件ID — 删除文件（会二次确认）\n"
        "/mine — 我上传的文件\n"
        "/stats — 全站统计\n"
        "/id — 我的用户 ID / 会话 ID\n"
        "/dir — 服务器保存路径\n"
        "/help — 本帮助\n\n"
        "*管理员命令*（所有者可用）\n"
        "/auth 用户ID — 授权某人使用\n"
        "/deny 用户ID — 拒绝授权申请\n"
        "/unauth 用户ID — 取消授权\n"
        "/requests — 查看待审批申请\n"
        "/authlist — 已授权列表\n\n"
        f"单文件上限：官方 API 为 20MB，自建本地 API server 后可支持大文件。\n"
        f"保存目录：`{DOWNLOAD_DIR}`",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update.message.reply_text(
        f"你的用户 ID：`{user.id}`\n"
        f"用户名：@{user.username or '无'}\n"
        f"当前会话 ID：`{update.effective_chat.id}`\n"
        f"会话类型：{update.effective_chat.type}",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_dir(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    used = sum(f["size"] for f in FILES)
    await update.message.reply_text(
        f"📁 保存目录：`{DOWNLOAD_DIR}`\n"
        f"🗂 已入库文件：{len(FILES)} 个，共 {human_size(used)}",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    limit = 20
    if args and args[0].isdigit():
        limit = max(1, min(int(args[0]), 100))
    await _send_list(update.message, FILES[-limit:][::-1], f"最近 {limit} 个文件")


async def cmd_mine(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    items = [f for f in FILES if f["user_id"] == user.id][::-1][:30]
    await _send_list(update.message, items, "你上传的文件")


async def _send_list(msg: Message, items: list[dict[str, Any]], title: str) -> None:
    if not items:
        await msg.reply_text("📭 还没有文件。")
        return
    lines = [f"*{title}*（{len(items)} 条）\n"]
    for f in items:
        lines.append(
            f"`{f['id']}` · {f['name']}\n"
            f"    {human_size(f['size'])} · {f['created_at']} · {f.get('username', '?')}"
        )
    text = "\n".join(lines)
    if len(text) > 3800:
        text = text[:3800] + "\n…（太长已截断）"
    await msg.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("用法：`/search 关键字`", parse_mode=ParseMode.MARKDOWN)
        return
    kw = " ".join(context.args).lower()
    items = [
        f
        for f in FILES
        if kw in f["name"].lower()
        or kw in str(f.get("label", "")).lower()
        or kw in str(f.get("username", "")).lower()
    ]
    await _send_list(update.message, items[::-1][:30], f"搜索「{kw}」")


def _find(file_id: str) -> Optional[dict[str, Any]]:
    return next((f for f in FILES if f["id"] == file_id), None)


async def cmd_get(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("用法：`/get 文件ID`", parse_mode=ParseMode.MARKDOWN)
        return
    entry = _find(context.args[0].strip())
    if not entry:
        await update.message.reply_text("❓ 没找到这个文件 ID。用 /list 看看有哪些。")
        return
    path = DOWNLOAD_DIR / entry["path"]
    try:
        tg_id = entry.get("tg_file_id")
        if tg_id:
            await context.bot.send_document(
                update.effective_chat.id, tg_id, caption=f"{entry['name']} · {human_size(entry['size'])}"
            )
        elif path.exists():
            with open(path, "rb") as fh:
                await context.bot.send_document(
                    update.effective_chat.id,
                    fh,
                    filename=entry["name"],
                    caption=f"{entry['name']} · {human_size(entry['size'])}",
                )
        else:
            await update.message.reply_text("⚠️ 服务器上的文件已不存在。")
    except TelegramError as exc:
        await update.message.reply_text(f"❌ 发送失败（可能超过 50MB 上限）：{exc}")


async def cmd_del(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("用法：`/del 文件ID`", parse_mode=ParseMode.MARKDOWN)
        return
    entry = _find(context.args[0].strip())
    if not entry:
        await update.message.reply_text("❓ 没找到这个文件 ID。")
        return
    user = update.effective_user
    if entry["user_id"] != user.id and not is_admin(user.id):
        await update.message.reply_text("⛔️ 只能删除自己上传的文件。")
        return
    markup = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("🗑 确认删除", callback_data=f"del:{entry['id']}"),
            InlineKeyboardButton("取消", callback_data="del:cancel"),
        ]]
    )
    await update.message.reply_text(
        f"确认删除 `{entry['name']}`（{human_size(entry['size'])}）？",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=markup,
    )


async def on_del_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data.split(":", 1)[1]
    if data == "cancel":
        await query.edit_message_text("已取消。")
        return
    entry = _find(data)
    if not entry:
        await query.edit_message_text("文件已不存在。")
        return
    path = DOWNLOAD_DIR / entry["path"]
    try:
        path.unlink(missing_ok=True)
        # 顺带清掉空的用户目录
        if path.parent.exists() and not any(path.parent.iterdir()):
            path.parent.rmdir()
    except OSError as exc:
        await query.edit_message_text(f"⚠️ 删除磁盘文件失败：{exc}")
    FILES.remove(entry)
    await save_index()
    await query.edit_message_text(f"🗑 已删除 `{entry['name']}`", parse_mode=ParseMode.MARKDOWN)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("⛔️ 仅管理员可用。/mine 可以看自己的统计。")
        return
    users: dict[int, list[int]] = {}
    for f in FILES:
        stat = users.setdefault(f["user_id"], [0, 0])
        stat[0] += 1
        stat[1] += f["size"]
    used = sum(f["size"] for f in FILES)
    disk = os.popen(f'df -k "{DOWNLOAD_DIR}"').read().strip().splitlines() if os.name != "nt" else []
    free_line = ""
    if len(disk) > 1:
        parts = disk[1].split()
        if len(parts) >= 4:
            free_line = f"\n💾 磁盘剩余：{human_size(int(parts[3]) * 1024)}"

    top = sorted(users.items(), key=lambda kv: kv[1][1], reverse=True)[:10]
    lines = [
        "*📊 机器人统计*",
        f"文件总数：{len(FILES)}",
        f"占用空间：{human_size(used)}",
        f"用户数：{len(users)}",
        f"保存目录：`{DOWNLOAD_DIR}`{free_line}",
    ]
    if top:
        lines.append("\n*上传排行*")
        for uid, (cnt, size) in top:
            name = next((f["username"] for f in FILES if f["user_id"] == uid), str(uid))
            lines.append(f"`{uid}` {name} — {cnt} 个 / {human_size(size)}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_auth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔️ 仅所有者/管理员可用。")
        return
    if not context.args or not context.args[0].lstrip("-").isdigit():
        await update.message.reply_text(
            "用法：`/auth 用户ID`（用 /requests 查看申请列表）", parse_mode=ParseMode.MARKDOWN
        )
        return
    uid = int(context.args[0])
    info = _auth["pending"].pop(str(uid), {})
    note = " ".join(context.args[1:]) or str(info.get("username") or "")
    _auth["authorized"][str(uid)] = {
        "username": note,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "by": update.effective_user.id,
    }
    await save_auth()
    await update.message.reply_text(
        f"✅ 已授权 `{uid}`" + (f"（{note}）" if note else ""), parse_mode=ParseMode.MARKDOWN
    )
    try:
        await context.bot.send_message(uid, "✅ 管理员已批准你使用本机器人，现在可以发送文件了。")
    except TelegramError:
        pass


async def cmd_deny(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔️ 仅所有者/管理员可用。")
        return
    if not context.args or not context.args[0].lstrip("-").isdigit():
        await update.message.reply_text("用法：`/deny 用户ID`", parse_mode=ParseMode.MARKDOWN)
        return
    uid = str(int(context.args[0]))
    info = _auth["pending"].pop(uid, None)
    await save_auth()
    await update.message.reply_text(
        f"🚫 已拒绝 `{uid}` 的申请" if info else f"ℹ️ `{uid}` 没有待审批的申请",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_unauth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔️ 仅所有者/管理员可用。")
        return
    if not context.args or not context.args[0].lstrip("-").isdigit():
        await update.message.reply_text("用法：`/unauth 用户ID`", parse_mode=ParseMode.MARKDOWN)
        return
    uid = str(int(context.args[0]))
    if uid in _auth["authorized"]:
        name = _auth["authorized"].pop(uid).get("username") or ""
        await save_auth()
        await update.message.reply_text(f"🗑 已取消 `{uid}` 的授权" + (f"（{name}）" if name else ""), parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(f"ℹ️ `{uid}` 本来就没有授权。", parse_mode=ParseMode.MARKDOWN)


async def cmd_authlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔️ 仅所有者/管理员可用。")
        return
    lines = [f"👑 所有者：`{_auth.get('owner')}`", ""]
    if _auth["authorized"]:
        lines.append("*已授权*")
        for uid, info in _auth["authorized"].items():
            lines.append(f"`{uid}` {info.get('username') or ''} · {info.get('time')}")
    else:
        lines.append("*已授权*：无")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_requests(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔️ 仅所有者/管理员可用。")
        return
    if not _auth["pending"]:
        await update.message.reply_text("📭 暂无待审批申请。")
        return
    lines = ["*待审批申请*"]
    for uid, info in _auth["pending"].items():
        lines.append(f"`{uid}` {info.get('username') or ''} · {info.get('time')}\n    批准：/auth {uid}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ===================================================================== 消息分发


async def on_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    user = update.effective_user
    if not allowed(user.id, update.effective_chat.id):
        await request_access(update, context, user)
        return
    try:
        await msg.reply_chat_action(ChatAction.TYPING)
    except TelegramError:
        pass
    if msg.media_group_id:
        await handle_album(update, context)
    else:
        await process_single(update, context)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    user = update.effective_user
    if not allowed(user.id, update.effective_chat.id):
        await request_access(update, context, user)
        return
    text = msg.text or ""
    match = URL_RE.search(text)
    if match:
        # 链接前后的文字当标签，例如「合同 https://...」→ 文件名「合同.pdf」
        tag = (text[: match.start()] + " " + text[match.end() :]).strip()
        await download_url(update, context, label=tag)
    else:
        await msg.reply_text(
            "把文件或 http(s) 直链发给我就行，/help 看说明。\n\n"
            "提示：给文件配一句文字说明（caption），或者回复一句话再发文件，\n"
            "我就会用那句话给文件命名，之后可以用 /search 找。"
        )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("处理更新时出错", exc_info=context.error)


# ===================================================================== 启动


async def post_init(app: Application) -> None:
    load_index()
    load_auth()
    await app.bot.set_my_commands(
        [
            BotCommand("start", "开始使用"),
            BotCommand("help", "帮助"),
            BotCommand("list", "最近的文件"),
            BotCommand("mine", "我上传的文件"),
            BotCommand("search", "搜索文件"),
            BotCommand("get", "获取文件"),
            BotCommand("del", "删除文件"),
            BotCommand("stats", "统计信息"),
            BotCommand("id", "我的 ID"),
        ]
    )
    me = await app.bot.get_me()
    log.info("已登录：@%s (%s)", me.username, me.id)
    log.info("保存目录：%s", DOWNLOAD_DIR)
    log.info("白名单：%s", ALLOWED_USER_IDS or "所有人")
    log.info("API：%s", API_BASE_URL)


def build_app() -> Application:
    if not BOT_TOKEN or ":" not in BOT_TOKEN:
        raise SystemExit("❌ 未配置 BOT_TOKEN，请在 .env 或环境变量里填写。")

    builder = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).concurrent_updates(True)
    if API_BASE_URL != "https://api.telegram.org":
        builder = builder.base_url(f"{API_BASE_URL}/bot")
    if PROXY_URL:
        # 官方 API + 代理：所有请求（含长轮询、文件下载）都走代理
        common = {"proxy": PROXY_URL, "connect_timeout": 30.0, "read_timeout": 30.0, "write_timeout": 30.0}
        builder = builder.request(HTTPXRequest(**common)).get_updates_request(HTTPXRequest(**common))
    app = builder.build()

    media_filter = (
        filters.Document.ALL
        | filters.VIDEO
        | filters.AUDIO
        | filters.VOICE
        | filters.ANIMATION
        | filters.VIDEO_NOTE
        | filters.PHOTO
        | filters.Sticker.ALL
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("dir", cmd_dir))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("mine", cmd_mine))
    app.add_handler(CommandHandler("search", cmd_search))
    app.add_handler(CommandHandler("get", cmd_get))
    app.add_handler(CommandHandler("del", cmd_del))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("auth", cmd_auth))
    app.add_handler(CommandHandler("deny", cmd_deny))
    app.add_handler(CommandHandler("unauth", cmd_unauth))
    app.add_handler(CommandHandler("authlist", cmd_authlist))
    app.add_handler(CommandHandler("requests", cmd_requests))
    app.add_handler(CallbackQueryHandler(on_del_callback, pattern=r"^del:"))
    app.add_handler(MessageHandler(media_filter, on_media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
    return app


def main() -> None:
    if PROXY_URL:
        log.info("使用代理：%s", PROXY_URL)
    else:
        log.warning("未检测到代理，将直连 Telegram（国内网络大概率连不上）")
    log.info("正在启动文件下载机器人…")
    retries = 0
    while True:
        try:
            app = build_app()
            app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)
            break  # 正常退出（Ctrl+C）
        except KeyboardInterrupt:
            break
        except (TimedOut, NetworkError) as exc:
            retries += 1
            log.error("连接 Telegram 失败（第 %s 次）：%s —— 5 秒后自动重试", retries, exc)
            time.sleep(5)
        except TelegramError as exc:
            log.error("Telegram 错误：%s —— 10 秒后自动重试", exc)
            time.sleep(10)


if __name__ == "__main__":
    main()
