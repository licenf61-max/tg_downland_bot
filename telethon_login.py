"""
登录 Telethon 用户账号（解析 t.me 链接用）——只需成功跑一次。

交互流程：输入手机号 → Telegram 发验证码（发给你的 Telegram App，不是短信）→
输入验证码 →（如果开了两步验证再输密码）。之后 session 存在 data/tg_user.session，
机器人启动即可直接使用，无需再登录。

双击 login_tg.bat 即可运行本脚本。
"""
from __future__ import annotations

import sys

from config import API_HASH, API_ID
import userdl


def main() -> None:
    if not userdl.available():
        if not userdl._HAS_TELETHON:
            print("未安装 telethon，请先安装：pip install telethon python-socks")
        else:
            print("请先在 .env 里填写 API_ID 和 API_HASH（在 https://my.telegram.org 免费申请）")
        sys.exit(1)

    from telethon import TelegramClient

    print("即将登录你的 Telegram 用户账号（用于解析 t.me 消息链接）")
    print("session 会保存在 data/tg_user.session，登录一次以后一直有效\n")

    client = TelegramClient(
        str(userdl.SESSION_PATH),
        API_ID,
        API_HASH,
        proxy=userdl.proxy_tuple(),
        device_model="file-bot",
    )

    # start() 会交互式地要手机号 / 验证码 / 两步验证密码
    client.start()
    me = client.get_me()
    print()
    print(f"登录成功：{me.first_name} (@{me.username or '无用户名'})  ID={me.id}")
    print("现在可以直接给机器人发 t.me/频道/123 链接了。")
    client.disconnect()


if __name__ == "__main__":
    main()
