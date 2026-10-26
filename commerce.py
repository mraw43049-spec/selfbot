# -*- coding: utf-8 -*-
"""منو، عضویت اجباری، الماس، دعوت، انتقال و رسید پرداخت."""
import os
import re
import secrets
import time
from decimal import Decimal
from sqlalchemy import Column, BigInteger, String, Numeric, Integer, Boolean, inspect, text
from telegram import InlineKeyboardButton as _B, InlineKeyboardMarkup as KB
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, MessageHandler, filters
from database import Base, get_session
import selfbot

CHANNELS = [('@selfsaz_iran', 'https://t.me/selfsaz_iran'), ('@fox_frenzy', 'https://t.me/fox_frenzy')]
SUPPORT = '@escotch'
CARD = '6219 8618 5116 0068'
OWNER = 'ظریفی'
ACTIVATE_COST = 5
# دورهٔ رایگان ۳ روزه به‌صورت پیش‌فرض خاموشه (بدون الماس سلف فعال نمی‌شه). برای روشن‌کردنش: SELF_TRIAL=1
TRIAL_ENABLED = os.environ.get('SELF_TRIAL', '').strip().lower() in ('1', 'true', 'yes')

_EMOJI_RE = __import__('re').compile("[\U0001F000-\U0001FFFF\u2190-\u21FF\u2300-\u23FF\u2600-\u27BF\uFE0F\u200d]")
def B(text, *args, **kwargs):
    text = _EMOJI_RE.sub("", text or "").strip()
    api = kwargs.pop("api_kwargs", {}) or {}
    api.setdefault("style", "danger" if any(x in text for x in ("رد", "لغو", "خاموش", "حذف")) else "success" if any(x in text for x in ("تأیید", "روشن", "عضو شدم")) else "primary")
    return _B(text, *args, api_kwargs=api, **kwargs)
class Wallet(Base):
    __tablename__ = 'diamond_wallets'
    user_id = Column(BigInteger, primary_key=True)
    balance = Column(Numeric(12, 1), nullable=False, default=Decimal('0.0'))
    referrer = Column(BigInteger, nullable=True)
    referred_awarded = Column(Boolean, default=False)
    last_charge = Column(Integer, default=0)
    self_started = Column(Boolean, default=False)
    trial_until = Column(Integer, nullable=True)
    trial_used = Column(Boolean, default=False)
class Payment(Base):
    __tablename__ = 'diamond_payments'
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False)
    plan = Column(String(20), nullable=False)
    status = Column(String(20), default='pending')
    created = Column(Integer, default=lambda: int(time.time()))

class GiftCode(Base):
    __tablename__ = 'gift_codes'
    code = Column(String(32), primary_key=True)
    amount = Column(Numeric(12, 1), nullable=False)
    max_uses = Column(Integer, nullable=False, default=1)
    used = Column(Integer, nullable=False, default=0)
    expires = Column(Integer, nullable=True)
    created = Column(Integer, default=lambda: int(time.time()))
    creator = Column(BigInteger, nullable=True)
class GiftRedeem(Base):
    __tablename__ = 'gift_redeems'
    code = Column(String(32), primary_key=True)
    user_id = Column(BigInteger, primary_key=True)
    ts = Column(Integer, default=lambda: int(time.time()))

def _migrate_wallet():
    # create_all does not add columns to existing SQLite/Postgres tables.
    try:
        insp = inspect(__import__('database').engine)
        if 'diamond_wallets' not in insp.get_table_names():
            return
        cols = {c['name'] for c in insp.get_columns('diamond_wallets')}
        dialect = __import__('database').engine.dialect.name
        with __import__('database').engine.begin() as conn:
            if 'trial_until' not in cols:
                conn.execute(text('ALTER TABLE diamond_wallets ADD COLUMN trial_until INTEGER'))
            if 'trial_used' not in cols:
                conn.execute(text('ALTER TABLE diamond_wallets ADD COLUMN trial_used BOOLEAN DEFAULT FALSE'))
    except Exception:
        import logging; logging.getLogger(__name__).exception('wallet migration failed')

def start_trial(uid):
    """Start the 72-hour trial once, only after required-channel membership is verified."""
    now = int(time.time())
    s = get_session()
    try:
        w = s.get(Wallet, uid)
        if not w:
            w = Wallet(user_id=uid, balance=Decimal('0.0'), last_charge=now)
            s.add(w)
        if not w.trial_used:
            w.trial_used = True
            w.trial_until = now + 3 * 24 * 60 * 60
            s.commit()
            selfbot.save_row(uid, enabled=True)
            return w.trial_until, True
        return w.trial_until, False
    finally:
        s.close()

