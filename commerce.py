# -*- coding: utf-8 -*-
"""منو، عضویت اجباری، الماس، دعوت، انتقال و رسید پرداخت."""
import time
from decimal import Decimal
from sqlalchemy import Column, BigInteger, String, Numeric, Integer, Boolean
from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as KB
from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler, filters
from database import Base, get_session
import selfbot

CHANNELS = [('@selfsaz_iran', 'https://t.me/selfsaz_iran'), ('@fox_frenzy', 'https://t.me/fox_frenzy')]
REQUIRE_JOIN = False  # عضویت اجباری خاموش؛ برای روشن‌کردن دوباره True کن
SUPPORT = '@escotch'
CARD = '6219861851160068'
OWNER = 'ظریفی'
class Wallet(Base):
    __tablename__ = 'diamond_wallets'
    user_id = Column(BigInteger, primary_key=True)
    balance = Column(Numeric(12, 1), nullable=False, default=Decimal('0.0'))
    referrer = Column(BigInteger, nullable=True)
    referred_awarded = Column(Boolean, default=False)
    last_charge = Column(Integer, default=0)
    self_started = Column(Boolean, default=False)
class Payment(Base):
    __tablename__ = 'diamond_payments'
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False)
    plan = Column(String(20), nullable=False)
    status = Column(String(20), default='pending')
    created = Column(Integer, default=lambda: int(time.time()))

def wallet(uid, ref=None):
    s=get_session()
    try:
        w=s.get(Wallet,uid)
        if not w:
            w=Wallet(user_id=uid,balance=Decimal('0.0'),last_charge=int(time.time()))
            if ref and ref != uid and s.get(Wallet,ref): w.referrer=ref
            s.add(w); s.commit()
        return {'balance': Decimal(w.balance or 0), 'referrer':w.referrer,'self_started':bool(w.self_started),'last_charge':w.last_charge or int(time.time())}
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
        if not w or not w.referrer or w.referred_awarded: return
        ref=s.get(Wallet,w.referrer)
        if ref:
            w.balance=Decimal(w.balance or 0)+Decimal('50.0')
            ref.balance=Decimal(ref.balance or 0)+Decimal('50.0')
            w.referred_awarded=True
            s.commit()
    finally:s.close()

def home_kb():
    return KB([[B('🏠 ساخت سلف',callback_data='shop:create',api_kwargs={'style':'success'})],
               [B('💎 خرید الماس',callback_data='shop:buy',api_kwargs={'style':'primary'})],
               [B('📊 داشبورد',callback_data='shop:dash'),B('❓ سلف چیه؟',callback_data='shop:about')],
               [B('👤 دعوت دوستان',callback_data='shop:invite'),B('💱 انتقال الماس',callback_data='shop:transfer')],
               [B('⭐ آموزش استفاده',callback_data='shop:help')],
               [B('✨ پشتیبانی',url='https://t.me/escotch')],
               [B('💎 درخواست نمایندگی',url='https://t.me/escotch')],
               [B('📣 کانال ما',url='https://t.me/selfsaz_iran'),B('🦊 گروه ما',url='https://t.me/fox_frenzy')]])
def join_kb():
    rows=[[B('عضویت در '+ch, url=url)] for ch,url in CHANNELS]
    rows.append([B('✅ عضو شدم',callback_data='shop:check')]); return KB(rows)
async def is_member(bot,uid):
    if not REQUIRE_JOIN: return True
    for ch,_ in CHANNELS:
        try:
            m=await bot.get_chat_member(ch,uid)
            if m.status in ('left','kicked'): return False
        except Exception:
            return False
    return True
async def start(update,context):
    u=update.effective_user
    if context.args and context.args[0]=='panel':
        await selfbot.cmd_self(update,context); return
    ref=None
    if context.args:
        try: ref=int(context.args[0].replace('ref',''))
        except: pass
    wallet(u.id,ref)
    award_referral(u.id)
    tx=("🤖 به ربات سلف خوش اومدی.\n\n"
        "اگه مدیر برات فعالش کرده، /self رو بزن یا بنویس «سلف».")
    rows=[]
    if u.id in selfbot.ADMIN_IDS:
        tx+="\n\nمدیر: برای فعال‌کردن کاربر، دکمه‌ی پایین یا /admin."
        rows.append([B("مدیریت سلف",callback_data="selfa:menu",api_kwargs={"style":"primary"})])
    await update.effective_message.reply_text(tx,reply_markup=KB(rows) if rows else None)
