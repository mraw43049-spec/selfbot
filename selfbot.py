# -*- coding: utf-8 -*-
"""
قابلیت «سلف» روبی.

- پشتیبانی از پنل مدیریت (🤖 سلف) با آیدی عددی کاربر، سلف رو برای اون کاربر فعال/غیرفعال می‌کنه.
- کاربرِ فعال‌شده با /self (یا نوشتن «سلف») اکانتش رو وصل می‌کنه و تا ۳ کلمه تنظیم می‌کنه:
  هر کلمه = (کلمه، گپ، تایمر، جای دکمه: چپ/وسط/راست/خالی).
- دستورهای نقطه‌ای داخل چت از اکانت کاربر: .پنل  .کلمه فلان  .زمان فلان  .دکمه فلان  .گپ [اسم گروه]
- حالت خودکار: اسلاتی که کلمه + گپ + تایمر (>= ۵ ثانیه) داره، هر تایمر ثانیه خودش کلمه رو توی گپ می‌گه و دکمه‌ی انتخابی رو می‌زنه.
- (حالت دستی قدیمی، فقط اگه تایمر صفر باشه) وقتی کاربر از اکانت خودش دقیقاً اون کلمه رو توی همون گپ بفرسته، بعد از تایمر، سلف روی دکمه‌ی انتخاب‌شده‌ی
  پیام ربات (ریپلای یا آخرین پیام دکمه‌دار ربات‌های دیگه) کلیک می‌کنه. روی ربات روباهیو هیچ‌وقت کلیک نمی‌کنه.

- ساعت زنده کنار اسم: .ساعت (روشن) | .ساعت 3 (انتخاب فونت) | .ساعت فونت (لیست) | .غیرفعال ساعت
- ایموجی پریمیوم (بدون اکانت پریمیوم، از طریق ربات اینلاین): .ایموجی متن [کد ایموجی]
- ریاکشن: روی پیام کاربر ریپلای کن و بنویس .ریاکشن 🔥❤👍  یا  /reykt  (اکانت پریمیوم تا ۳ ریاکشن روی همون پیام)
- کپی پروفایل: روی پیام یه کاربر ریپلای کن و بنویس .کپی  (اسم، بیو، عکس) | برگشت: .غیرفعال کپی

امنیت: سشن‌ها رمزنگاری‌شده ذخیره می‌شن (Fernet)، سلف هیچ‌وقت روی ربات روباهیو کلیک نمی‌کنه،
محدودیت سرعت داره و کاربر هر لحظه می‌تونه اتصال رو قطع کنه (خروج کامل از سشن).

متغیرهای Railway: SELF_API_ID و SELF_API_HASH (از my.telegram.org)، اختیاری: SELF_KEY.
"""
import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, Column, DateTime, String, Text
from telegram import InlineKeyboardButton as _IKB, InlineKeyboardMarkup as KB, KeyboardButton as _KB, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram import InlineQueryResultArticle, InlineQueryResultCachedPhoto, InlineQueryResultPhoto, InputTextMessageContent, MessageEntity
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, InlineQueryHandler, MessageHandler, filters

from database import Base, get_session


# ---------- دکمه‌های رنگی (بدون ایموجی) ----------
# Bot API 9.4: style = primary (آبی) | success (سبز) | danger (قرمز). روی تلگرام قدیمی بدون رنگ دیده می‌شه.
_EMO = re.compile("[\U0001F000-\U0001FFFF\u2190-\u21FF\u2300-\u23FF\u25A0-\u25FF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D\u20E3]")
_BTN_MAP = {"⌫": "حذف", "◀️": "قبلی", "▶️": "بعدی", "🔙": "بازگشت", "✔️ ": "» "}


def _clean(t):
    t = t or ""
    if t in _BTN_MAP:
        return _BTN_MAP[t]
    t = t.replace("✔️ ", "» ")
    t = _EMO.sub("", t)
    return re.sub(r"\s+", " ", t).strip() or "•"


def _style_for(text, cd):
    cd = cd or ""
    t = text or ""
    if cd in ("self:disc", "self:kc", "selfa:off", "selfa:del", "self:discy") or cd.startswith("self:d:") \
            or any(w in t for w in ("لغو", "انصراف", "قطع", "توقف", "خاموش", "حذف")) and cd != "self:kx":
        return "danger"
    if cd in ("self:ky", "self:conn", "selfa:on", "self:tog", "self:clkt") or any(w in t for w in ("فعال", "روشن", "تأیید", "اتصال")):
        return "success"
    return "primary"


def B(text, callback_data=None, style=None, **kw):
    txt = _clean(text)
    return _IKB(txt, callback_data=callback_data, api_kwargs={"style": style or _style_for(text, callback_data)}, **kw)


def KeyboardButton(text, **kw):
    return _KB(_clean(text), api_kwargs={"style": "danger" if "لغو" in text else "primary"}, **kw)


logger = logging.getLogger(__name__)

MAX_SLOTS = 3
MAX_DELAY = 3600
MIN_AUTO = 5      # تایمر >= این مقدار یعنی حالت خودکار: هر N ثانیه کلمه رو خودش توی گپ می‌گه
POS = {"l": "⬅️ چپ", "m": "⏺ وسط", "r": "➡️ راست", "n": "⬜ خالی"}
API_ID = int(os.environ.get("SELF_API_ID", "0") or 0)
API_HASH = os.environ.get("SELF_API_HASH", "").strip()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()


class SelfAccess(Base):
    __tablename__ = "self_access"
    user_id = Column(BigInteger, primary_key=True)
    enabled = Column(Boolean, default=True)
    paused = Column(Boolean, default=False)
    phone = Column(String, nullable=True)
    session_enc = Column(Text, nullable=True)
    slots = Column(Text, default="[]")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class SelfProfile(Base):
    """وضعیت ساعت و کپی پروفایل هر کاربر (JSON)."""
    __tablename__ = "self_profile"
    user_id = Column(BigInteger, primary_key=True)
    data = Column(Text, default="{}")


# ---------- ابزارها ----------
_FA = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def fa2en(s):
    return (s or "").translate(_FA)


def norm(s):
    return re.sub(r"[\u200c\u200d\ufe0f\s]+", " ", s or "").strip().lower()


def _fernet():
    from cryptography.fernet import Fernet
    raw = os.environ.get("SELF_KEY", "").strip() or ("rubahio-self:" + BOT_TOKEN)
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest()))


def enc(s):
    return _fernet().encrypt(s.encode()).decode()


def dec(s):
    return _fernet().decrypt(s.encode()).decode()


def configured():
    return bool(API_ID and API_HASH)


def get_row(uid):
    s = get_session()
    try:
        r = s.get(SelfAccess, uid)
        if r:
            s.expunge(r)
        return r
    finally:
        s.close()


def save_row(uid, **kw):
    s = get_session()
    try:
        r = s.get(SelfAccess, uid)
        if not r:
            r = SelfAccess(user_id=uid, enabled=True, paused=False, slots="[]")
            s.add(r)
        for k, v in kw.items():
            setattr(r, k, v)
        s.commit()
    finally:
        s.close()