def wallet(uid, ref=None):
    s=get_session()
    try:
        w=s.get(Wallet,uid)
        if not w:
            w=Wallet(user_id=uid,balance=Decimal('0.0'),last_charge=int(time.time()))
            if ref and ref != uid and s.get(Wallet,ref): w.referrer=ref
            s.add(w); s.commit()
        return {'balance': Decimal(w.balance or 0), 'referrer':w.referrer,'self_started':bool(w.self_started),'last_charge':w.last_charge or int(time.time()), 'trial_until':getattr(w,'trial_until',None), 'trial_used':bool(getattr(w,'trial_used',False))}
    finally:s.close()
def change(uid, amount):
    s=get_session()
    try:
        w=s.get(Wallet,uid)
        if not w: w=Wallet(user_id=uid,balance=Decimal('0.0'),last_charge=int(time.time())); s.add(w)
        w.balance=Decimal(w.balance or 0)+Decimal(str(amount)); s.commit(); return Decimal(w.balance)
    finally:s.close()

def award_referral(uid):
    s=get_session()
    try:
        w=s.get(Wallet,uid)
        if not w or not w.referrer or w.referred_awarded: return False
        ref=s.get(Wallet,w.referrer)
        if ref:
            w.balance=Decimal(w.balance or 0)+Decimal('50.0')
            ref.balance=Decimal(ref.balance or 0)+Decimal('50.0')
            w.referred_awarded=True
            s.commit()
            return True
        return False
    finally:s.close()

def btn(text, data=None, url=None, style="primary"):
    kw = {"api_kwargs": {"style": style}}
    if url:
        return B(text, url=url, **kw)
    return B(text, callback_data=data, **kw)

def home_kb():
    return KB([
        [btn('ساخت سلف', 'shop:create', style='success')],
        [btn('خرید الماس', 'shop:buy')],
        [btn('دعوت دوستان', 'shop:invite'), btn('انتقال الماس', 'shop:transfer')],
        [btn('کد هدیه', 'shop:code', style='success')],
        [btn('آموزش استفاده', 'shop:help'), btn('درباره سلف', 'shop:about')],
        [btn('حساب کاربری', 'shop:dash')],
    ])
def join_kb():
    rows=[[B('عضویت در '+ch, url=url)] for ch,url in CHANNELS]
    rows.append([B('✅ عضو شدم',callback_data='shop:check')]); return KB(rows)
async def is_member(bot,uid):
    for ch,_ in CHANNELS:
        try:
            m=await bot.get_chat_member(ch,uid)
            if m.status in ('left','kicked'): return False
        except Exception:
            return False
    return True
_CODE_CHARS = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'

def make_gift(creator, amount, max_uses, hours=None):
    s = get_session()
    try:
        for _ in range(10):
            code = 'RUBI-' + ''.join(secrets.choice(_CODE_CHARS) for _ in range(8))
            if not s.get(GiftCode, code):
                break
        exp = int(time.time()) + int(hours) * 3600 if hours else None
        s.add(GiftCode(code=code, amount=Decimal(str(amount)), max_uses=int(max_uses), used=0, expires=exp, creator=creator))
        s.commit()
        return code
    finally:
        s.close()

def redeem_gift(uid, code):
    """('ok', amount, balance) یا (علت, None, None)."""
    code = (code or '').strip().upper()
    s = get_session()
    try:
        g = s.query(GiftCode).filter(GiftCode.code == code).with_for_update().one_or_none()
        if not g:
            return ('invalid', None, None)
        now = int(time.time())
        if g.expires and now > g.expires:
            return ('expired', None, None)
        if (g.used or 0) >= g.max_uses:
            return ('finished', None, None)
        if s.get(GiftRedeem, (code, uid)):
            return ('already', None, None)
        w = s.get(Wallet, uid)
        if not w:
            w = Wallet(user_id=uid, balance=Decimal('0.0'), last_charge=now)
            s.add(w)
        w.balance = Decimal(w.balance or 0) + Decimal(g.amount)
        g.used = (g.used or 0) + 1
        s.add(GiftRedeem(code=code, user_id=uid))
        s.commit()
        return ('ok', Decimal(g.amount), Decimal(w.balance))
    except Exception:
        s.rollback()
        return ('invalid', None, None)
    finally:
        s.close()

def fmt_bal(x):
    d = Decimal(x or 0).quantize(Decimal('0.1'))
    t = format(d, 'f')
    return t[:-2] if t.endswith('.0') else t