async def callback(update,context):
    q=update.callback_query; uid=q.from_user.id; data=q.data or ''; await q.answer()
    if data=='shop:check':
        if not await is_member(context.bot,uid): await q.message.reply_text('هنوز عضویت هر دو کانال تأیید نشده؛ بعد از عضویت دوباره بزن.',reply_markup=join_kb()); return
        award_referral(uid)
        await q.message.reply_text('✅ عضویت تأیید شد. از منوی زیر استفاده کن.',reply_markup=home_kb()); return
    if not await is_member(context.bot,uid):
        await q.message.reply_text('برای ادامه باید عضو هر دو کانال باشی.',reply_markup=join_kb()); return
    w=wallet(uid)
    if data=='shop:home': await q.message.reply_text('🏠 منوی اصلی',reply_markup=home_kb())
    elif data=='shop:create':
        await q.message.reply_text(f'💎 موجودی فعلی: {w["balance"]}\nهر بار روشن‌کردن: ۵ الماس\nهزینه‌ی ساعتی در حالت روشن: ۲ الماس؛ در حالت خاموش: ۰٫۲ الماس.',reply_markup=KB([[B('▶️ روشن کردن',callback_data='shop:on')],[B('⏹ خاموش کردن',callback_data='shop:off')],[B('👤 حساب کاربری',callback_data='shop:dash')],[B('🏠 منوی اصلی',callback_data='shop:home')]]))
    elif data=='shop:on':
        if w['self_started']:
            await q.message.reply_text('سلفت از قبل روشنه. برای دیدن تنظیمات، دکمه‌ی اتصال اکانت یا دستور «پنل» رو استفاده کن.',reply_markup=KB([[B('🔗 اتصال اکانت',callback_data='self:conn')],[B('⏹ خاموش کردن',callback_data='shop:off')],[B('🏠 منوی اصلی',callback_data='shop:home')]])); return
        if w['balance'] < 5: await q.message.reply_text('❌ حداقل ۵ الماس لازم داری. از بخش خرید الماس شارژ کن.',reply_markup=home_kb()); return
        change(uid,-5)
        s=get_session()
        try:
            x=s.get(Wallet,uid); x.self_started=True; x.last_charge=int(time.time()); s.commit()
        finally:s.close()
        # Enable self access and present existing safe account connection flow
        selfbot.save_row(uid,enabled=True)
        await q.message.reply_text('✅ ۵ الماس کسر شد. حالا شماره‌ی اکانتی که می‌خوای وصل کنی رو با دکمه‌ی اتصال اکانت وارد کن. هرگز کد ورودت رو برای هیچ‌کس نفرست.',reply_markup=KB([[B('🔗 اتصال اکانت',callback_data='self:conn')],[B('👤 حساب کاربری',callback_data='shop:dash')],[B('🏠 منوی اصلی',callback_data='shop:home')]]))
    elif data=='shop:off':
        s=get_session()
        try: x=s.get(Wallet,uid); x.self_started=False; x.last_charge=int(time.time()); s.commit()
        finally:s.close()
        if uid in selfbot._clients: await selfbot.stop_client(uid,restore=True)
        await q.message.reply_text('⏹ سلف خاموش شد. هزینه‌ی حالت خاموش ۰٫۲ الماس در ساعت است.',reply_markup=home_kb())
    elif data=='shop:dash': await q.message.reply_text(f'👤 حساب کاربری\n🆔 آیدی: {uid}\n💎 موجودی: {w["balance"]} الماس\n🔌 وضعیت سلف: {"روشن" if w["self_started"] else "خاموش"}',reply_markup=KB([[B('💎 خرید الماس',callback_data='shop:buy')],[B('🏠 منوی اصلی',callback_data='shop:home')]]))
    elif data=='shop:about': await q.message.reply_text('سلف ابزاریه که از اکانت تلگرام خودت، طبق تنظیماتی که انجام می‌دی، کارهای خودکار انجام می‌ده. استفاده از اکانت شخصی ریسک محدودیت تلگرام رو داره. اگه نفهمیدی یا کمکی خواستی با پشتیبانی تماس بگیر: @escotch',reply_markup=KB([[B('✨ پشتیبانی',url='https://t.me/escotch')],[B('🏠 منوی اصلی',callback_data='shop:home')]]))
    elif data=='shop:help': await q.message.reply_text('برای شروع «ساخت سلف» و سپس «روشن کردن» رو بزن. بعد اتصال اکانت رو انجام بده و در چت‌های تلگرام خودت دستور «پنل» رو بنویس. پشتیبانی: @escotch',reply_markup=home_kb())
    elif data=='shop:buy': await q.message.reply_text(f'💎 خرید الماس\n\n۱ ماه — ۱۰۰۰ الماس — ۷۵٬۰۰۰ تومان\n۲ ماه — ۱۸۰۰ الماس — ۱۲۰٬۰۰۰ تومان\n\nشماره کارت: {CARD}\nبه نام: {OWNER}\nبعد از واریز، رسید رو همین‌جا ارسال کن؛ رسید و توضیحات برای پشتیبانی فرستاده می‌شه.',reply_markup=KB([[B('خرید ۱ ماهه',callback_data='shop:pay:1')],[B('خرید ۲ ماهه',callback_data='shop:pay:2')],[B('🏠 منوی اصلی',callback_data='shop:home')]]))
    elif data.startswith('shop:pay:'):
        plan=data.rsplit(':',1)[1]; context.user_data['payment_plan']=plan
        await q.message.reply_text('حالا عکس یا فایل رسید پرداخت رو ارسال کن. برای لغو بنویس «لغو».')
    elif data=='shop:invite':
        me=await context.bot.get_me(); link=f'https://t.me/{me.username}?start=ref{uid}'
        await q.message.reply_text(f'🔗 لینک اختصاصی دعوتت:\n{link}\nهر کاربر جدیدی که با لینک تو وارد بشه، ۵۰ الماس می‌گیره و برای تو هم ۵۰ الماس ثبت می‌شه.',reply_markup=KB([[B('🏠 منوی اصلی',callback_data='shop:home')]]))
    elif data=='shop:transfer':
        context.user_data['transfer_step']='recipient'; await q.message.reply_text('آیدی عددی گیرنده یا @شناسه کاربری رو بفرست:')
