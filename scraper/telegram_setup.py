# -*- coding: utf-8 -*-
"""從 Telegram API 取出你的 chat id 並寫回 .env。

比去找 @userinfobot 可靠：直接讀你傳給自己 bot 的那則訊息，
拿到的一定是這個 bot 眼中的正確 chat id。
"""
import re
import sys
import logging
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
logging.basicConfig(level=logging.WARNING)

import requests                      # noqa: E402
from . import config as C            # noqa: E402

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def write_env(key, value):
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    out, replaced = [], False
    for ln in lines:
        if ln.startswith(key + "="):
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(ln)
    if not replaced:
        out.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")


def main():
    token = C.TELEGRAM_BOT_TOKEN
    if not token or token.startswith("123456789:AA"):
        print("[FAIL] .env 的 TELEGRAM_BOT_TOKEN 還沒填")
        return 1
    if not re.match(r"^\d+:[A-Za-z0-9_-]+$", token):
        print(f"[FAIL] token 格式不對（長度 {len(token)}）。"
              "應該長得像 8123456789:AAH...，注意有沒有多複製到空白或換行")
        return 1

    # 1. token 有效嗎
    r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=20)
    if r.status_code != 200 or not r.json().get("ok"):
        print(f"[FAIL] token 無效（HTTP {r.status_code}）。"
              "到 @BotFather 傳 /mybots → 選你的 bot → API Token 重新複製一次")
        return 1
    bot = r.json()["result"]
    print(f"[ OK ] token 有效：@{bot.get('username')}（{bot.get('first_name')}）")

    # 2. 從最近的訊息找 chat id
    r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=20)
    updates = r.json().get("result", []) if r.status_code == 200 else []
    chats = {}
    for u in updates:
        msg = u.get("message") or u.get("edited_message") or {}
        chat = msg.get("chat") or {}
        if chat.get("id"):
            chats[chat["id"]] = chat

    if not chats:
        print("[FAIL] 讀不到任何訊息。請到你的 bot 對話再傳一則訊息（例如 hi）後重跑。")
        print("       註：Telegram 只保留最近 24 小時的更新，且已被讀取過的會消失。")
        return 1

    if len(chats) > 1:
        print(f"[WARN] 找到 {len(chats)} 個對話，取第一個：")
        for cid, c in chats.items():
            print(f"       {cid}  {c.get('first_name') or c.get('title')}")

    chat_id, chat = next(iter(chats.items()))
    who = chat.get("first_name") or chat.get("username") or chat.get("title")
    print(f"[ OK ] chat id：{chat_id}（{who}）")

    write_env("TELEGRAM_CHAT_ID", str(chat_id))
    print("[ OK ] 已寫入 .env")

    # 3. 實際送一則
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id,
              "text": "✅ <b>104 職缺追蹤</b>\n推播管道設定完成，這是測試訊息。",
              "parse_mode": "HTML"},
        timeout=20)
    if r.status_code == 200 and r.json().get("ok"):
        print("[ OK ] 測試訊息已送出，請看手機")
        return 0
    print(f"[FAIL] 送出失敗：HTTP {r.status_code} {r.text[:200]}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
