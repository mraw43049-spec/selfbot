# -*- coding: utf-8 -*-
"""امکانات بیشتر سلف: پاسخ خودکار، پیام‌های حذف/ویرایش‌شده، ذخیره مدیای تایمردار،
منشن‌گیر و ناظر کلمه/کاربر. تنظیمات هر کاربر داخل همان JSON پروفایل (sb._pget/_pput) ذخیره می‌شود."""
import asyncio
import io
import logging
import random
import time
from collections import OrderedDict

import selfbot as sb

logger = logging.getLogger("selfextras")

DEFAULT_AR = "سلام! الان آنلاین نیستم؛ هر وقت برگشتم جواب می‌دم 🙏"
AR_COOLDOWN = 6 * 3600        # به هر نفر حداکثر هر ۶ ساعت یک‌بار
AR_MAX_PER_HOUR = 20          # سقف کل پاسخ‌های خودکار در ساعت (برای ایمنی اکانت)
CACHE_MAX = 1500
CACHE_TTL = 48 * 3600
WL_MAX = 10
NOTIFY_PER_MIN = 20
WL_COOLDOWN = 20

TOGGLES = ("ar_on", "del_on", "ttl_on", "mn_on", "wl_on")
CB_ACTIONS = ("more", "mt", "ar_text", "wl", "wl_add", "wl_del")

_cache = {}      # uid -> OrderedDict[msg_id -> rec]
_ar_seen = {}    # uid -> {sender_id: ts}
_ar_hist = {}    # uid -> [ts]
_notif_hist = {}  # uid -> [ts]
_wl_seen = {}    # (uid, chat, item) -> ts
_mn_seen = {}    # (uid, chat, msg_id) -> ts


# ---------- کمکی‌ها ----------
def _name(e):
    if e is None:
        return "ناشناس"
    n = " ".join(x for x in (getattr(e, "first_name", None), getattr(e, "last_name", None)) if x)
    return n or getattr(e, "title", None) or (("@" + e.username) if getattr(e, "username", None) else str(getattr(e, "id", "؟")))


def _link(chat, mid):
    try:
        from telethon.tl.types import Channel
        un = getattr(chat, "username", None)
        if un:
            return f"https://t.me/{un}/{mid}"
        if isinstance(chat, Channel):
            return f"https://t.me/c/{chat.id}/{mid}"
    except Exception:  # noqa: BLE001
        pass
    return ""


def _kind(msg):
    for attr, label in (("photo", "عکس"), ("video", "ویدیو"), ("voice", "ویس"), ("video_note", "ویدیو مسیج"),
                        ("sticker", "استیکر"), ("gif", "گیف"), ("audio", "آهنگ"), ("document", "فایل")):
        if getattr(msg, attr, None):
            return label
    return "رسانه" if getattr(msg, "media", None) else ""


async def _notify(uid, text):
    """اعلان توی چت ربات؛ سقف تعداد در دقیقه برای جلوگیری از اسپم."""
    bot = sb._botref[0]
    if not bot:
        return
    now = time.time()
    h = [t for t in _notif_hist.get(uid, []) if now - t < 60]
    if len(h) >= NOTIFY_PER_MIN:
        return
    h.append(now)
    _notif_hist[uid] = h
    try:
        await bot.send_message(uid, text[:4000], disable_web_page_preview=True)
    except Exception as e:  # noqa: BLE001
        logger.info("extras notify %s: %s", uid, e)


def cleanup(uid):
    _cache.pop(uid, None)
    _ar_seen.pop(uid, None)
    _ar_hist.pop(uid, None)
    _notif_hist.pop(uid, None)


def match_watch(items, text, sender_id, username):
    """آیتم‌های ناظر که با این پیام جور هستن (کلمه / @یوزرنیم / آیدی عددی)."""
    low = (text or "").lower()
    un = (username or "").lower()
    out = []
    for it in items or []:
        k = it.lower()
        if k.startswith("@"):
            if un and k[1:] == un:
                out.append(it)
        elif k.isdigit():
            if sender_id is not None and int(k) == int(sender_id):
                out.append(it)
        elif k and k in low:
            out.append(it)
    return out