def welcome_text(name, balance):
    lines = [
        f'سلام {name} 👋',
        'به «سلف‌ساز روباهیو» خوش اومدی ⚡️',
        '',
        'اکانت تلگرام خودت رو تبدیل کن به یه دستیار خودکار و حرفه‌ای:',
        '🤖 بازی‌ها رو خودش انجام می‌ده؛ کلمه می‌گه، تایمر می‌ذاره و دکمه می‌زنه',
        '🕒 ساعت زنده کنار اسمت',
        '🪞 کپی پروفایل فقط با یه ریپلای',
        '🟢 همیشه آنلاین  •  ⌨️ تایپینگ  •  🔤 حالت متن  •  🌐 ترجمه  •  🏷 تگ اعضا',
        '🎛 همه‌چیز از یه پنل شیک؛ کافیه توی هر چتی بنویسی «.پنل»',
        '',
        f'💎 سلف با الماس کار می‌کنه: فعال‌سازی {ACTIVATE_COST} الماس، بعدش ساعتی ۲ الماس (روشن) یا ۰٫۲ الماس (خاموش).',
        '🔒 بدون الماس سلف فعال نمی‌شه.',
        '',
        f'💰 موجودی تو: {fmt_bal(balance)} الماس',
    ]
    if Decimal(balance or 0) < ACTIVATE_COST:
        lines.append(f'⚠️ برای فعال‌سازی حداقل {ACTIVATE_COST} الماس لازمه؛ از «خرید الماس» شارژ کن یا دوستات رو دعوت کن (هر دعوت ۵۰ الماس برای هر دو نفر).')
    else:
        lines.append('✅ الماس کافی داری؛ «ساخت سلف» رو بزن و شروع کن.')
    lines.append('')
    lines.append('💬 فرستادن «موجودی» توی هر چت، موجودیت رو نشون می‌ده.')
    return '\n'.join(lines)

async def start(update,context):
    u=update.effective_user
    if context.args and context.args[0]=='panel':
        await selfbot.cmd_self(update,context); return
    ref=None
    if context.args:
        try: ref=int(context.args[0].replace('ref',''))
        except: pass
    wallet(u.id,ref)
    if not await is_member(context.bot,u.id):
        await update.effective_message.reply_text('برای استفاده از ربات، اول عضو هر دو کانال شو و بعد «عضو شدم» رو بزن.',reply_markup=join_kb()); return
    until, trial_new = start_trial(u.id) if TRIAL_ENABLED else (None, False)
    awarded = award_referral(u.id)
    if awarded:
        wref = wallet(u.id)['referrer']
        try:
            await context.bot.send_message(u.id, 'با لینک دعوت وارد شدی؛ ۵۰ الماس پاداش گرفتی.')
            if wref:
                await context.bot.send_message(wref, f'{u.first_name} با لینک دعوت تو وارد شد؛ هر دو ۵۰ الماس گرفتید.')
        except Exception:
            pass
    trial_msg = '\n🎁 دورهٔ رایگان سلفت به مدت ۳ روز شروع شد.' if trial_new else ''
    await update.effective_message.reply_text(welcome_text(u.first_name or '', wallet(u.id)['balance']) + trial_msg, reply_markup=home_kb())
async def show_panel(q, text, **kwargs):
    try:
        await q.edit_message_text(text, **kwargs)
    except Exception:
        await q.message.reply_text(text, **kwargs)