# کلمه‌هایی که هیچ‌کس نمی‌تونه برای اسلات تنظیم کنه (فاصله، ریزه‌کاری نوشتاری و حروف تکراری هم دور زدنش نمی‌شه)
_BANNED_RAW = ("روب روب", "شکار", "هور هور", "عو عو", "روباه")
_BAN_TR = str.maketrans("يكۀةىئؤأإآ", "یکههیییااا")


def _squash(t):
    t = (t or "").translate(_BAN_TR).lower()
    t = re.sub(r"[\u064B-\u065F\u0670\u0640]", "", t)      # اعراب و کشیده
    t = re.sub(r"[^\w]|_", "", t)                              # فاصله، نیم‌فاصله، علامت‌ها
    return re.sub(r"(.)\1+", r"\1", t)                         # روووب → روب


_BANNED = tuple(_squash(w) for w in _BANNED_RAW)


def is_banned_word(text):
    q = _squash(text)
    return bool(q) and any(b in q for b in _BANNED)


def slots_of(row):
    try:
        v = json.loads(row.slots or "[]")
        out = [x for x in v if isinstance(x, dict)][:MAX_SLOTS]
        for x in out:      # کلمهٔ ممنوعه‌ای که قبلاً ذخیره شده هم کار نمی‌کنه و از پنل پاک می‌شه
            if is_banned_word(x.get("word")):
                x["word"] = ""
        return out
    except Exception:
        return []


def parse_delay(text):
    m = re.match(r"^(\d{1,5})\s*(s|m|ثانیه|دقیقه)?$", fa2en(text).strip().lower())
    if not m:
        return None
    n = int(m.group(1)) * (60 if m.group(2) in ("m", "دقیقه") else 1)
    return n if 0 <= n <= MAX_DELAY else None


def fmt_delay(n):
    n = int(n or 0)
    return f"{n // 60} دقیقه" if n >= 60 and n % 60 == 0 else f"{n} ثانیه"


# ---------- هسته‌ی سلف (Telethon) ----------
_clients = {}
_slots = {}
_paused = {}
_hist = {}
_last = {}
_login = {}
_bot_id = [0]
_capture = {}
_botref = [None]
_own = {}   # (uid, chat, کلمه) -> زمان؛ پیام‌هایی که خود سلف می‌فرسته و نباید دوباره تریگر بشن
_feature_tasks = {}  # uid -> asyncio tasks for presence/typing helpers
_typing_emit_at = {}


def _load_runtime(uid, row):
    _slots[uid] = slots_of(row)
    _paused[uid] = bool(row.paused)
    _sync_auto(uid)


async def stop_client(uid, logout=False, restore=False):
    c = _clients.pop(uid, None)
    _cancel_auto(uid)
    _cancel_clock(uid)
    for task in _feature_tasks.pop(uid, []):
        task.cancel()
    _typing_emit_at.pop(uid, None)
    try:
        import extras
        extras.cleanup(uid)
    except Exception:  # noqa: BLE001
        pass
    _slots.pop(uid, None)
    if c and restore:
        try:
            await _reset_profile(uid, c)
        except Exception as e:  # noqa: BLE001
            logger.info("self reset profile %s: %s", uid, e)
    if c:
        try:
            if logout:
                await c.log_out()
            else:
                await c.disconnect()
        except Exception:
            try:
                await c.disconnect()
            except Exception:
                pass


async def start_client(uid):
    if not configured():
        return False
    from telethon import TelegramClient, events
    from telethon.sessions import StringSession
    row = get_row(uid)
    if not (row and row.enabled and row.session_enc):
        return False
    await stop_client(uid)
    try:
        client = TelegramClient(StringSession(dec(row.session_enc)), API_ID, API_HASH)
        await client.connect()
        if not await client.is_user_authorized():
            save_row(uid, session_enc=None)
            await client.disconnect()
            return False
    except Exception as e:  # noqa: BLE001
        logger.warning("self start failed %s: %s", uid, e)
        return False
    _load_runtime(uid, row)

    async def _h(ev, _uid=uid):
        await _on_out(_uid, ev)
    client.add_event_handler(_h, events.NewMessage(outgoing=True))
    async def _raw_typing(update, _uid=uid, _client=client):
        await _mirror_typing(_uid, _client, update)
    client.add_event_handler(_raw_typing, events.Raw)
    try:
        import extras
        extras.attach(uid, client)
    except Exception as e:  # noqa: BLE001
        logger.warning("extras attach %s: %s", uid, e)
    _clients[uid] = client
    _sync_auto(uid)
    _sync_clock(uid)
    _sync_presence(uid)
    return True


async def _on_out(uid, ev):
    cap = _capture.get(uid)
    if cap:
        if time.time() - cap["ts"] > 300:
            _capture.pop(uid, None)
        elif ev.chat_id == cap["chat"] and (ev.raw_text or "").strip():
            _capture.pop(uid, None)
            if is_banned_word(ev.raw_text):
                try:
                    await _botref[0].send_message(uid, "⛔ این کلمه مجاز نیست و برای اسلات ثبت نشد. یه کلمهٔ دیگه انتخاب کن.")
                except Exception as e:  # noqa: BLE001
                    logger.info("self banned notify failed: %s", e)
                return
            sl = _get_slots(uid)
            sl[cap["slot"]]["word"] = (ev.raw_text or "").strip()[:40]
            sl[cap["slot"]].setdefault("delay", 0)
            _put_slots(uid, sl)
            try:
                tx, kb = _slot_panel(uid, cap["slot"])
                await _botref[0].send_message(uid, f"✅ کلمه خودکار ثبت شد: «{sl[cap['slot']]['word']}»\n\n" + tx,
                                              reply_markup=kb, parse_mode="HTML")
            except Exception as e:  # noqa: BLE001
                logger.info("self capture notify failed: %s", e)
            return
    if await _handle_dot(uid, ev):
        return
    # Apply selected Unicode font to ordinary text messages. Commands remain untouched.
    dfont = _pget(uid)
    mode = dfont.get("font_mode", "ساده")
    fmap = _FONT_MAPS.get(mode)
    original = (ev.raw_text or "")
    if fmap and original and not original.lstrip().startswith((".", "/")) and not ev.media:
        try:
            changed = original.translate(fmap)
            # Unicode styled alphabets cover Latin letters but not Persian. Use Telegram
            # formatting entities for Persian text, while retaining styled Latin letters.
            if mode in ("بولد", "ایتالیک", "مونو") and re.search(r"[\u0600-\u06FF]", original):
                import html as _html
                tag = {"بولد": "b", "ایتالیک": "i", "مونو": "code"}[mode]
                if mode == "بولد":
                    changed = original.translate(fmap) if fmap else original
                # Escape text before wrapping so user input cannot inject markup.
                safe = _html.escape(changed, quote=False)
                await ev.edit(f"<{tag}>{safe}</{tag}>", parse_mode="html")
            elif changed != original:
                await ev.edit(changed, parse_mode=None)
        except Exception as e:
            logger.debug("font transform skipped %s: %s", uid, e)
    if _paused.get(uid):
        return
    txt = norm(ev.raw_text or "")
    if not txt:
        return
    if _own.pop((uid, ev.chat_id, txt), 0) > time.time():
        return   # پیامی که خود سلف فرستاده؛ دوباره تریگر نشه
    for i, s in enumerate(_slots.get(uid, [])):
        if s and s.get("word") and s.get("chat") and ev.chat_id == s["chat"] and ev.chat_id != _bot_id[0] and norm(s["word"]) == txt and int(s.get("delay", 0) or 0) < MIN_AUTO:
            now = time.time()
            if now - _last.get((uid, i), 0) < 2:
                continue
            h = [t for t in _hist.get(uid, []) if now - t < 60]
            if len(h) >= 20:
                return
            h.append(now)
            _hist[uid] = h
            _last[(uid, i)] = now
            asyncio.create_task(_fire(uid, ev, s))


