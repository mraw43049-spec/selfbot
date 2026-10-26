# -*- coding: utf-8 -*-
"""ربات مستقل سلف روباهیو — polling، بدون وابستگی به ربات اصلی.

متغیرهای Railway:
  BOT_TOKEN      توکن ربات جدید (از @BotFather)
  ADMIN_IDS      آیدی عددی مدیرها، با کاما (مثلاً 123,456)
  SELF_API_ID    از my.telegram.org
  SELF_API_HASH  از my.telegram.org
  SELF_KEY       یه رشته‌ی تصادفی بلند برای رمزنگاری سشن‌ها (بعداً عوضش نکن!)
  DATABASE_URL   (اختیاری) Postgres جدا؛ بدون اون SQLite محلی که با دیپلوی پاک می‌شه
"""
import logging
import os

from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as KB
from telegram.ext import Application, CallbackQueryHandler, CommandHandler

import selfbot
import commerce
from database import init_db

logging.basicConfig(format="%(asctime)s %(name)s %(levelname)s %(message)s", level=logging.INFO)
logger = logging.getLogger("selfapp")

TOKEN = os.environ.get("BOT_TOKEN", "").strip()
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",") if x.strip().lstrip("-").isdigit()}


def _home_kb(uid):
    rows = []
    if uid in ADMIN_IDS:
        rows.append([B("مدیریت سلف", callback_data="selfa:menu", api_kwargs={"style": "primary"})])
    return KB(rows) if rows else None


async def cmd_start(update, context):
    u = update.effective_user
    if context.args and context.args[0] == "panel":
        await selfbot.cmd_self(update, context)
        return
    tx = ("🤖 به ربات سلف خوش اومدی.\n\n"
          "اگه مدیر برات فعالش کرده، /self رو بزن یا بنویس «سلف».")
    if u.id in ADMIN_IDS:
        tx += "\n\nمدیر: برای فعال‌کردن کاربر، دکمه‌ی پایین یا /admin."
    await update.effective_message.reply_text(tx, reply_markup=_home_kb(u.id))


async def cmd_admin(update, context):
    u = update.effective_user
    if u.id not in ADMIN_IDS:
        return
    await update.effective_message.reply_text("🤖 مدیریت سلف", reply_markup=selfbot.admin_menu_kb())


async def cb_back(update, context):
    q = update.callback_query
    await q.answer()
    context.user_data.pop("admin_action", None)
    await q.message.reply_text("🏠 منوی اصلی", reply_markup=_home_kb(q.from_user.id))


async def post_init(app):
    await selfbot.start_all(app)


def main():
    if not TOKEN:
        raise SystemExit("BOT_TOKEN تنظیم نشده")
    if not ADMIN_IDS:
        logger.warning("ADMIN_IDS خالیه؛ هیچ‌کس نمی‌تونه کاربر فعال کنه")
    init_db()
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    commerce.setup(app)
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CallbackQueryHandler(cb_back, pattern=r"^admin:back$"))
    selfbot.register(app, ADMIN_IDS)
    logger.info("selfbot standalone started")
    app.run_polling(allowed_updates=["message", "callback_query", "inline_query"], drop_pending_updates=True)


if __name__ == "__main__":
    main()