async def callback(update,context):
    q=update.callback_query; uid=q.from_user.id; data=q.data or ''; await q.answer()
    if data=='shop:check':
        if not await is_member(context.bot,uid): await show_panel(q, 'هنوز عضویت هر دو کانال تأیید نشده؛ بعد از عضویت دوباره بزن.',reply_markup=join_kb()); return
        until, trial_new = start_trial(uid) if TRIAL_ENABLED else (None, False)
        awarded = award_referral(uid)
        if awarded:
            wref = wallet(uid)['referrer']
            try:
                await context.bot.send_message(uid, 'با لینک دعوت وارد شدی؛ ۵۰ الماس پاداش گرفتی.')
                if wref:
                    await context.bot.send_message(wref, f'{q.from_user.first_name} با لینک دعوت تو وارد شد؛ هر دو ۵۰ الماس گرفتید.')
            except Exception:
                pass
        note = '\n🎁 دورهٔ رایگان ۳ روزه شروع شد.' if trial_new else ''
        await show_panel(q, '✅ عضویتت تأیید شد!\n\n' + welcome_text(q.from_user.first_name or '', wallet(uid)['balance']) + note,reply_markup=home_kb()); return
    if not await is_member(context.bot,uid):
        await show_panel(q, 'برای ادامه باید عضو هر دو کانال باشی.',reply_markup=join_kb()); return
    w=wallet(uid)
    if data=='shop:home': await show_panel(q, 'منوی اصلی',reply_markup=home_kb())
    elif data=='shop:create':
        await show_panel(q, ('⚠️ الماس کافی نداری؛ بدون الماس سلف فعال نمی‌شه. اول از «خرید الماس» شارژ کن.\n\n' if w['balance'] < ACTIVATE_COST and not (w.get('trial_until') and int(w['trial_until']) > int(time.time())) else '') + f'💎 موجودی فعلی: {w["balance"]}\nهر بار روشن‌کردن: ۵ الماس\nهزینه‌ی ساعتی در حالت روشن: ۲ الماس؛ در حالت خاموش: ۰٫۲ الماس.',reply_markup=KB([[B('▶️ روشن کردن',callback_data='shop:on')],[B('⏹ خاموش کردن',callback_data='shop:off')],[B('👤 حساب کاربری',callback_data='shop:dash')],[B('منوی اصلی',callback_data='shop:home')]]))
    elif data=='shop:on':
        if w['self_started']:
            await show_panel(q, 'سلفت از قبل روشنه. برای دیدن تنظیمات، دکمه‌ی اتصال اکانت یا دستور «پنل» رو استفاده کن.',reply_markup=KB([[B('🔗 اتصال اکانت',callback_data='self:conn')],[B('⏹ خاموش کردن',callback_data='shop:off')],[B('منوی اصلی',callback_data='shop:home')]])); return
        trial_active = bool(w.get('trial_until') and int(w['trial_until']) > int(time.time()))
        if w['balance'] < ACTIVATE_COST and not trial_active:
            await show_panel(q, f'❌ الماس کافی نداری.\n\n💎 موجودی تو: {fmt_bal(w["balance"])} الماس\n🔒 برای فعال‌سازی سلف حداقل {ACTIVATE_COST} الماس لازمه.\nاز «خرید الماس» شارژ کن یا با دعوت دوستان الماس بگیر.',reply_markup=KB([[B('خرید الماس',callback_data='shop:buy')],[B('دعوت دوستان',callback_data='shop:invite')],[B('منوی اصلی',callback_data='shop:home')]])); return
        if not trial_active:
            change(uid,-5)
        s=get_session()
        try:
            x=s.get(Wallet,uid); x.self_started=True; x.last_charge=int(time.time()); s.commit()
        finally:s.close()
        # Enable self access and present existing safe account connection flow
        selfbot.save_row(uid,enabled=True)
        await show_panel(q, ('دورهٔ رایگان فعاله؛ الماسی کسر نشد. حالا شمارهٔ اکانتی که می‌خوای وصل کنی رو با دکمهٔ اتصال اکانت وارد کن. هرگز کد ورودت رو برای هیچ‌کس نفرست.' if trial_active else '۵ الماس کسر شد. حالا شمارهٔ اکانتی که می‌خوای وصل کنی رو با دکمهٔ اتصال اکانت وارد کن. هرگز کد ورودت رو برای هیچ‌کس نفرست.'),reply_markup=KB([[B('🔗 اتصال اکانت',callback_data='self:conn')],[B('👤 حساب کاربری',callback_data='shop:dash')],[B('منوی اصلی',callback_data='shop:home')]]))
    elif data=='shop:off':
        s=get_session()
        try: x=s.get(Wallet,uid); x.self_started=False; x.last_charge=int(time.time()); s.commit()
        finally:s.close()
        selfbot.save_row(uid, enabled=False)
        if uid in selfbot._clients: await selfbot.stop_client(uid,restore=True)
        await show_panel(q, '⏹ سلف خاموش شد. هزینه‌ی حالت خاموش ۰٫۲ الماس در ساعت است.',reply_markup=home_kb())
    elif data=='shop:dash': await show_panel(q, f'👤 حساب کاربری\n🆔 آیدی: {uid}\n💎 موجودی: {w["balance"]} الماس\n🔌 وضعیت سلف: {"روشن" if w["self_started"] else "خاموش"}',reply_markup=KB([[B('خرید الماس',callback_data='shop:buy')],[B('منوی اصلی',callback_data='shop:home')]]))
    elif data=='shop:about': await show_panel(q, 'سلف ابزاریه که از اکانت تلگرام خودت، طبق تنظیماتی که انجام می‌دی، کارهای خودکار انجام می‌ده. استفاده از اکانت شخصی ریسک محدودیت تلگرام رو داره. اگه نفهمیدی یا کمکی خواستی با پشتیبانی تماس بگیر: @escotch',reply_markup=KB([[B('پشتیبانی',url='https://t.me/escotch')],[B('منوی اصلی',callback_data='shop:home')]]))
    elif data=='shop:help': await show_panel(q, 'برای شروع «ساخت سلف» و سپس «روشن کردن» رو بزن. بعد اتصال اکانت رو انجام بده و در چت‌های تلگرام خودت دستور «پنل» رو بنویس. پشتیبانی: @escotch',reply_markup=home_kb())
    elif data=='shop:buy': await show_panel(q, f'💎 خرید الماس\n\n۱ ماه — ۱۰۰۰ الماس — ۷۵٬۰۰۰ تومان\n۲ ماه — ۱۸۰۰ الماس — ۱۲۰٬۰۰۰ تومان\n\nشماره کارت: {CARD}\nبه نام: {OWNER}\nبعد از واریز، رسید رو همین‌جا ارسال کن؛ رسید و توضیحات برای پشتیبانی فرستاده می‌شه.',reply_markup=KB([[B('خرید ۱ ماهه',callback_data='shop:pay:1')],[B('خرید ۲ ماهه',callback_data='shop:pay:2')],[B('منوی اصلی',callback_data='shop:home')]]))
    elif data.startswith('shop:pay:'):
        plan=data.rsplit(':',1)[1]; context.user_data['payment_plan']=plan
        await show_panel(q, 'حالا عکس یا فایل رسید پرداخت رو ارسال کن. برای لغو بنویس «لغو».')
    elif data=='shop:invite':
        me=await context.bot.get_me(); link=f'https://t.me/{me.username}?start=ref{uid}'
        await show_panel(q, f'🔗 لینک اختصاصی دعوتت:\n{link}\nهر کاربر جدیدی که با لینک تو وارد بشه و عضویت کانال‌هاش تأیید بشه، ۵۰ الماس می‌گیره و برای تو هم ۵۰ الماس ثبت می‌شه.',reply_markup=KB([[B('منوی اصلی',callback_data='shop:home')]]))
    elif data=='shop:code':
        context.user_data['gift_step']='redeem'; await show_panel(q, '🎁 کد هدیه‌ات رو بفرست (مثل RUBI-XXXXXXXX). برای لغو بنویس «لغو».')
    elif data=='shop:transfer':
        context.user_data['transfer_step']='recipient'; await show_panel(q, 'آیدی عددی گیرنده یا @شناسه کاربری رو بفرست:')