async def incoming(update,context):
    m=update.effective_message; u=update.effective_user
    if not m or not u or update.effective_chat.type!='private': return
    if not await is_member(context.bot,u.id):
        await m.reply_text('برای استفاده باید عضو هر دو کانال باشی.',reply_markup=join_kb()); return
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
                await context.bot.send_message(admin,f'💳 رسید خرید الماس\nکاربر: {u.id} (@{u.username or "ندارد"})\nپلن: {"۱ ماهه / ۱۰۰۰ الماس" if plan=="1" else "۲ ماهه / ۱۸۰۰ الماس"}\nشناسه پیگیری: {pid}')
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
            # عضویت اجباری: اگر کاربر بعد از دریافت پاداش معرفی از کانال‌ها خارج شود، پاداش ۵۰ الماسی برگردانده می‌شود.
            try:
                member = await is_member(context.bot, w.user_id) if REQUIRE_JOIN else True
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
            last=w.last_charge or now; hours=(now-last)//3600
            if hours<=0: continue
            rate=Decimal('2.0') if w.self_started else Decimal('0.2')
            cost=rate*hours; w.balance=max(Decimal('0.0'),Decimal(w.balance or 0)-cost); w.last_charge=last+hours*3600
            if w.self_started and Decimal(w.balance)<=0:
                w.self_started=False
                try: context.application.create_task(selfbot.stop_client(w.user_id,restore=True))
                except Exception: pass
                try: context.application.create_task(context.bot.send_message(w.user_id,'⚠️ الماس‌هات تموم شد و سلف خاموش شد. برای ادامه الماس بخر.'))
                except Exception: pass
            elif cost>0:
                try: context.application.create_task(context.bot.send_message(w.user_id,f'💎 هزینه‌ی {hours} ساعت: {cost} الماس. موجودی فعلی: {w.balance}'))
                except Exception: pass
        s.commit()
    finally:s.close()
def setup(app):
    Base.metadata.create_all(__import__('database').engine)
    app.add_handler(CommandHandler('start',start),group=-10)
    app.add_handler(CallbackQueryHandler(callback,pattern=r'^shop:'),group=-10)
    app.add_handler(MessageHandler((filters.TEXT|filters.PHOTO|filters.Document.ALL)&filters.ChatType.PRIVATE, incoming),group=-10)
    app.job_queue.run_repeating(charge_job, interval=300, first=60, name='diamond-hourly-charges')