def _pick_button(msg, pos):
    rows = [r for r in (msg.buttons or []) if r]
    if not rows or pos == "n":
        return None
    row = rows[0]
    if pos == "l":
        return row[0]
    if pos == "r":
        return row[-1]
    if len(row) == 1:
        return row[0]
    return row[len(row) // 2] if len(row) % 2 == 1 else None


async def _usable(m):
    """پیام دکمه‌دار از یک ربات؛ هیچ‌وقت ربات روباهیو."""
    if not m.buttons or m.sender_id == _bot_id[0]:
        return False
    snd = await m.get_sender()
    return bool(snd and getattr(snd, "bot", False))


async def _fire(uid, ev, s):
    try:
        await asyncio.sleep(max(0, int(s.get("delay", 0))))
        client = _clients.get(uid)
        if not client or _paused.get(uid) or ev.chat_id == _bot_id[0]:
            return
        pos = s.get("pos", "n")
        # سر تایمر کلمه رو توی همون گپ می‌گه (ریپلای هم باشه، ریپلای می‌کنه)
        word = (s.get("word") or "").strip()
        if word:
            _own[(uid, ev.chat_id, norm(word))] = time.time() + 15
            await client.send_message(ev.chat_id, word, reply_to=ev.reply_to_msg_id if ev.is_reply else None)
        if pos == "n":
            return
        target = None
        if ev.is_reply:
            m = await ev.get_reply_message()
            if m and await _usable(m):
                target = m
        if not target:
            async for m in client.iter_messages(ev.chat_id, limit=15):
                if m.buttons and await _usable(m):
                    target = m
                    break
        btn = _pick_button(target, pos) if target else None
        if btn:
            await btn.click()
    except Exception as e:  # noqa: BLE001
        logger.info("self fire failed %s: %s", uid, e)


# ---------- حالت خودکار: هر N ثانیه کلمه رو خودش توی گپ می‌گه و دکمه رو می‌زنه ----------
_auto = {}   # (uid, اسلات) -> (امضا، تسک)


def _auto_sig(s):
    return (s.get("word"), s.get("chat"), int(s.get("delay", 0) or 0), s.get("pos", "n"))


def _cancel_auto(uid):
    for k in [k for k in _auto if k[0] == uid]:
        try:
            _auto.pop(k)[1].cancel()
        except Exception:
            pass


def _sync_auto(uid):
    """تسک‌های خودکار رو با تنظیمات فعلی هماهنگ می‌کنه (تغییر تنظیمات = ری‌استارت همون اسلات)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    sl = _slots.get(uid, []) if uid in _clients else []
    want = {}
    for i, s in enumerate(sl):
        if s and s.get("word") and s.get("chat") and int(s.get("delay", 0) or 0) >= MIN_AUTO:
            want[(uid, i)] = _auto_sig(s)
    for k in [k for k in _auto if k[0] == uid and (k not in want or _auto[k][0] != want[k])]:
        _auto.pop(k)[1].cancel()
    for k, sig in want.items():
        if k not in _auto:
            _auto[k] = (sig, loop.create_task(_auto_loop(uid, k[1], sig)))


async def _auto_loop(uid, i, sig):
    delay = sig[2]
    try:
        while True:
            await asyncio.sleep(delay)
            client = _clients.get(uid)
            sl = _slots.get(uid, [])
            cur = sl[i] if i < len(sl) else None
            if not client or not cur or _auto_sig(cur) != sig:
                return
            if _paused.get(uid):
                continue
            now = time.time()
            h = [t for t in _hist.get(uid, []) if now - t < 60]
            if len(h) >= 20:
                continue
            h.append(now)
            _hist[uid] = h
            try:
                await _auto_fire(uid, client, cur)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.info("self auto fire %s/%s: %s", uid, i, e)
                wait = getattr(e, "seconds", None)
                if isinstance(wait, int) and wait > 0:
                    await asyncio.sleep(min(wait, 300))
    except asyncio.CancelledError:
        raise


async def _auto_fire(uid, client, s):
    chat = s["chat"]
    word = (s.get("word") or "").strip()
    if not word or chat == _bot_id[0]:
        return
    _own[(uid, chat, norm(word))] = time.time() + 15
    sent = await client.send_message(chat, word)
    pos = s.get("pos", "n")
    if pos == "n":
        return
    target = None
    for _ in range(4):                      # جواب ربات رو چند ثانیه صبر می‌کنه
        await asyncio.sleep(1.5)
        async for m in client.iter_messages(chat, limit=10):
            if m.id <= sent.id:
                break
            if m.buttons and await _usable(m):
                target = m
                break
        if target:
            break
    if not target:                          # جواب تازه نیومد؛ آخرین پیام دکمه‌دار رباتا
        async for m in client.iter_messages(chat, limit=15):
            if m.buttons and await _usable(m):
                target = m
                break
    btn = _pick_button(target, pos) if target else None
    if btn:
        await btn.click()


async def start_all(app):
    _bot_id[0] = app.bot.id
    _botref[0] = app.bot
    asyncio.create_task(prepare_banner(app))
    if not configured():
        logger.info("self: SELF_API_ID/SELF_API_HASH تنظیم نشده؛ سلف غیرفعال است")
        return
    s = get_session()
    try:
        ids = [r.user_id for r in s.query(SelfAccess).filter(SelfAccess.enabled.is_(True), SelfAccess.session_enc.isnot(None)).all()]
    finally:
        s.close()

    async def _go():
        for uid in ids:
            try:
                await start_client(uid)
            except Exception as e:  # noqa: BLE001
                logger.warning("self autostart %s: %s", uid, e)
            await asyncio.sleep(1.5)
    asyncio.create_task(_go())


async def _dialogs(client):
    out = []
    try:
        async for d in client.iter_dialogs(limit=300):
            if d.id == _bot_id[0]:
                continue
            if d.is_group:
                out.append((d.id, d.name or str(d.id), True))
            elif d.is_user and not getattr(d.entity, "bot", False):
                out.append((d.id, d.name or str(d.id), False))
    except Exception as e:  # noqa: BLE001
        logger.info("dialogs failed: %s", e)
    return out


# ---------- ساعت زنده کنار اسم + کپی پروفایل ----------
# هر فونت: (اسم، ۱۰ رقم از 0 تا 9، علامت دونقطه)
CLOCK_FONTS = [
    ("عادی", "0123456789", ":"),
    ("بولد", "𝟎𝟏𝟐𝟑𝟒𝟓𝟔𝟕𝟖𝟗", ":"),
    ("دابل", "𝟘𝟙𝟚𝟛𝟜𝟝𝟞𝟟𝟠𝟡", ":"),
    ("مونو", "𝟶𝟷𝟸𝟹𝟺𝟻𝟼𝟽𝟾𝟿", ":"),
    ("سنس بولد", "𝟬𝟭𝟮𝟯𝟰𝟱𝟲𝟳𝟴𝟵", ":"),
    ("سنس", "𝟢𝟣𝟤𝟥𝟦𝟧𝟨𝟩𝟪𝟫", ":"),
    ("دایره‌ای", "⓪①②③④⑤⑥⑦⑧⑨", ":"),
    ("فول‌وید", "０１２３４５６７８９", "："),
    ("فارسی", "۰۱۲۳۴۵۶۷۸۹", ":"),
    ("بالانویس", "⁰¹²³⁴⁵⁶⁷⁸⁹", ":"),
]
CLOCK_TZ = os.environ.get("SELF_TZ", "Asia/Tehran").strip() or "Asia/Tehran"
_clock_tasks = {}


def _tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(CLOCK_TZ)
    except Exception:
        from datetime import timedelta
        return timezone(timedelta(hours=3, minutes=30))   # ایران (بدون ساعت تابستانی)


def fmt_clock(font=1, now=None):
    font = font if isinstance(font, int) and 1 <= font <= len(CLOCK_FONTS) else 1
    _, digits, colon = CLOCK_FONTS[font - 1]
    t = (now or datetime.now(_tz())).strftime("%H:%M")
    return "".join(colon if ch == ":" else digits[int(ch)] for ch in t)


def _u16(s):
    return len(s.encode("utf-16-le")) // 2


def build_first_name(base_first, clock_txt=None, limit=64):
    """اسم + ساعت؛ اگه اسم بلند بود اسم کوتاه می‌شه تا ساعت جا بشه."""
    base_first = (base_first or "").strip()
    if not clock_txt:
        return base_first[:limit] or "."
    room = limit - _u16(clock_txt) - 1
    if room <= 0 or not base_first:
        return clock_txt
    while base_first and _u16(base_first) > room:
        base_first = base_first[:-1]
    return (base_first.rstrip() + " " + clock_txt) if base_first else clock_txt


def _pget(uid):
    s = get_session()
    try:
        r = s.get(SelfProfile, uid)
        return json.loads(r.data or "{}") if r else {}
    except Exception:
        return {}
    finally:
        s.close()


def _pput(uid, d):
    s = get_session()
    try:
        r = s.get(SelfProfile, uid)
        if not r:
            r = SelfProfile(user_id=uid)
            s.add(r)
        r.data = json.dumps(d, ensure_ascii=False)
        s.commit()
    finally:
        s.close()


async def _ensure_orig(uid, client):
    """پروفایل اصلی کاربر (قبل از ساعت/کپی) یک‌بار ذخیره می‌شه تا برگردوندنش ممکن باشه."""
    d = _pget(uid)
    if "orig_first" in d:
        return d
    from telethon.tl.functions.users import GetFullUserRequest
    me = await client.get_me()
    full = await client(GetFullUserRequest(me))
    d["orig_first"] = me.first_name or ""
    d["orig_last"] = me.last_name or ""
    d["orig_about"] = getattr(full.full_user, "about", None) or ""
    _pput(uid, d)
    return d


def _maybe_clear(uid):
    d = _pget(uid)
    if not d.get("clock_on") and not d.get("copy_on"):
        _pput(uid, {"font": d.get("font", 1)})


async def _apply_name(uid, client, about=None):
    from telethon.tl.functions.account import UpdateProfileRequest
    d = _pget(uid)
    if d.get("copy_on"):
        bf, bl = d.get("copy_first", ""), d.get("copy_last", "")
    else:
        bf, bl = d.get("orig_first", ""), d.get("orig_last", "")
    clk = fmt_clock(d.get("font", 1)) if d.get("clock_on") else None
    first = build_first_name(bf, clk)
    await client(UpdateProfileRequest(first_name=first, last_name=(bl or "")[:64], about=about))
    d["last_clock"] = clk
    _pput(uid, d)


def _cancel_clock(uid):
    t = _clock_tasks.pop(uid, None)
    if t:
        try:
            t.cancel()
        except Exception:
            pass


def _sync_clock(uid):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    t = _clock_tasks.get(uid)
    if t and not t.done():
        return
    if uid in _clients and _pget(uid).get("clock_on"):
        _clock_tasks[uid] = loop.create_task(_clock_loop(uid))


async def _clock_loop(uid):
    from telethon import errors
    last = None
    try:
        while True:
            client = _clients.get(uid)
            d = _pget(uid)
            if not client or not d.get("clock_on"):
                return
            cur = fmt_clock(d.get("font", 1))
            wait = 0
            if cur != last:
                try:
                    await _apply_name(uid, client)
                    last = cur
                except errors.FloodWaitError as e:
                    wait = min(int(getattr(e, "seconds", 60)) + 2, 3600)
                except Exception as e:  # noqa: BLE001
                    logger.info("self clock %s: %s", uid, e)
                    wait = 30
            if not wait:
                wait = 60 - (time.time() % 60) + 0.7
            await asyncio.sleep(wait)
    except asyncio.CancelledError:
        raise
    finally:
        if _clock_tasks.get(uid) is asyncio.current_task():
            _clock_tasks.pop(uid, None)


def clock_fonts_text(uid):
    d = _pget(uid)
    cur = d.get("font", 1)
    lines = [f"🕒 ساعت: {'✅ روشن' if d.get('clock_on') else '❌ خاموش'}   فونت فعلی: {cur}", ""]
    for i, (name, _, _) in enumerate(CLOCK_FONTS, 1):
        lines.append(f"{i}) {fmt_clock(i)}   {name}" + ("  ✔️" if i == cur else ""))
    lines += ["", "انتخاب: .ساعت 3   |   خاموش: .غیرفعال ساعت"]
    return "\n".join(lines)


async def clock_set(uid, on=None, font=None):
    client = _clients.get(uid)
    if not client:
        return "❌ اول اکانتت رو وصل کن (/self)."
    d = _pget(uid)
    if font is not None:
        d["font"] = font
    try:
        if on is False:
            if not d.get("clock_on"):
                return "🕒 ساعت از قبل خاموشه."
            d["clock_on"] = False
            _pput(uid, d)
            _cancel_clock(uid)
            await _apply_name(uid, client)
            _maybe_clear(uid)
            return "✅ ساعت خاموش شد و اسمت برگشت."
        d = await _ensure_orig(uid, client) if not d.get("clock_on") else d
        if font is not None:
            d["font"] = font
        d["clock_on"] = True
        _pput(uid, d)
        await _apply_name(uid, client)
        _sync_clock(uid)
        return f"✅ ساعت زنده روشن شد: {fmt_clock(d.get('font', 1))}  (فونت {d.get('font', 1)})\nتغییر فونت: .ساعت 2   |   لیست: .ساعت فونت"
    except Exception as e:  # noqa: BLE001
        logger.info("self clock_set %s: %s", uid, e)
        return "⚠️ خطا: " + str(e)[:120]


async def _delete_copy_photo(client, d):
    pid = d.pop("copy_photo", None)
    if not pid:
        return
    from telethon import utils
    from telethon.tl.functions.photos import DeletePhotosRequest
    async for ph in client.iter_profile_photos("me", limit=10):
        if ph.id == pid:
            await client(DeletePhotosRequest([utils.get_input_photo(ph)]))
            break


async def copy_on(uid, ev):
    client = _clients.get(uid)
    if not client:
        return "❌ اول اکانتت رو وصل کن (/self)."
    if not ev.is_reply:
        return "↩️ روی پیام اون کاربر ریپلای کن و بنویس: .کپی"
    try:
        import io
        from telethon.tl.types import User as TUser
        from telethon.tl.functions.users import GetFullUserRequest
        from telethon.tl.functions.photos import UploadProfilePhotoRequest
        msg = await ev.get_reply_message()
        target = await msg.get_sender() if msg else None
        if not isinstance(target, TUser):
            return "❌ فقط روی پیام یه کاربر می‌شه کپی کرد (نه کانال/ادمین ناشناس)."
        me = await client.get_me()
        if target.id == me.id:
            return "😅 این خودتی!"
        d = await _ensure_orig(uid, client)
        full = await client(GetFullUserRequest(target))
        about = getattr(full.full_user, "about", None) or ""
        about = about[: (140 if getattr(me, "premium", False) else 70)]
        first = (target.first_name or "").strip() or (target.username or "") or "."
        last = (target.last_name or "").strip()
        photo_bytes = None
        try:
            photo_bytes = await client.download_profile_photo(target, file=bytes)
        except Exception as e:  # noqa: BLE001
            logger.info("self copy photo dl: %s", e)
        await _delete_copy_photo(client, d)    # اگه قبلاً کپی دیگه‌ای فعال بود، عکسش پاک بشه
        new_pid = None
        if photo_bytes:
            bio = io.BytesIO(photo_bytes)
            bio.name = "photo.jpg"
            up = await client.upload_file(bio)
            res = await client(UploadProfilePhotoRequest(file=up))
            new_pid = getattr(getattr(res, "photo", None), "id", None)
        d["copy_on"] = True
        d["copy_first"], d["copy_last"] = first[:64], last[:64]
        d["copy_photo"] = new_pid
        d["copy_about"] = about
        _pput(uid, d)
        try:
            await _apply_name(uid, client, about=about)
        except Exception:
            await _apply_name(uid, client, about=about[:70])
        _sync_clock(uid)
        extra = "" if photo_bytes else "\n(عکس نداشت یا مخفیه؛ عکس خودت دست نخورد.)"
        return f"✅ پروفایل «{first}» کپی شد (اسم، بیو" + ("، عکس" if photo_bytes else "") + ")." + extra + "\nبرگشت: .غیرفعال کپی"
    except Exception as e:  # noqa: BLE001
        logger.info("self copy %s: %s", uid, e)
        return "⚠️ کپی نشد: " + str(e)[:120]


async def copy_off(uid):
    client = _clients.get(uid)
    if not client:
        return "❌ اول اکانتت رو وصل کن (/self)."
    d = _pget(uid)
    if not d.get("copy_on"):
        return "ℹ️ کپی فعال نیست."
    try:
        await _delete_copy_photo(client, d)
        for k in ("copy_on", "copy_first", "copy_last", "copy_about"):
            d.pop(k, None)
        _pput(uid, d)
        await _apply_name(uid, client, about=d.get("orig_about", ""))
        _maybe_clear(uid)
        return "✅ کپی غیرفعال شد؛ اسم، بیو و عکس خودت برگشت."
    except Exception as e:  # noqa: BLE001
        logger.info("self copy_off %s: %s", uid, e)
        return "⚠️ خطا: " + str(e)[:120]


async def _reset_profile(uid, client):
    """موقع قطع اتصال/غیرفعال‌سازی: ساعت و کپی برداشته می‌شن و پروفایل اصلی برمی‌گرده."""
    d = _pget(uid)
    if not d.get("clock_on") and not d.get("copy_on"):
        return
    had_copy = bool(d.get("copy_on"))
    if had_copy:
        await _delete_copy_photo(client, d)
    for k in ("copy_on", "copy_first", "copy_last", "copy_about"):
        d.pop(k, None)
    d["clock_on"] = False
    _pput(uid, d)
    await _apply_name(uid, client, about=d.get("orig_about", "") if had_copy else None)
    _maybe_clear(uid)


def _clock_panel(uid):
    d = _pget(uid)
    on = bool(d.get("clock_on"))
    cur = d.get("font", 1)
    tx = (f"🕒 <b>ساعت زنده</b>\n\nوضعیت: {'✅ روشن' if on else '❌ خاموش'}\n"
          f"نمونه: {fmt_clock(cur)}\n\nساعت کنار اسم اکانتت (به وقت ایران) هر دقیقه به‌روز می‌شه. فونت دلخواهت رو انتخاب کن:\n"
          "⚠️ تا ساعت روشنه اسمت رو دستی عوض نکن؛ اول ساعت رو خاموش کن.")
    kb = [[B("⏹ خاموش کردن" if on else "▶️ روشن کردن", callback_data="self:clkt")]]
    row = []
    for i in range(1, len(CLOCK_FONTS) + 1):
        row.append(B(f"{'✔️ ' if i == cur else ''}{fmt_clock(i)}", callback_data=f"self:clkf:{i}"))
        if len(row) == 2:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    kb.append([B("🔙 بازگشت", callback_data="self:home")])
    return tx, KB(kb)


EMOJI_BOT = (os.environ.get("SELF_EMOJI_BOT", "AS_EmojiBot").strip() or "AS_EmojiBot").lstrip("@")


async def send_premium(client, chat, text, reply_to=None):
    """ایموجی پریمیوم از راه ربات اینلاین: همون کاری که کاربر دستی می‌کنه (@AS_EmojiBot متن [کد])."""
    res = await client.inline_query(EMOJI_BOT, text, entity=chat)
    if not res:
        return False
    await res[0].click(chat, reply_to=reply_to)
    return True


REACT_DEFAULT = ["❤", "🔥", "👍"]
_EMO_RE = re.compile("(?:[\\U0001F1E6-\\U0001F1FF]{2}|[\\u2600-\\u27BF\\u2B00-\\u2BFF\\U0001F000-\\U0001F1E5\\U0001F200-\\U0001FAFF]\\ufe0f?[\\U0001F3FB-\\U0001F3FF]?(?:\\u200d[\\u2600-\\u27BF\\u2B00-\\u2BFF\\U0001F000-\\U0001F1E5\\U0001F200-\\U0001FAFF]\\ufe0f?)*)")


async def react_start(uid, ev, arg):
    """.ریاکشن [ایموجی‌ها] روی پیام کاربر ریپلای کن؛ همون پیام ریاکشن می‌خوره.
    اکانت پریمیوم تا ۳ ریاکشن هم‌زمان، اکانت عادی ۱ تا. مثل: .ریاکشن 🔥❤👍  یا  /reykt 🔥❤👍"""
    from telethon import errors
    from telethon.tl.functions.messages import SendReactionRequest
    from telethon.tl.types import ReactionEmoji
    client = _clients.get(uid)
    if not client:
        return "❌ اول اکانتت رو وصل کن (/self)."
    if not ev.is_reply:
        return "↩️ روی پیام اون کاربر ریپلای کن و بنویس:\n.ریاکشن 🔥❤👍   یا   /reykt"
    try:
        me = await client.get_me()
        limit = 3 if getattr(me, "premium", False) else 1
        emos = []
        for e in _EMO_RE.findall(arg or ""):
            e = e.replace("\ufe0f", "")
            if e and e not in emos:
                emos.append(e)
        emos = (emos or REACT_DEFAULT)[:limit]
        await client(SendReactionRequest(peer=ev.chat_id, msg_id=ev.reply_to_msg_id,
                                         reaction=[ReactionEmoji(emoticon=e) for e in emos]))
    except errors.ReactionInvalidError:
        return "❌ یکی از ایموجی‌ها به‌عنوان ریاکشن مجاز نیست."
    except errors.FloodWaitError as e:
        return f"⏳ تلگرام محدود کرد؛ {e.seconds} ثانیه صبر کن."
    except Exception as e:  # noqa: BLE001
        logger.info("self react %s: %s", uid, e)
        return "⚠️ ارسال نشد: " + str(e)[:120]
    try:
        await ev.delete()      # پیام دستور پاک می‌شه
    except Exception:  # noqa: BLE001
        pass
    return None


def _u16(t):
    return len(t.encode("utf-16-le")) // 2


async def emoji_send(uid, ev, arg):
    """ایموجی پریمیوم مستقیم از خود اکانت (بدون ربات واسطه).
    دو حالت: ۱) ایموجی پریمیوم رو از کیبوردت توی متن بذار  ۲) {آیدی عددی ایموجی} بنویس.
    اکانت باید پریمیوم باشه."""
    import copy
    from telethon.tl.types import MessageEntityCustomEmoji
    client = _clients.get(uid)
    if not client:
        return "❌ اول اکانتت رو وصل کن (/self)."
    if not arg:
        return "✨ مثل: .ایموجی سلام 😎(ایموجی پریمیوم)\nیا با آیدی: .ایموجی سلام {5368324170671202286}"
    try:
        me = await client.get_me()
        if not getattr(me, "premium", False):
            return "❌ اکانتی که وصل کردی پریمیوم نیست؛ ایموجی پریمیوم فقط از اکانت پریمیوم ارسال می‌شه."
        raw = ev.raw_text or ""
        end = len(raw.rstrip())
        start = end - len(arg)
        start16 = _u16(raw[:start])
        ents = []
        for e in (ev.message.entities or []):
            if e.offset >= start16:
                e2 = copy.copy(e)
                e2.offset -= start16
                ents.append(e2)
        text = arg
        # جایگزینی {آیدی} با ایموجی پریمیوم
        while True:
            m = re.search(r"\{(\d{6,})\}", text)
            if not m:
                break
            pos16 = _u16(text[:m.start()])
            old16 = _u16(m.group(0))
            for e in ents:
                if e.offset >= pos16 + old16:
                    e.offset -= old16 - 1
            ents.append(MessageEntityCustomEmoji(offset=pos16, length=1, document_id=int(m.group(1))))
            text = text[:m.start()] + "⭐" + text[m.end():]
        if not any(isinstance(e, MessageEntityCustomEmoji) for e in ents):
            return "❌ توی متن ایموجی پریمیوم پیدا نشد؛ از کیبوردت بذار یا {آیدی} بنویس."
        ents.sort(key=lambda e: e.offset)
        await client.send_message(ev.chat_id, text, formatting_entities=ents, parse_mode=None,
                                  reply_to=ev.reply_to_msg_id if ev.is_reply else None)
    except Exception as e:  # noqa: BLE001
        logger.info("self emoji %s: %s", uid, e)
        return "⚠️ ارسال نشد: " + str(e)[:120]
    try:
        await ev.delete()      # پیام دستور پاک می‌شه و فقط پیام ایموجی می‌مونه
    except Exception:
        pass
    return None


# ---------- دستورهای نقطه‌ای داخل چت (.پنل / .کلمه / .زمان / .دکمه / .گپ) ----------
# کاربرِ فعال‌شده از خود اکانتش (هر چتی؛ بهترین جا: Saved Messages) این دستورها رو می‌نویسه.
#   .پنل            → پنل تنظیمات بالا میاد
#   .کلمه فلان      → کلمه‌ی اسلات فعلی (اگه عدد اسلات بذاری: .کلمه 2 فلان)
#   .زمان فلان      → تایمر: 5 یا 10s یا 2 دقیقه
#   .دکمه فلان      → چپ | وسط | راست | خالی
#   .گپ             → همین گپی که توش می‌نویسی؛ یا .گپ اسم گروه
# بعد از تنظیم (کلمه + گپ + دکمه) سلف خودش کار می‌کنه.
_CMD = {"پنل": "panel", "panel": "panel", "منو": "panel",
        "ترجمه": "translate", "translate": "translate",
        "تگ": "tag", "tag": "tag", "ریاکشن": "react", "واکنش": "react",
        "react": "react", "reykt": "react", "reaction": "react",
        "ذخیره": "save", "save": "save",
        "ایموجی": "emoji", "emoji": "emoji", "ساعت": "clock", "clock": "clock",
        "کپی": "copy", "copy": "copy", "غیرفعال": "off", "غیر‌فعال": "off", "off": "off",
        # --- ابزارها و سکوت/بلاک (پیاده‌سازی در extras.py) ---
        "سکوت": "mute", "mute": "mute", "رفعسکوت": "unmute", "unmute": "unmute",
        "بلاک": "block", "block": "block", "آنبلاک": "unblock", "unblock": "unblock", "رفع": "undo",
        "حساب": "calc", "محاسبه": "calc", "ماشینحساب": "calc", "calc": "calc",
        "قیمت": "price", "price": "price", "تاریخ": "date", "date": "date",
        "کوتاه": "short", "کوتاهکن": "short", "short": "short", "shorten": "short",
        "استوری": "story", "story": "story"}
_POSW = {"چپ": "l", "left": "l", "l": "l", "وسط": "m", "میانه": "m", "mid": "m", "middle": "m", "m": "m",
         "راست": "r", "right": "r", "r": "r", "خالی": "n", "هیچ": "n", "none": "n", "n": "n"}
_AR2FA = str.maketrans("يك", "یک")
_cur = {}          # uid -> اسلاتی که آخر دست زدیم (دستورهای بدون شماره روی همین اعمال می‌شن)
_cmd_hist = {}


def parse_dot(raw):
    """'.کلمه سلام' → ('word', 'سلام'). اگه دستور شناخته‌شده نبود None."""
    t = (raw or "").strip().translate(_AR2FA)
    if not (t.startswith(".") or t.startswith("/")):
        return None
    m = re.match(r"^[./]\s*(\S+)\s*(.*)$", t, re.S)
    if not m:
        return None
    cmd = _CMD.get(m.group(1).replace("\u200c", "").lower())
    if cmd and t.startswith("/") and cmd != "react":
        return None      # با / فقط دستور ریاکشن کار می‌کنه
    return (cmd, m.group(2).strip()) if cmd else None


def split_slot(arg):
    """'2 سلام' → (1, 'سلام'). بدون عدد اسلات → (None, arg)."""
    m = re.match(r"^([1-3\u06f1-\u06f3])\s+(\S.*)$", arg or "", re.S)
    if m:
        return int(fa2en(m.group(1))) - 1, m.group(2).strip()
    return None, (arg or "").strip()


def _slot_line(i, s):
    if not s or not s.get("word"):
        return f"{i + 1}️⃣ ➕ خالی"
    parts = [f"{i + 1}️⃣ «{s['word']}»", f"💬 {(s.get('title') or 'گپ؟')[:22]}",
             (f"🔁 هر {fmt_delay(s.get('delay', 0))}" if int(s.get('delay', 0) or 0) >= MIN_AUTO else "🔁 خاموش"),
             f"🎯 {POS[s.get('pos', 'n')]}"]
    return " | ".join(parts) + ("  ✅" if s.get("chat") else "  ⚠️ گپ تنظیم نشده")


def _missing(s):
    miss = []
    if not s.get("word"):
        miss.append(".کلمه فلان")
    if not s.get("chat"):
        miss.append(".گپ")
    if int(s.get("delay", 0) or 0) < MIN_AUTO:
        miss.append(".زمان 30 (هر چند ثانیه خودکار بگه؛ حداقل 5)")
    return miss


def _ready_text(s):
    miss = _missing(s)
    if not miss:
        tail = (f" و بعدش دکمه‌ی {POS[s.get('pos')]} رو می‌زنه." if s.get("pos", "n") != "n"
                else " (دکمه‌ای زده نمی‌شه؛ اگه می‌خوای: .دکمه چپ|وسط|راست).")
        return (f"🚀 فعاله! هر {fmt_delay(s.get('delay', 0))} خودش «{s['word']}» رو توی «{s.get('title') or 'گپ'}» می‌گه" + tail)
    return "📌 مونده: " + " | ".join(miss)


def _panel_text(uid):
    row = get_row(uid)
    sl = slots_of(row) if row else []
    on = uid in _clients
    lines = ["🤖 سلف روبی", "",
             f"🔌 اتصال: {'✅ وصل' if on else '❌ قطع'}   ⚙️ وضعیت: {'⏸ متوقف' if row and row.paused else '▶️ فعال'}", ""]
    for i in range(MAX_SLOTS):
        lines.append(_slot_line(i, sl[i] if i < len(sl) else {}))
    return "\n".join(lines)


async def _del_later(m, sec):
    try:
        await asyncio.sleep(sec)
        await m.delete()
    except Exception:
        pass


async def _say(uid, ev, text, keep=False):
    """جواب رو همون‌جا (با ادیت پیام دستور) می‌ده و بعد چند ثانیه پاکش می‌کنه."""
    try:
        m = await ev.edit(text, parse_mode=None)
    except Exception:
        try:
            await _botref[0].send_message(uid, text)
        except Exception:
            pass
        return
    if not keep:
        asyncio.create_task(_del_later(m, 10))


def _pick_slot(uid, sl, explicit, word=None):
    if explicit is not None:
        return explicit
    if word:
        w = norm(word)
        for i, s in enumerate(sl):
            if s.get("word") and norm(s["word"]) == w:
                return i
        for i, s in enumerate(sl):
            if not s.get("word"):
                return i
    return min(max(_cur.get(uid, 0), 0), MAX_SLOTS - 1)


async def _find_chat(client, name):
    q = norm(name)
    ds = await _dialogs(client)
    exact = [d for d in ds if norm(d[1]) == q]
    if len(exact) == 1:
        return [exact[0]]
    return [d for d in ds if q in norm(d[1])]


async def _handle_dot(uid, ev):
    raw = ev.raw_text or ""
    if raw.strip() == "پنل":
        raw = ".پنل"
    if not raw.lstrip().startswith((".", "/")):
        return False
    pc = parse_dot(raw)
    if not pc:
        return False
    cmd, arg = pc
    if cmd == "translate":
        if not ev.is_reply:
            await _say(uid, ev, "روی متن موردنظر ریپلای کن و بعد .ترجمه را بفرست.")
            return True
        try:
            reply = await ev.get_reply_message()
            source = (reply.raw_text or "").strip()
            if not source:
                await _say(uid, ev, "پیام ریپلای‌شده متن قابل ترجمه ندارد.")
            else:
                result = await _translate_text(source)
                await ev.reply("ترجمه:\n" + result, parse_mode=None)
                try: await ev.delete()
                except Exception: pass
        except Exception as e:
            await _say(uid, ev, "ترجمه انجام نشد؛ اتصال اینترنت سرور را بررسی کن. " + str(e)[:80])
        return True
    if cmd == "tag" and norm(arg) in ("اعضا", "members", "members all", "") :
        try:
            if not ev.is_group and not ev.is_channel:
                await _say(uid, ev, "این دستور فقط در گروه قابل استفاده است.")
                return True
            from telethon.tl.types import User
            mentions = []
            async for participant in _clients[uid].iter_participants(ev.chat_id, limit=10):
                if isinstance(participant, User) and not getattr(participant, "bot", False):
                    label = (participant.first_name or "عضو").replace("[", "").replace("]", "")
                    mentions.append(f"[{label}](tg://user?id={participant.id})")
            if mentions:
                await ev.reply("اعضای گروه (حداکثر ۱۰ نفر):\n" + " ".join(mentions), parse_mode="md")
            else:
                await _say(uid, ev, "عضوی برای منشن در دسترس نبود.")
            try: await ev.delete()
            except Exception: pass
        except Exception as e:
            await _say(uid, ev, "تگ اعضا انجام نشد؛ ممکن است تلگرام دسترسی اعضا را محدود کرده باشد. " + str(e)[:70])
        return True
    if cmd == "save":
        import extras
        await _say(uid, ev, await extras.save_replied(uid, ev))
        return True
    import extras
    if cmd in extras.CMD_WORDS:
        await extras.handle_cmd(uid, ev, cmd, arg)
        return True
    # تنظیمات سلف (کلمه/زمان/گپ/دکمه/ساعت) فقط از دکمه‌های .پنل انجام می‌شوند؛
    # دستورهای ریپلایی و سریع (.کپی، .غیرفعال کپی، .ریاکشن، .ایموجی، .ساعت) توی چت کار می‌کنن.
    if cmd not in ("panel", "copy", "off", "react", "emoji", "clock"):
        try:
            await ev.delete()
        except Exception:
            pass
        return True
    now = time.time()
    h = [t for t in _cmd_hist.get(uid, []) if now - t < 60]
    if len(h) >= 40:
        return True
    h.append(now)
    _cmd_hist[uid] = h
    try:
        if cmd == "panel":
            if await send_inline_panel(uid, ev):
                return True
            if ev.chat_id == uid:      # Saved Messages: پنل همون‌جا بالا میاد
                await ev.edit(_panel_text(uid), parse_mode=None)
            else:                      # گپ عمومی: پیام دستور پاک می‌شه و پنل می‌ره توی چت ربات
                try:
                    await ev.delete()
                except Exception:
                    pass
                await send_panel(_botref[0], uid)
            return True
        if cmd == "off":
            what = norm(re.sub(r"^فعال\s*", "", arg or ""))
            if what in ("کپی", "copy"):
                await _say(uid, ev, await copy_off(uid))
            elif what in ("ساعت", "clock"):
                await _say(uid, ev, await clock_set(uid, on=False))
            else:
                await _say(uid, ev, "❓ مثل: .غیرفعال کپی   یا   .غیرفعال ساعت")
            return True
        if cmd == "react":
            msg_out = await react_start(uid, ev, arg)
            if msg_out:
                await _say(uid, ev, msg_out)
            return True
        if cmd == "emoji":
            msg_out = await emoji_send(uid, ev, arg)
            if msg_out:
                await _say(uid, ev, msg_out)
            return True
        if cmd == "copy":
            if norm(arg) in ("خاموش", "off", "غیرفعال"):
                await _say(uid, ev, await copy_off(uid))
            else:
                await _say(uid, ev, await copy_on(uid, ev))
            return True
        if cmd == "clock":
            a = norm(arg)
            if a in ("خاموش", "off", "غیرفعال"):
                await _say(uid, ev, await clock_set(uid, on=False))
            elif a in ("فونت", "فونت ها", "فونتها", "fonts", "font", "لیست"):
                await _say(uid, ev, clock_fonts_text(uid), keep=True)
            elif fa2en(a).isdigit():
                n = int(fa2en(a))
                if 1 <= n <= len(CLOCK_FONTS):
                    await _say(uid, ev, await clock_set(uid, on=True, font=n))
                else:
                    await _say(uid, ev, clock_fonts_text(uid), keep=True)
            elif a == "":
                d = _pget(uid)
                if d.get("clock_on"):
                    await _say(uid, ev, clock_fonts_text(uid), keep=True)
                else:
                    await _say(uid, ev, await clock_set(uid, on=True))
            else:
                await _say(uid, ev, "🕒 مثل: .ساعت   |   .ساعت 3   |   .ساعت فونت   |   .غیرفعال ساعت")
            return True
        explicit, rest = split_slot(arg)
        sl = _get_slots(uid)
        if cmd == "word":
            if not rest:
                await _say(uid, ev, "✏️ مثل: .کلمه سلام   (یا اسلات مشخص: .کلمه 2 سلام)")
                return True
            if is_banned_word(rest):
                await _say(uid, ev, "⛔ این کلمه مجاز نیست.")
                return True
            i = _pick_slot(uid, sl, explicit, rest)
            sl[i]["word"] = rest[:40]
            sl[i].setdefault("delay", 0)
            _put_slots(uid, sl)
            _cur[uid] = i
            await _say(uid, ev, f"✅ کلمه‌ی اسلات {i + 1}: «{sl[i]['word']}»\n" + _ready_text(sl[i]))
        elif cmd == "time":
            d = parse_delay(rest)
            if d is None or 0 < d < MIN_AUTO:
                await _say(uid, ev, "⏱ هر چند ثانیه خودکار بگه؟ مثل: .زمان 30   یا   .زمان 2 دقیقه\n(حداقل 5 ثانیه، حداکثر ۶۰ دقیقه؛ .زمان 0 = خودکار خاموش)")
                return True
            i = _pick_slot(uid, sl, explicit)
            sl[i]["delay"] = d
            _put_slots(uid, sl)
            _cur[uid] = i
            await _say(uid, ev, (f"✅ زمان اسلات {i + 1}: هر {fmt_delay(d)}\n" if d else f"✅ حالت خودکار اسلات {i + 1} خاموش شد.\n") + _ready_text(sl[i]))
        elif cmd == "btn":
            pos = _POSW.get(norm(rest))
            if not pos:
                await _say(uid, ev, "🎯 مثل: .دکمه چپ   یا   .دکمه وسط   یا   .دکمه راست   یا   .دکمه خالی")
                return True
            i = _pick_slot(uid, sl, explicit)
            sl[i]["pos"] = pos
            _put_slots(uid, sl)
            _cur[uid] = i
            await _say(uid, ev, f"✅ دکمه‌ی اسلات {i + 1}: {POS[pos]}\n" + _ready_text(sl[i]))
        elif cmd == "chat":
            client = _clients.get(uid)
            slot_only = re.fullmatch(r"[1-3\u06f1-\u06f3]", arg or "")
            if slot_only:
                explicit, rest = int(fa2en(arg)) - 1, ""
            if not rest:      # همین گپ
                chat = await ev.get_chat()
                is_bot = bool(ev.is_private and getattr(chat, "bot", False))
                if ev.chat_id in (uid, _bot_id[0]) or not (ev.is_group or is_bot):
                    await _say(uid, ev, "💬 این دستور رو داخل گپِ هدف (گروه یا چت ربات) بنویس، یا بنویس: .گپ اسم گروه")
                    return True
                cid = ev.chat_id
                title = getattr(chat, "title", None) or getattr(chat, "first_name", None) or str(cid)
            else:
                found = await _find_chat(client, rest) if client else []
                if not found:
                    await _say(uid, ev, f"❌ گپی با اسم «{rest[:30]}» پیدا نشد (فقط گروه‌ها و چت رباتا).")
                    return True
                if len(found) > 1:
                    names = "\n".join(f"• {d[1][:30]}" for d in found[:6])
                    await _say(uid, ev, "🔎 چند تا پیدا شد؛ اسم دقیق‌تر بنویس:\n" + names)
                    return True
                cid, title = found[0][0], found[0][1]
            i = _pick_slot(uid, sl, explicit)
            sl[i]["chat"] = cid
            sl[i]["title"] = str(title)[:40]
            _put_slots(uid, sl)
            _cur[uid] = i
            await _say(uid, ev, f"✅ گپ اسلات {i + 1}: «{sl[i]['title']}»\n" + _ready_text(sl[i]))
    except Exception as e:  # noqa: BLE001
        logger.info("self dot-cmd failed %s: %s", uid, e)
    return True


# ---------- آنلاین‌بودن و بازتاب تایپینگ ----------
def _sync_presence(uid):
    """شروع/توقف تازه‌سازی وضعیت آنلاین و تایپینگ بر اساس تنظیمات ذخیره‌شده."""
    client = _clients.get(uid)
    if not client:
        return
    tasks = _feature_tasks.setdefault(uid, [])
    # پاک‌کردن تسک‌های قبلی این قابلیت‌ها؛ تسک‌های ساعت/اسلات جدا هستند.
    for task in tasks:
        task.cancel()
    tasks.clear()
    d = _pget(uid)
    if d.get("always_online"):
        async def online_loop():
            from telethon.tl.functions.account import UpdateStatusRequest
            while uid in _clients and _pget(uid).get("always_online"):
                try:
                    await client(UpdateStatusRequest(offline=False))
                except asyncio.CancelledError:
                    raise
                except Except