async def payment_review(update, context):
    q = update.callback_query
    uid = q.from_user.id
    if uid not in selfbot.ADMIN_IDS:
        await q.answer("دسترسی ندارید", show_alert=True)
        return
    await q.answer()
    parts = (q.data or "").split(":")
    if len(parts) != 3:
        return
    action, pid = parts[1], parts[2]
    try:
        pid = int(pid)
    except ValueError:
        return
    s = get_session()
    try:
        p = s.get(Payment, pid)
        if not p:
            await q.edit_message_reply_markup(reply_markup=None)
            await q.message.reply_text("درخواست پرداخت پیدا نشد.")
            return
        if p.status != "pending":
            await q.answer("این درخواست قبلاً بررسی شده است.", show_alert=True)
            return
        if action == "approve":
            amount = Decimal("1000") if p.plan == "1" else Decimal("1800")
            w = s.get(Wallet, p.user_id)
            if not w:
                w = Wallet(user_id=p.user_id, balance=Decimal("0"), last_charge=int(time.time()))
                s.add(w)
            w.balance = Decimal(w.balance or 0) + amount
            p.status = "approved"
            s.commit()
            await context.bot.send_message(p.user_id, f"پرداخت تأیید شد؛ {amount} الماس به حسابت اضافه شد.")
            await q.edit_message_reply_markup(reply_markup=None)
            await q.message.reply_text(f"تأیید شد. {amount} الماس به کاربر {p.user_id} اضافه شد.")
        elif action == "reject":
            p.status = "rejected"
            s.commit()
            await context.bot.send_message(p.user_id, "درخواست خرید الماس رد شد. برای بررسی بیشتر با پشتیبانی تماس بگیر.")
            await q.edit_message_reply_markup(reply_markup=None)
            await q.message.reply_text(f"درخواست #{pid} رد شد.")
    finally:
        s.close()