# ---------- اتصال هندلرها ----------
def attach(uid, client):
    from telethon import events

    async def _in(ev):
        await _safe(on_incoming(uid, client, ev))

    async def _ed(ev):
        await _safe(on_edited(uid, ev))

    async def _del(ev):
        await _safe(on_deleted(uid, ev))

    client.add_event_handler(_in, events.NewMessage(incoming=True))
    client.add_event_handler(_ed, events.MessageEdited(incoming=True))
    client.add_event_handler(_del, events.MessageDeleted())


async def _safe(coro):
    try:
        await coro
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.info("extras handler: %s", e)


# ---------- پیام ورودی ----------
async def on_incoming(uid, client, ev):
    d = sb._pget(uid)
    if not any(d.get(k) for k in TOGGLES):
        return
    msg = ev.message
    if msg is None:
        return
    try:
        snd = await ev.get_sender()
    except Exception:  # noqa: BLE001
        snd = None
    sid = getattr(snd, "id", None)
    if sid is not None and sid == sb._bot_id[0]:
        return
    text = msg.raw_text or ""

    # ۲) حافظهٔ پیام‌ها برای حذف/ویرایش (فقط چت‌های خصوصی)
    if d.get("del_on") and ev.is_private:
        c = _cache.setdefault(uid, OrderedDict())
        c[msg.id] = {"chat": ev.chat_id, "name": _name(snd), "text": text, "media": _kind(msg), "ts": time.time()}
        while len(c) > CACHE_MAX:
            c.popitem(last=False)

    # ۳) ذخیرهٔ خودکار مدیای تایمردار
    if d.get("ttl_on") and getattr(getattr(msg, "media", None), "ttl_seconds", None):
        ok = await _save_media(client, msg, "me", f"⏱ مدیای تایمردار از {_name(snd)}" + (f"\n{text}" if text else ""))
        if not ok:
            await _notify(uid, f"⚠️ مدیای تایمردار از {_name(snd)} رسید ولی ذخیره نشد.")

    # ۱) پاسخ خودکار
    if d.get("ar_on") and ev.is_private and snd is not None and not getattr(snd, "bot", False) and sid != 777000:
        now = time.time()
        seen = _ar_seen.setdefault(uid, {})
        if now - seen.get(sid, 0) >= AR_COOLDOWN:
            hist = [t for t in _ar_hist.get(uid, []) if now - t < 3600]
            if len(hist) < AR_MAX_PER_HOUR:
                seen[sid] = now
                hist.append(now)
                _ar_hist[uid] = hist
                if len(seen) > 5000:
                    for k in sorted(seen, key=seen.get)[:1000]:
                        seen.pop(k, None)
                await asyncio.sleep(random.uniform(2, 5))
                await client.send_message(ev.chat_id, (d.get("ar_text") or DEFAULT_AR)[:500])

    # ۴) منشن‌گیر
    if d.get("mn_on") and ev.is_group and getattr(msg, "mentioned", False):
        key = (uid, ev.chat_id, msg.id)
        if key not in _mn_seen:
            _mn_seen[key] = time.time()
            if len(_mn_seen) > 3000:
                _mn_seen.clear()
            try:
                chat = await ev.get_chat()
            except Exception:  # noqa: BLE001
                chat = None
            link = _link(chat, msg.id)
            await _notify(uid, f"📣 تگ شدی\n👥 گروه: {_name(chat)}\n👤 از: {_name(snd)}\n💬 {text[:300] or '[' + (_kind(msg) or 'بدون متن') + ']'}"
                          + (f"\n🔗 {link}" if link else ""))

    # ۹) ناظر
    items = d.get("wl_items") or []
    if d.get("wl_on") and items:
        hits = match_watch(items, text, sid, getattr(snd, "username", None))
        if hits:
            now = time.time()
            fresh = [h for h in hits if now - _wl_seen.get((uid, ev.chat_id, h), 0) >= WL_COOLDOWN]
            for h in fresh:
                _wl_seen[(uid, ev.chat_id, h)] = now
            if len(_wl_seen) > 3000:
                _wl_seen.clear()
            if fresh:
                try:
                    chat = await ev.get_chat()
                except Exception:  # noqa: BLE001
                    chat = None
                link = _link(chat, msg.id)
                await _notify(uid, f"👁 ناظر: {'، '.join(fresh)}\n💬 چت: {_name(chat)}\n👤 از: {_name(snd)}\n📝 {text[:300] or '[' + (_kind(msg) or 'بدون متن') + ']'}"
                              + (f"\n🔗 {link}" if link else ""))