async def support_panel(update, context):
    uid = update.effective_user.id
    if uid not in selfbot.ADMIN_IDS:
        return
    s = get_session()
    try:
        pending = s.query(Payment).filter(Payment.status == "pending").order_by(Payment.id.desc()).limit(20).all()
        lines = ["پنل پشتیبانی", "درخواست‌های پرداخت در انتظار:"]
        lines += [f"#{p.id} | کاربر {p.user_id} | پلن {p.plan}" for p in pending]
        if not pending:
            lines.append("درخواست پرداختی در انتظار نیست.")
        await update.effective_message.reply_text("\\n".join(lines), reply_markup=KB([[btn("بازگشت به پنل اصلی", "shop:home")]]))
    finally:
        s.close()

async def incoming(update,context):
    m=update.effective_message; u=update.effective_user
    if not m or not u or update.effective_chat.type!='private': return
    if context.user_data.get('gift_step')=='admin_new' and u.id in selfbot.ADMIN_IDS and m.text:
        t=m.text.strip()
        if t=='لغو':
            context.user_data.pop('gift_step',None); await m.reply_text('لغو شد.'); return
        await _gift_create(m, context, t); return
    if not await is_member(context.bot,u.id):
        await m.reply_text('برای استفاده باید عضو هر دو کانال باشی.',reply_markup=join_kb()); return
    if context.user_data.get('gift_step')=='redeem' and m.text:
        t=m.text.strip()
        if t=='لغو':
            context.user_data.pop('gift_step',None); await m.reply_text('لغو شد.',reply_markup=home_kb()); return
        now=time.time(); fails=[x for x in context.user_data.get('gift_fail',[]) if now-x<600]
        if len(fails)>=5:
            await m.reply_text('⛔ تلاش‌های اشتباه زیاد بود؛ ۱۰ دقیقه دیگه دوباره امتحان کن.'); return
        res,amt,bal=redeem_gift(u.id,t)
        if res=='ok':
            context.user_data.pop('gift_step',None); context.user_data.pop('gift_fail',None)
            await m.reply_text(f'🎉 کد هدیه ثبت شد!\n💎 {fmt_bal(amt)} الماس به حسابت اضافه شد.\n💰 موجودی جدید: {fmt_bal(bal)} الماس',reply_markup=home_kb()); return
        if res=='invalid':
            fails.append(now); context.user_data['gift_fail']=fails
        msgs={'invalid':'❌ کد معتبر نیست؛ دوباره بفرست یا «لغو» بنویس.','expired':'⌛ این کد منقضی شده.','finished':'😕 ظرفیت این کد تموم شده.','already':'ℹ️ این کد رو قبلاً استفاده کردی.'}
        if res!='invalid': context.user_data.pop('gift_step',None)
        await m.reply_text(msgs.get(res,'❌ کد معتبر نیست.')); return
    plan=context.user_data.get('payment_plan')
    if plan and (m.photo or m.document):
        s=get_session()
        try:
            p=Payment(user_id=u.id,plan=plan,status='pending'); s.add(p); s.commit(); pid=p.id
        finally:s.close()
        context.user_data.pop('payment_plan',None)
        recipients=set(selfbot.ADMIN_IDS)
        recipients.add('@escotch')
        for admin in recipients:
            try:
                await context.bot.send_message(admin,f'رسید خرید الماس\nکاربر: {u.id} (@{u.username or "ندارد"})\nپلن: {"۱ ماهه / ۱۰۰۰ الماس" if plan=="1" else "۲ ماهه / ۱۸۰۰ الماس"}\nشناسه پیگیری: {pid}', reply_markup=KB([[btn('تأیید و واریز الماس', f'pay:approve:{pid}', style='success'), btn('رد درخواست', f'pay:reject:{pid}', style='danger')]]))
                if m.photo: await context.bot.send_photo(admin,m.photo[-1].file_id,caption=f'رسید کاربر {u.id} | پرداخت #{pid}')
                elif m.document: await context.bot.send_document(admin,m.document.file_id,caption=f'رسید کاربر {u.id} | پرداخت #{pid}')
            except Exception: pass
        await m.reply_text('✅ رسید برای پشتیبانی ارسال شد. بعد از بررسی، الماس‌ها به حسابت اضافه می‌شن.',reply_markup=home_kb()); return
    if context.user_data.get('transfer_step')=='recipient':
        val=m.text.strip() if m.text else ''
        target=None
        if val.startswith('@'):
            try: target=(await context.bot.get_chat(val)).id
            except Exception: pass
        else:
            try: target=int(val)
            except Exception: pass
        if not target or target==u.id: await m.reply_text('آیدی معتبر بفرست؛ نمی‌تونی به خودت انتقال بدی.'); return
        context.user_data['transfer_target']=target; context.user_data['transfer_step']='amount'; await m.reply_text('مقدار الماس رو بفرست (مثلاً 25 یا 10.5):'); return
    if context.user_data.get('transfer_step')=='amount':
        try: amount=Decimal(m.text.strip().replace(',','.'))
        except Exception: amount=Decimal('0')
        target=context.user_data.get('transfer_target'); bal=wallet(u.id)['balance']
        if amount<=0 or amount*10 != (amount*10).to_integral_value() or bal<amount:
            await m.reply_text(f'مقدار نامعتبره یا موجودی کافی نیست. موجودی: {bal}'); return
        # Debit and credit in a single DB transaction
        s=get_session()
        try:
            src=s.get(Wallet,u.id); dst=s.get(Wallet,target)
            if not dst: dst=Wallet(user_id=target,balance=Decimal('0.0'),last_charge=int(time.time())); s.add(dst)
            if Decimal(src.balance)<amount: await m.reply_text('موجودی کافی نیست.'); return
            src.balance=Decimal(src.balance)-amount; dst.balance=Decimal(dst.balance)+amount; s.commit()
        finally:s.close()
        context.user_data.pop('transfer_step',None); context.user_data.pop('transfer_target',None)
        await m.reply_text(f'✅ {amount} الماس به {target} منتقل شد.',reply_markup=home_kb())
        try: await context.bot.send_message(target,f'💎 شما {amount} الماس از کاربر {u.id} دریافت کردید.')
        except Exception: pass
        return
    if m.text and m.text.strip()=='لغو':
        context.user_data.pop('payment_plan',None); context.user_data.pop('transfer_step',None); await m.reply_text('لغو شد.',reply_markup=home_kb())
async def charge_job(context):
    now=int(time.time()); s=get_session()
    try:
        for w in s.query(Wallet).all():
            trial_active = bool(getattr(w, 'trial_until', None) and now < w.trial_until)
            if getattr(w, 'trial_until', None) and now >= w.trial_until:
                w.trial_until = None
                w.self_started = False
                try:
                    selfbot.save_row(w.user_id, enabled=False)
                    context.application.create_task(selfbot.stop_client(w.user_id, restore=True))
                    context.application.create_task(context.bot.send_message(w.user_id, 'دورهٔ رایگان ۳ روزه تموم شد و سلف به‌صورت خودکار قطع شد. برای ادامه، الماس تهیه کن.'))
                except Exception:
                    pass
            # عضویت اجباری: اگر کاربر بعد از دریافت پاداش معرفی از کانال‌ها خارج شود، پاداش ۵۰ الماسی برگردانده می‌شود.
            try:
                member = await is_member(context.bot, w.user_id)
            except Exception:
                member = True
            if not member and w.referred_awarded:
                w.balance=max(Decimal('0.0'), Decimal(w.balance or 0)-Decimal('50.0'))
                if w.referrer:
                    rw=s.get(Wallet,w.referrer)
                    if rw: rw.balance=max(Decimal('0.0'),Decimal(rw.balance or 0)-Decimal('50.0'))
                w.referred_awarded=False
                try: context.application.create_task(context.bot.send_message(w.user_id,'⚠️ از یکی از کانال‌های عضویت اجباری خارج شدی؛ ۵۰ الماس پاداش دعوتت کسر شد. دوباره عضو شو.',reply_markup=join_kb()))
                except Exception: pass
            if trial_active:
                # No diamond deductions during the 72-hour free period.
                continue
            last=w.last_charge or now; hours=(now-last)//3600
            if hours<=0: continue
            rate=Decimal('2.0') if w.self_started else Decimal('0.2')
            cost=rate*hours; w.balance=max(Decimal('0.0'),Decimal(w.balance or 0)-cost); w.last_charge=last+hours*3600
            if w.self_started and Decimal(w.balance)<=0:
                w.self_started=False
                try: selfbot.save_row(w.user_id, enabled=False)
                except Exception: pass
                try: context.application.create_task(selfbot.stop_client(w.user_id,restore=True))
                except Exception: pass
                try: context.application.create_task(context.bot.send_message(w.user_id,'⚠️ الماس‌هات تموم شد و سلف خاموش شد. برای ادامه الماس بخر.'))
                except Exception: pass
            elif cost>0:
                try: context.application.create_task(context.bot.send_message(w.user_id,f'💎 هزینه‌ی {hours} ساعت: {cost} الماس. موجودی فعلی: {w.balance}'))
                except Exception: pass
        s.commit()
    finally:s.close()