# ---------- ویرایش و حذف ----------
async def on_edited(uid, ev):
    if not sb._pget(uid).get("del_on") or not ev.is_private:
        return
    c = _cache.get(uid)
    rec = c.get(ev.message.id) if c else None
    new = ev.message.raw_text or ""
    if rec and rec["text"] != new:
        old = rec["text"]
        rec["text"] = new
        await _notify(uid, f"✏️ پیام ویرایش شد\n👤 از: {rec['name']}\n⬅️ قبل: {old[:500] or '[بدون متن]'}\n➡️ بعد: {new[:500] or '[بدون متن]'}")


async def on_deleted(uid, ev):
    if not sb._pget(uid).get("del_on"):
        return
    c = _cache.get(uid)
    if not c:
        return
    now = time.time()
    out = []
    for mid in ev.deleted_ids or []:
        rec = c.get(mid)
        if not rec:
            continue
        if ev.chat_id and rec["chat"] != ev.chat_id:
            continue
        c.pop(mid, None)
        if now - rec["ts"] <= CACHE_TTL:
            out.append(rec)
    for rec in out[:10]:
        body = rec["text"] or (f"[{rec['media']}]" if rec["media"] else "[بدون متن]")
        await _notify(uid, f"🗑 پیام حذف شد\n👤 از: {rec['name']}\n💬 {body[:800]}" + (f"\n📎 همراه با {rec['media']}" if rec["media"] and rec["text"] else ""))
    if len(out) > 10:
        await _notify(uid, f"🗑 و {len(out) - 10} پیام حذف‌شدهٔ دیگر.")


# ---------- ذخیرهٔ مدیا ----------
async def _save_media(client, msg, dest, caption):
    try:
        data = await client.download_media(msg, file=bytes)
        if not data:
            return False
        ext = getattr(getattr(msg, "file", None), "ext", None) or ".bin"
        bio = io.BytesIO(data)
        bio.name = "media" + ext
        await client.send_file(dest, bio, caption=caption[:1000])
        return True
    except Exception as e:  # noqa: BLE001
        logger.info("extras save media: %s", e)
        return False


async def save_replied(uid, ev):
    """دستور .ذخیره (ریپلای روی مدیا) → مدیا به Saved Messages می‌رود."""
    client = sb._clients.get(uid)
    if not client or not ev.is_reply:
        return "روی مدیای موردنظر ریپلای کن و بنویس .ذخیره"
    r = await ev.get_reply_message()
    if not r or not getattr(r, "media", None):
        return "پیام ریپلای‌شده مدیا ندارد."
    ok = await _save_media(client, r, "me", "💾 ذخیره‌شده با .ذخیره")
    return "✅ توی Saved Messages ذخیره شد." if ok else "❌ ذخیره نشد؛ احتمالاً مدیا قبلاً منقضی یا باز شده."


# ---------- پنل ----------
def _st(d, k):
    return "روشن" if d.get(k) else "خاموش"


def more_panel(uid):
    d = sb._pget(uid)
    wl = d.get("wl_items") or []
    B, KB = sb.B, sb.KB
    note = "" if uid in sb._clients else "⚠️ اکانت وصل نیست؛ این امکانات بعد از اتصال کار می‌کنن.\n\n"
    tx = (note + "› امکانات بیشتر\n\n"
          f"🤖 پاسخ خودکار: {_st(d, 'ar_on')} — به پی‌وی‌ها (هر نفر حداکثر هر ۶ ساعت یک‌بار) جواب می‌ده\n"
          f"🗑 حذف/ویرایش: {_st(d, 'del_on')} — پیام پاک‌شده یا ویرایش‌شدهٔ پی‌وی‌ها رو برات می‌فرسته\n"
          f"⏱ مدیای تایمردار: {_st(d, 'ttl_on')} — عکس/ویدیوی یک‌بارمصرف خودکار توی Saved Messages ذخیره می‌شه (دستی: ریپلای + .ذخیره)\n"
          f"📣 منشن‌گیر: {_st(d, 'mn_on')} — هر تگ توی گروه‌ها با لینک پیام خبرت می‌کنه\n"
          f"👁 ناظر: {_st(d, 'wl_on')} — {len(wl)} مورد (کلمه، @یوزرنیم یا آیدی عددی)")
    tx = tx[:1000]

    def t(k, label):
        return B(f"{label}: {_st(d, k)}", callback_data=f"self:mt:{k}", style="success" if d.get(k) else "primary")

    kb = KB([
        [t("ar_on", "پاسخ خودکار"), B("متن پاسخ", callback_data="self:ar_text", style="primary")],
        [t("del_on", "حذف/ویرایش"), t("ttl_on", "مدیای تایمردار")],
        [t("mn_on", "منشن‌گیر"), t("wl_on", "ناظر")],
        [B("مدیریت ناظر", callback_data="self:wl", style="primary")],
        [B("بازگشت", callback_data="self:home", style="primary")],
    ])
    return tx, kb


def wl_panel(uid):
    d = sb._pget(uid)
    items = d.get("wl_items") or []
    B, KB = sb.B, sb.KB
    tx = "› مدیریت ناظر\n\n" + (
        "موردی ثبت نشده.\nکلمه، @یوزرنیم یا آیدی عددی اضافه کن؛ هر وقت توی هر چتی دیده بشه خبرت می‌کنم."
        if not items else "برای حذف، روی هر مورد بزن:") + f"\n(حداکثر {WL_MAX} مورد)"
    rows = [[B(f"🗑 {it[:40]}", callback_data=f"self:wl_del:{i}", style="danger")] for i, it in enumerate(items)]
    if len(items) < WL_MAX:
        rows.append([B("افزودن مورد", callback_data="self:wl_add", style="success")])
    rows.append([B("بازگشت", callback_data="self:more", style="primary")])
    return tx, KB(rows)


async def handle_cb(uid, a, p, qm, context):
    ud = context.user_data
    if a == "more":
        tx, kb = more_panel(uid)
        await qm.edit_text(tx, reply_markup=kb)
    elif a == "mt":
        key = p[2] if len(p) > 2 else ""
        if key in TOGGLES:
            d = sb._pget(uid)
            d[key] = not bool(d.get(key))
            if key == "ar_on" and d[key] and not d.get("ar_text"):
                d["ar_text"] = DEFAULT_AR
            sb._pput(uid, d)
        tx, kb = more_panel(uid)
        await qm.edit_text(tx, reply_markup=kb)
    elif a == "ar_text":
        ud["self_st"] = {"step": "ar_text"}
        cur = sb._pget(uid).get("ar_text") or DEFAULT_AR
        await context.bot.send_message(uid, f"متن فعلی:\n{cur}\n\nمتن جدید پاسخ خودکار رو بفرست (حداکثر ۵۰۰ حرف). برای لغو بنویس «لغو».")
    elif a == "wl":
        tx, kb = wl_panel(uid)
        await qm.edit_text(tx, reply_markup=kb)
    elif a == "wl_add":
        ud["self_st"] = {"step": "wl_add"}
        await context.bot.send_message(uid, "کلمه، @یوزرنیم یا آیدی عددی رو بفرست (چندتا رو با خط جدید یا کاما جدا کن). برای لغو بنویس «لغو».")
    elif a == "wl_del":
        d = sb._pget(uid)
        items = d.get("wl_items") or []
        try:
            items.pop(int(p[2]))
        except Exception:  # noqa: BLE001
            pass
        d["wl_items"] = items
        sb._pput(uid, d)
        tx, kb = wl_panel(uid)
        await qm.edit_text(tx, reply_markup=kb)


async def handle_text(uid, st, text, msg, context):
    step = st.get("step")
    if text in ("لغو", "/cancel"):
        await msg.reply_text("لغو شد.")
        return
    d = sb._pget(uid)
    if step == "ar_text":
        d["ar_text"] = text[:500]
        sb._pput(uid, d)
        await msg.reply_text("✅ متن پاسخ خودکار ذخیره شد.")
    elif step == "wl_add":
        items = d.get("wl_items") or []
        raw = [x.strip() for x in text.replace("،", ",").replace("\n", ",").split(",")]
        added = 0
        for x in raw:
            x = x.strip()[:40]
            if not x or x.lower() in [i.lower() for i in items] or len(items) >= WL_MAX:
                continue
            items.append(x)
            added += 1
        d["wl_items"] = items
        if added:
            d["wl_on"] = True
        sb._pput(uid, d)
        await msg.reply_text(f"✅ {added} مورد اضافه شد." if added else "موردی اضافه نشد (تکراری، خالی یا سقف ۱۰ مورد).")
    tx, kb = more_panel(uid)
    await msg.reply_text(tx, reply_markup=kb)