async def _gift_create(m, context, t):
    parts=t.replace(',',' ').split()
    try:
        amount=Decimal(parts[0]); uses=int(parts[1]) if len(parts)>1 else 1; hours=int(parts[2]) if len(parts)>2 else None
        if amount<=0 or amount>100000 or uses<1 or uses>10000 or (amount*10)!=(amount*10).to_integral_value(): raise ValueError
    except Exception:
        await m.reply_text('فرمت درست نیست. مثال: 100 5  (۱۰۰ الماس، ۵ نفر) یا 100 5 48 (با اعتبار ۴۸ ساعته). برای لغو بنویس «لغو».'); return
    code=make_gift(m.from_user.id, amount, uses, hours)
    context.user_data.pop('gift_step',None)
    await m.reply_text(f'🎁 کد هدیه ساخته شد:\n\n{code}\n\n💎 {fmt_bal(amount)} الماس | 👥 {uses} بار استفاده'+(f' | ⏳ {hours} ساعت' if hours else '')+'\nکاربر باید از «کد هدیه» توی منوی ربات واردش کنه.')

async def gift_cb(update, context):
    q=update.callback_query
    if q.from_user.id not in selfbot.ADMIN_IDS:
        await q.answer('دسترسی نداری.', show_alert=True); return
    await q.answer()
    act=(q.data or '').split(':')[1]
    if act=='new':
        context.user_data['gift_step']='admin_new'
        await q.message.reply_text('🎁 ساخت کد هدیه\n\nمقدار الماس و تعداد دفعات استفاده رو بفرست:\n100 5  ← ۱۰۰ الماس برای ۵ نفر\n100 5 48  ← با اعتبار ۴۸ ساعته\nبرای لغو بنویس «لغو».')
    elif act=='list':
        s=get_session()
        try:
            rows=s.query(GiftCode).order_by(GiftCode.created.desc()).limit(15).all()
            lines=[f'{g.code} | 💎 {fmt_bal(g.amount)} | {g.used}/{g.max_uses}'+(' | ⌛' if g.expires and g.expires<int(time.time()) else '') for g in rows]
        finally:
            s.close()
        await q.message.reply_text('🎟 آخرین کدهای هدیه (کد | الماس | استفاده):\n\n'+('\n'.join(lines) if lines else 'هنوز کدی ساخته نشده.'))

_BAL_RE = r'^\s*(موجودی|موجودی من|موجودی\s*من\s*چقدره|موجودیم)\s*[؟?!.]*\s*$'

def _bal_kb(uid, balance):
    return KB([[B('💎 ' + fmt_bal(balance) + ' الماس', callback_data=f'bal:{uid}')]])

async def balance_cmd(update, context):
    m = update.effective_message; u = update.effective_user
    if not m or not u or not m.text:
        return
    bal = wallet(u.id)['balance']
    try:
        await m.reply_text('💰 موجودی شما', reply_markup=_bal_kb(u.id, bal), reply_to_message_id=m.message_id)
    except Exception:
        pass
    if update.effective_chat.type == 'private':
        raise ApplicationHandlerStop

async def balance_cb(update, context):
    q = update.callback_query
    try:
        owner = int((q.data or '').split(':')[1])
    except Exception:
        await q.answer(); return
    if q.from_user.id != owner:
        await q.answer('این دکمه برای تو نیست؛ خودت بنویس «موجودی».', show_alert=True); return
    w = wallet(owner)
    state = 'روشن ✅' if w['self_started'] else 'خاموش ⏹'
    await q.answer(f'💎 موجودی شما: {fmt_bal(w["balance"])} الماس\n🔌 سلف: {state}', show_alert=True)
    try:
        await q.edit_message_reply_markup(reply_markup=_bal_kb(owner, w['balance']))
    except Exception:
        pass

def setup(app):
    Base.metadata.create_all(__import__('database').engine)
    _migrate_wallet()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex(_BAL_RE), balance_cmd), group=-11)
    app.add_handler(CallbackQueryHandler(balance_cb, pattern=r'^bal:'), group=-11)
    app.add_handler(CallbackQueryHandler(gift_cb, pattern=r'^gift:'), group=-20)
    app.add_handler(CommandHandler('start',start),group=-10)
    app.add_handler(CallbackQueryHandler(payment_review, pattern=r'^pay:(approve|reject):'), group=-20)
    app.add_handler(CommandHandler('support', support_panel), group=-10)
    app.add_handler(CallbackQueryHandler(callback,pattern=r'^shop:'),group=-10)
    app.add_handler(MessageHandler((filters.TEXT|filters.PHOTO|filters.Document.ALL)&filters.ChatType.PRIVATE, incoming),group=-10)
    app.job_queue.run_repeating(charge_job, interval=300, first=60, name='diamond-hourly-charges')
