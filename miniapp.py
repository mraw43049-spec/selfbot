# -*- coding: utf-8 -*-
"""
مینی‌اپ تلگرام: داشبورد کاربر (الماس، وضعیت سلف، روشن/خاموش) + تب بازی‌ها (مار و پله) + تب سلف.

- بدون وابستگی جدید (فقط stdlib)؛ یک سرور HTTP سبک توی همین پروسهٔ ربات بالا میاد و روی PORT گوش می‌ده.
- احراز هویت: initData تلگرام با HMAC و BOT_TOKEN چک می‌شه؛ آیدی کاربر فقط از همون خونده می‌شه (نه از ورودی کاربر).
- متغیرها: MINIAPP_URL (آدرس عمومی https، اختیاری؛ اگه نباشه از RAILWAY_PUBLIC_DOMAIN ساخته می‌شه)،
  PORT (Railway خودش می‌ده)، MINIAPP_DISABLED=1 برای خاموش‌کردن کامل.
- نصب: خودکار؛ commerce.setup(app) این ماژول رو صدا می‌زنه (مار و پله، snakes.py، هم از همین‌جا نصب می‌شه). تغییر main.py لازم نیست.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from decimal import Decimal
from urllib.parse import parse_qsl

logger = logging.getLogger(__name__)

AUTH_MAX_AGE = 24 * 3600      # initData قدیمی‌تر از این پذیرفته نمی‌شه
MAX_BODY = 16 * 1024
_app_ref = [None]
_server = [None]


# ---------- آدرس ----------
def base_url():
    if os.environ.get("MINIAPP_DISABLED", "").strip().lower() in ("1", "true", "yes", "on"):
        return ""
    u = os.environ.get("MINIAPP_URL", "").strip().rstrip("/")
    if not u:
        d = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip()
        u = ("https://" + d) if d else ""
    if u and not u.startswith("http"):
        u = "https://" + u
    return u


def app_url():
    b = base_url()
    return (b + "/app") if b else ""


# ---------- احراز هویت ----------
def validate_init_data(init_data, token, now=None, max_age=AUTH_MAX_AGE):
    """initData رو بررسی می‌کنه؛ خروجی: dict کاربر یا None."""
    if not init_data or not token:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=False))
    except Exception:
        return None
    got = pairs.pop("hash", None)
    if not got:
        return None
    check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, got):
        return None
    try:
        age = (now or time.time()) - int(pairs.get("auth_date", "0"))
    except ValueError:
        return None
    if age > max_age or age < -300:
        return None
    try:
        user = json.loads(pairs.get("user", ""))
        if not isinstance(user, dict) or not isinstance(user.get("id"), int):
            return None
        return user
    except Exception:
        return None


# ---------- منطق (همون حساب‌های commerce.py) ----------
def _cm():
    import commerce
    return commerce


def build_state(uid, member=True):
    cm = _cm()
    import selfbot
    w = cm.wallet(uid)
    bal = Decimal(w["balance"])
    on = bool(w["self_started"])
    rate = Decimal("2.0") if on else Decimal("0.2")
    hours = int(bal / rate) if bal > 0 else 0
    row = selfbot.get_row(uid)
    now = int(time.time())
    return {
        "id": uid,
        "balance": cm.fmt_bal(bal),
        "spent": cm.fmt_bal(w["spent"]),
        "toman": int(bal * Decimal(cm.unit_price())),
        "on": on,
        "rate": "2" if on else "0.2",
        "hours": hours,
        "gift_received": bool(w["trial_used"]),
        "gift_amount": cm.TRIAL_BONUS,
        "connected": bool(row and row.session_enc),
        "trial_active": bool(w.get("trial_until") and int(w["trial_until"]) > now),
        "activate_cost": cm.ACTIVATE_COST,
        "member": bool(member),
        "support": cm.SUPPORT,
        "game": _game_of(uid),
    }


def _game_of(uid):
    try:
        import snakes
        return snakes.active_summary(uid)
    except Exception:
        return None


async def _member(uid):
    try:
        return bool(await _cm().is_member(_app_ref[0].bot, uid))
    except Exception:
        logger.exception("miniapp: membership check failed")
        return True       # خطای موقت تلگرام نباید داشبورد رو ببنده؛ تغییر وضعیت جدا چک می‌شه


async def toggle(uid, want_on):
    """روشن/خاموش‌کردن سلف؛ همون منطق دکمه‌های shop:on و shop:off. خروجی: (ok, کد خطا یا None)"""
    cm = _cm()
    import selfbot
    if not await _member(uid):
        return False, "not_member"
    w = cm.wallet(uid)
    if want_on:
        if w["self_started"]:
            return True, None
        now = int(time.time())
        trial_active = bool(w.get("trial_until") and int(w["trial_until"]) > now)
        if w["balance"] < cm.ACTIVATE_COST and not trial_active:
            return False, "low_balance"
        if not trial_active:
            cm.change(uid, -cm.ACTIVATE_COST)
            cm.add_spent(uid, cm.ACTIVATE_COST)
        s = cm.get_session()
        try:
            x = s.get(cm.Wallet, uid)
            x.self_started = True
            x.last_charge = now
            s.commit()
        finally:
            s.close()
        selfbot.save_row(uid, enabled=True)
        row = selfbot.get_row(uid)
        if row and row.session_enc and uid not in selfbot._clients:
            _app_ref[0].create_task(selfbot.start_client(uid))
        return True, None
    s = cm.get_session()
    try:
        x = s.get(cm.Wallet, uid)
        if x:
            x.self_started = False
            x.last_charge = int(time.time())
            s.commit()
    finally:
        s.close()
    selfbot.save_row(uid, enabled=False)
    if uid in selfbot._clients:
        await selfbot.stop_client(uid, restore=True)
    return True, None


# ---------- سرور HTTP سبک ----------
_STATUS = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found",
           405: "Method Not Allowed", 413: "Payload Too Large", 500: "Internal Server Error"}
_rate = {}   # (uid, bucket) -> [timestamps]


def _limited(uid, limit=30, window=60, bucket="api"):
    now = time.time()
    k = (uid, bucket)
    q = [t for t in _rate.get(k, []) if now - t < window]
    q.append(now)
    _rate[k] = q
    if len(_rate) > 5000:
        for kk in [kk for kk, v in _rate.items() if not v or now - v[-1] > window]:
            _rate.pop(kk, None)
    return len(q) > limit


def _resp(code, body, ctype="application/json; charset=utf-8", cache="no-store"):
    if isinstance(body, (dict, list)):
        body = json.dumps(body, ensure_ascii=False)
    b = body.encode() if isinstance(body, str) else body
    head = (f"HTTP/1.1 {code} {_STATUS.get(code, 'OK')}\r\nContent-Type: {ctype}\r\nContent-Length: {len(b)}\r\n"
            f"Cache-Control: {cache}\r\nX-Content-Type-Options: nosniff\r\n"
            "Content-Security-Policy: default-src 'self' https://telegram.org; script-src 'self' 'unsafe-inline' https://telegram.org; "
            "style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors https://web.telegram.org https://*.telegram.org\r\n"
            "Connection: close\r\n\r\n")
    return head.encode() + b


BOARD_FILE = "snakes_board.jpg"      # عکس تخته؛ کنار miniapp.py توی ریشهٔ ریپو
_board_cache = [None]


def _board_bytes():
    if _board_cache[0] is None:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), BOARD_FILE)
        try:
            with open(p, "rb") as f:
                _board_cache[0] = f.read()
        except Exception:
            logger.warning("miniapp: %s پیدا نشد؛ تخته بدون عکس نشون داده می‌شه.", BOARD_FILE)
            _board_cache[0] = b""
    return _board_cache[0]


def _page():
    """صفحهٔ مینی‌اپ؛ جدول مار و پله از snakes.py داخلش گذاشته می‌شه (یک منبع واحد)."""
    try:
        import snakes
        j = json.dumps({"snakes": snakes.SNAKES, "ladders": snakes.LADDERS})
    except Exception:
        logger.exception("miniapp: snakes tables unavailable")
        j = '{"snakes":{},"ladders":{}}'
    return HTML.replace("__SNK__", j)


async def _route(method, path, headers, body):
    if path in ("/health", "/healthz"):
        return _resp(200, {"ok": True})
    if path in ("/", "/app"):
        return _resp(200, _page(), "text/html; charset=utf-8")
    if path == "/board.jpg":
        b = _board_bytes()
        if not b:
            return _resp(404, {"error": "not_found"})
        return _resp(200, b, "image/jpeg", cache="public, max-age=86400")
    if path in ("/api/me", "/api/toggle", "/api/snk/state", "/api/snk/roll"):
        if method != "POST":
            return _resp(405, {"error": "method"})
        auth = headers.get("authorization", "")
        init = auth[4:] if auth.lower().startswith("tma ") else ""
        user = validate_init_data(init, os.environ.get("BOT_TOKEN", "").strip())
        if not user:
            return _resp(401, {"error": "auth"})
        uid = user["id"]
        if path.startswith("/api/snk/"):
            if _limited(uid, 200, 60, "snk"):
                return _resp(403, {"error": "rate"})
            try:
                data = json.loads(body.decode() or "{}")
                if not isinstance(data, dict):
                    raise ValueError
            except Exception:
                return _resp(400, {"error": "bad_request"})
            import snakes
            return _resp(200, await snakes.api(path, uid, data))
        if _limited(uid):
            return _resp(403, {"error": "rate"})
        if path == "/api/me":
            return _resp(200, build_state(uid, await _member(uid)))
        try:
            data = json.loads(body.decode() or "{}")
            want = data.get("on")
            if not isinstance(want, bool):
                raise ValueError
        except Exception:
            return _resp(400, {"error": "bad_request"})
        ok, err = await toggle(uid, want)
        st = build_state(uid, err != "not_member")
        st["ok"] = ok
        if err:
            st["error"] = err
        return _resp(200, st)
    return _resp(404, {"error": "not_found"})


async def _handle(reader, writer):
    try:
        async def _serve():
            line = await reader.readline()
            parts = line.decode("latin-1").split()
            if len(parts) < 2:
                return _resp(400, {"error": "bad_request"})
            method, path = parts[0].upper(), parts[1].split("?", 1)[0]
            headers = {}
            for _ in range(100):
                h = await reader.readline()
                if h in (b"\r\n", b"\n", b""):
                    break
                k, _, v = h.decode("latin-1").partition(":")
                headers[k.strip().lower()] = v.strip()
            n = int(headers.get("content-length", "0") or 0)
            if n > MAX_BODY:
                return _resp(413, {"error": "too_large"})
            body = await reader.readexactly(n) if n else b""
            return await _route(method, path, headers, body)
        out = await asyncio.wait_for(_serve(), timeout=20)
    except Exception:
        logger.exception("miniapp request failed")
        out = _resp(500, {"error": "server"})
    try:
        writer.write(out)
        await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def _start(context=None):
    if _server[0]:
        return
    port = int(os.environ.get("PORT", "8080") or 8080)
    try:
        _server[0] = await asyncio.start_server(_handle, "0.0.0.0", port)
        logger.info("miniapp: listening on :%s (url=%s)", port, app_url() or "—")
    except Exception:
        logger.exception("miniapp: cannot start server on port %s", port)
        return
    if app_url():
        try:
            from telegram import MenuButtonWebApp, WebAppInfo
            await _app_ref[0].bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(text="داشبورد", web_app=WebAppInfo(url=app_url())))
        except Exception:
            logger.exception("miniapp: set menu button failed")


async def cmd_app(update, context):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
    if update.effective_chat.type != "private":
        return
    if not app_url():
        await update.effective_message.reply_text("مینی‌اپ هنوز تنظیم نشده (MINIAPP_URL یا دامنهٔ عمومی Railway لازمه).")
        return
    await update.effective_message.reply_text(
        "📊 داشبورد مینی‌اپ", reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("باز کردن داشبورد", web_app=WebAppInfo(url=app_url()))]]))


def setup(app):
    from telegram.ext import CommandHandler
    if _app_ref[0] is app:      # اگه دستی هم صدا زده بشه، دوبار نصب نمی‌شه
        return
    _app_ref[0] = app
    # آدرس درخواست‌های httpx شامل توکن رباته؛ از لاگ پنهانش می‌کنیم
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    if os.environ.get("MINIAPP_DISABLED", "").strip().lower() in ("1", "true", "yes", "on"):
        return
    if not base_url():
        logger.warning("miniapp: MINIAPP_URL / RAILWAY_PUBLIC_DOMAIN تنظیم نشده؛ سرور بالا میاد ولی دکمهٔ مینی‌اپ نشون داده نمی‌شه.")
    app.add_handler(CommandHandler("app", cmd_app), group=-10)
    app.job_queue.run_once(_start, 2, name="miniapp-start")
    try:
        import snakes
        snakes.setup(app)
    except Exception:
        logger.exception("miniapp: snakes setup failed")


# ---------- رابط کاربری ----------
HTML = r"""<!doctype html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,viewport-fit=cover">
<title>داشبورد</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
:root{--bg:var(--tg-theme-bg-color,#fff);--tx:var(--tg-theme-text-color,#111);--hint:var(--tg-theme-hint-color,#707579);
--card:var(--tg-theme-secondary-bg-color,#f1f3f5);--btn:var(--tg-theme-button-color,#2481cc);--btx:var(--tg-theme-button-text-color,#fff);
--ok:#2e9e5b;--bad:#d64545}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:var(--bg);color:var(--tx);font-family:system-ui,-apple-system,"Vazirmatn",Tahoma,sans-serif;
padding:16px 14px calc(84px + env(safe-area-inset-bottom));min-height:100vh}
body.ing{padding-bottom:calc(24px + env(safe-area-inset-bottom))}
h1{font-size:18px;margin:0 0 14px}
.hero{background:var(--card);border-radius:18px;padding:18px;text-align:center}
.hero .n{font-size:40px;font-weight:700;line-height:1.1}
.hero .l{color:var(--hint);font-size:13px;margin-top:4px}
.pill{display:inline-block;margin-top:12px;padding:5px 14px;border-radius:99px;font-size:13px;font-weight:600;color:#fff}
.pill.on{background:var(--ok)}.pill.off{background:var(--hint)}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px}
.c{background:var(--card);border-radius:14px;padding:12px}
.c b{display:block;font-size:17px;margin-top:2px}.c span{color:var(--hint);font-size:12px}
.row{background:var(--card);border-radius:14px;padding:12px;margin-top:10px;display:flex;justify-content:space-between;gap:8px;font-size:14px}
.row span:first-child{color:var(--hint)}
button{width:100%;margin-top:14px;padding:14px;border:0;border-radius:14px;font-size:16px;font-weight:600;background:var(--btn);color:var(--btx);font-family:inherit}
button.red{background:var(--bad);color:#fff}button.alt{background:var(--card);color:var(--tx)}
button:disabled{opacity:.55}
.msg{margin-top:12px;padding:10px 12px;border-radius:12px;background:var(--card);font-size:13px;text-align:center}
.id{direction:ltr;unicode-bidi:embed;cursor:pointer}
.skel{opacity:.5}
.sm{width:auto;padding:8px 14px;font-size:13px;margin:0 0 10px}
.pl{display:flex;align-items:center;gap:8px;background:var(--card);border-radius:12px;padding:9px 12px;margin-top:6px;font-size:14px}
.pl u{display:inline-block;width:13px;height:13px;border-radius:50%;border:1.5px solid #fff;text-decoration:none;flex:none}
.pl span{flex:1}.pl.cur{outline:2px solid var(--btn)}.pl.out{opacity:.45;text-decoration:line-through}
.dice{font-size:64px;line-height:1;text-align:center;margin-top:12px}
.shake{animation:sh .6s}
@keyframes sh{0%{transform:rotate(0)}25%{transform:rotate(-25deg) scale(1.2)}50%{transform:rotate(25deg) scale(1.2)}75%{transform:rotate(-12deg)}100%{transform:rotate(0)}}
.top{background:var(--card);border-radius:14px;padding:12px;text-align:center;font-size:15px}
.lg{color:var(--hint);font-size:12px;margin-top:8px;line-height:1.7}

/* ---- تخته (عکس + لایهٔ شماره‌ها و مهره‌ها) ---- */
.bw{position:relative;width:100%;max-width:520px;margin:12px auto 0;border-radius:14px;overflow:hidden;background:#1d2733;direction:ltr}
.bw img{display:block;width:100%;height:auto}
.bw.nobg{aspect-ratio:1024/1536}
.ov{position:absolute;left:15.918%;top:18.229%;width:72.266%;height:70.964%;display:grid;grid-template-columns:repeat(10,1fr);grid-template-rows:repeat(10,1fr)}
.nobg .c1{outline:1px solid rgba(255,255,255,.18)}
.c1{position:relative}
.c1 b{position:absolute;top:2px;left:2px;font:700 8.5px/1 system-ui,sans-serif;color:#fff;background:rgba(0,0,0,.52);padding:1px 3px;border-radius:4px}
.c1.hs b{background:rgba(214,45,45,.9)}.c1.ld b{background:rgba(35,150,80,.92)}
.tkl{position:absolute;left:0;top:0;right:0;bottom:0;pointer-events:none}
.tok{position:absolute;width:21px;height:21px;margin:-10px 0 0 -10px;border-radius:50%;border:2.5px solid #fff;box-shadow:0 2px 7px rgba(0,0,0,.65);transition:left .2s ease-out,top .2s ease-out,opacity .3s;z-index:3}
.tok.jump{transition:left .9s cubic-bezier(.55,0,.25,1),top .9s cubic-bezier(.55,0,.25,1)}
.tok.me{box-shadow:0 0 0 3px rgba(255,235,59,.9),0 2px 7px rgba(0,0,0,.65)}
.toast{position:fixed;left:50%;bottom:calc(24px + env(safe-area-inset-bottom));transform:translateX(-50%);max-width:92%;background:rgba(20,20,20,.88);color:#fff;padding:10px 16px;border-radius:14px;font-size:14px;z-index:9;display:none;text-align:center}

/* ---- تب‌ها و بازی‌ها ---- */
.tabs{position:fixed;left:0;right:0;bottom:0;display:flex;background:var(--card);border-top:1px solid rgba(128,128,128,.25);padding-bottom:env(safe-area-inset-bottom);z-index:5}
.tabs a{flex:1;text-align:center;padding:9px 0 8px;font-size:12px;color:var(--hint);cursor:pointer}
.tabs a i{display:block;font-style:normal;font-size:20px;margin-bottom:1px}
.tabs a.on{color:var(--btn);font-weight:700}
body.ing .tabs{display:none}
.gc{background:var(--card);border-radius:18px;overflow:hidden;margin-top:4px}
.gimg{height:150px;background:#1d2733 url(/board.jpg) top center/100% auto no-repeat}
.gb{padding:14px}
.gb h3{margin:0 0 6px;font-size:17px}
.gb p{margin:0;color:var(--hint);font-size:13px;line-height:1.8}
ul.rl{margin:10px 0 0;padding:0 18px 0 0;font-size:13px;line-height:1.9;color:var(--tx)}
.soon{opacity:.6}
</style></head><body>
<h1 id="ttl">📊 داشبورد</h1>
<div id="v" class="skel"><div class="hero"><div class="n">…</div><div class="l">در حال بارگذاری</div></div></div>
<div id="gl" style="display:none"></div>
<div id="sf" style="display:none"></div>
<div id="g" style="display:none"></div>
<div class="toast" id="toast"></div>
<div class="tabs" id="tabs"><a data-t="dash" class="on"><i>📊</i>داشبورد</a><a data-t="games"><i>🎮</i>بازی‌ها</a><a data-t="self"><i>⚡</i>سلف</a></div>
<script>
const SNK=__SNK__;
const tg=window.Telegram&&Telegram.WebApp;if(tg){tg.ready();tg.expand();}
const init=tg?tg.initData:"";
const $=id=>document.getElementById(id);
const fa=s=>String(s).replace(/\d/g,d=>"۰۱۲۳۴۵۶۷۸۹"[d]).replace(".","٫");
const num=n=>fa(Number(n).toLocaleString("en"));
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
function span(h){const d=Math.floor(h/24),x=h%24;return (d?fa(d)+" روز و ":"")+fa(x)+" ساعت"}
const E={low_balance:"الماس کافی نداری؛ اول شارژ کن.",not_member:"اول باید عضو کانال‌ها بشی (داخل ربات).",rate:"کمی صبر کن و دوباره امتحان کن.",auth:"نشست منقضی شده؛ مینی‌اپ رو دوباره باز کن."};
async function api(p,b){const r=await fetch(p,{method:"POST",headers:{"Authorization":"tma "+init,"Content-Type":"application/json"},body:JSON.stringify(b||{})});
 const j=await r.json().catch(()=>({}));if(!r.ok)throw new Error(E[j.error]||"خطا در ارتباط با سرور");return j}
function esc(s){return String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
function haptic(k){try{tg&&tg.HapticFeedback&&(k==="light"?tg.HapticFeedback.impactOccurred("light"):tg.HapticFeedback.notificationOccurred(k))}catch(e){}}
let _tt=null;
function toast(t,ms){const e=$("toast");e.textContent=t;e.style.display="block";clearTimeout(_tt);_tt=setTimeout(()=>e.style.display="none",ms||2600)}

/* ---------- تب‌ها ---------- */
let ME=null,VIEW="dash",busy=false;
const TT={dash:"📊 داشبورد",games:"🎮 بازی‌ها",self:"⚡ سلف"};
const PANES={dash:"v",games:"gl",self:"sf"};
function showTab(t){VIEW=t;document.body.classList.remove("ing");$("g").style.display="none";
 for(const k in PANES)$(PANES[k]).style.display=(k===t)?"block":"none";
 document.querySelectorAll(".tabs a").forEach(a=>a.classList.toggle("on",a.dataset.t===t));
 $("ttl").textContent=TT[t];$("ttl").style.display="block"}
document.querySelectorAll(".tabs a").forEach(a=>a.onclick=()=>{if(a.dataset.t!==VIEW||$("g").style.display==="block"){stopGame();showTab(a.dataset.t)}});
function msg(t){document.querySelectorAll(".nm").forEach(m=>{m.textContent=t;m.style.display=t?"block":"none"})}

/* ---------- داشبورد ---------- */
function renderAll(s,note){ME=s;renderDash(s,note);renderGames(s);renderSelf(s,note)}
function noteBox(note){return `<div class="msg nm" style="display:${note?"block":"none"}">${note||""}</div>`}
function toggleBtn(s){return `<button class="tgl ${s.on?"red":""}">${s.on?"خاموش کردن سلف":"روشن کردن سلف ("+fa(s.activate_cost)+" الماس)"}</button>`}
function bindTgl(){document.querySelectorAll(".tgl").forEach(b=>b.onclick=()=>flip())}
function renderDash(s,note){
 const warn=s.on&&s.hours<24?`<div class="msg">⚠️ شارژت کمتر از ${fa(Math.max(s.hours,1))} ساعت کافیه.</div>`:"";
 const conn=s.on&&!s.connected?`<div class="msg">اکانتت هنوز وصل نشده؛ از داخل ربات «ورود به اکانت» رو بزن.</div>`:"";
 $("v").className="";
 $("v").innerHTML=`
 <div class="hero"><div class="n">💎 ${fa(s.balance)}</div><div class="l">≈ ${num(s.toman)} تومان</div>
 <span class="pill ${s.on?"on":"off"}">${s.on?"سلف روشن ✅":"سلف خاموش ⏹"}</span></div>
 <div class="grid"><div class="c"><span>مصرف‌شده</span><b>${fa(s.spent)}</b></div>
 <div class="c"><span>کسر ساعتی</span><b>${fa(s.rate)} الماس</b></div></div>
 <div class="row"><span>⏳ باقی‌مانده</span><span>${span(s.hours)}</span></div>
 <div class="row"><span>🎁 هدیهٔ شروع</span><span>${s.gift_received?"دریافت شد ("+fa(s.gift_amount)+")":"هنوز نه"}</span></div>
 <div class="row"><span>🔗 اتصال اکانت</span><span>${s.connected?"وصل ✅":"وصل نیست"}</span></div>
 <div class="row"><span>🆔 آیدی</span><span class="id" onclick="navigator.clipboard&&navigator.clipboard.writeText('${s.id}');haptic('success')">${s.id}</span></div>
 ${warn}${conn}
 ${toggleBtn(s)}
 <button class="alt" id="b">خرید الماس / بازگشت به ربات</button>
 ${noteBox(note)}`;
 $("b").onclick=()=>tg&&tg.close();bindTgl();
}
async function flip(){
 if(busy||!ME)return;const s=ME;
 const go=async()=>{busy=true;document.querySelectorAll(".tgl").forEach(b=>b.disabled=true);
  try{const r=await api("/api/toggle",{on:!s.on});renderAll(r,r.error?E[r.error]:(r.on?"✅ سلف روشن شد.":"⏹ سلف خاموش شد."));}
  catch(e){msg(e.message)}finally{busy=false;document.querySelectorAll(".tgl").forEach(b=>b.disabled=false)}};
 if(s.on&&tg&&tg.showConfirm){tg.showConfirm("سلف خاموش بشه؟",ok=>ok&&go())}else go();
}

/* ---------- تب سلف ---------- */
function renderSelf(s,note){
 const warn=s.on&&!s.connected?`<div class="msg">اکانتت هنوز وصل نشده؛ از داخل ربات «ورود به اکانت» رو بزن.</div>`:"";
 $("sf").innerHTML=`
 <div class="hero"><div class="n">${s.on?"⚡ روشن":"⏹ خاموش"}</div>
 <div class="l">${s.on?"سلف فعاله؛ ساعتی "+fa(s.rate)+" الماس کسر می‌شه":"با روشن‌کردن، سلف اکانتت فعال می‌شه ("+fa(s.activate_cost)+" الماس)"}</div></div>
 <div class="row"><span>🔗 اتصال اکانت</span><span>${s.connected?"وصل ✅":"وصل نیست"}</span></div>
 <div class="row"><span>💎 موجودی</span><span>${fa(s.balance)}</span></div>
 <div class="row"><span>⏳ باقی‌مانده</span><span>${span(s.hours)}</span></div>
 ${warn}${toggleBtn(s)}
 <div class="msg">🎛 تنظیمات کامل سلف (فونت، پاسخ خودکار، ساعت، بازی‌ها و...) فعلاً از «.پنل» توی چت‌های تلگرامته.</div>
 ${noteBox(note)}`;bindTgl()}

/* ---------- تب بازی‌ها ---------- */
function renderGames(s){
 const cont=s.game?`<button id="gcont">▶️ ادامه بازی (${s.game.status==="lobby"?"در حال جمع‌شدن بازیکن":"در جریان"})</button>`:"";
 $("gl").innerHTML=`<div class="gc"><div class="gimg"></div><div class="gb">
 <h3>🎲🐍 مار و پله</h3>
 <p>۲ تا ۴ نفر، شرط‌بندی با الماس. اول به خونهٔ ۱۰۰ برسه، برنده‌ست!</p>
 <ul class="rl"><li>تاس ۶ = یه نوبت دیگه</li><li>🐍 رسیدی به سر مار؟ می‌افتی پایین</li><li>🪜 رسیدی به پایین نردبون؟ می‌ری بالا</li><li>نوبت ۳۰ ثانیه‌ست؛ ۳ بار پشت‌سرهم نندازی حذف می‌شی</li><li>جایزه = کل شرط‌ها منهای ۵٪ مالیات</li></ul>
 ${cont}
 <button class="alt" id="gprac">🤖 تمرین با ربات (رایگان)</button>
 <div class="msg">برای بازی با شرط: توی گروه بنویس «مار و پله 10» و بقیه رو دعوت کن.</div>
 </div></div>`;
 const c=$("gcont");if(c)c.onclick=()=>openGame(s.game.id,false);
 $("gprac").onclick=startPractice;
}

/* ---------- بازی مار و پله ---------- */
const COLORS=["#e5484d","#2e9e5b","#2481cc","#f5a524"],FACE=["⚀","⚁","⚂","⚃","⚄","⚅"];
const GE={not_player:"تو توی این بازی نیستی.",no_game:"این بازی فعال نیست.",not_your_turn:"نوبت تو نیست.",rate:"کمی صبر کن."};
let GID=null,GS=null,SIG="",LSEQ=-1,LEFT=0,LAT=0,PT=null,RB=false,FIRST=true,STOPAT=0,PRAC=false,PG=null,PON=false;
let TKE={},DISP={},Q=Promise.resolve();
function tokPos(n,idx){
 if(n<=0)return [3+(idx%4)*4.2,103];
 const c=(n-1)%10,rt=9-Math.floor((n-1)/10);
 const dx=[-2.4,2.4,-2.4,2.4][idx%4],dy=[-1.8,-1.8,1.8,1.8][idx%4];
 return [c*10+5+dx,rt*10+5+dy]}
function setTok(p,n,jump){
 let e=TKE[p.id];
 if(!e){e=document.createElement("div");e.className="tok";e.style.background=COLORS[p.idx%4];$("tkl").appendChild(e);TKE[p.id]=e}
 const g=GS;e.classList.toggle("me",!!(g&&p.id===g.me));
 e.classList.toggle("jump",!!jump);
 const xy=tokPos(n,p.idx);e.style.left=xy[0]+"%";e.style.top=xy[1]+"%";e.style.opacity=p.out?".35":"1";DISP[p.id]=n}
function shell(){
 let b="";
 for(let rt=0;rt<10;rt++)for(let c=0;c<10;c++){const n=(9-rt)*10+c+1;
  const cl=SNK.snakes[n]?" hs":SNK.ladders[n]?" ld":"";b+=`<div class="c1${cl}"><b>${n}</b></div>`}
 $("g").innerHTML=`<button class="alt sm" id="gback">‹ بازگشت</button>
 <div id="gtop"></div>
 <div class="bw"><img src="/board.jpg" alt="" onerror="this.style.display='none';this.parentNode.classList.add('nobg')"><div class="ov">${b}<div class="tkl" id="tkl"></div></div></div>
 <div class="lg" id="glog"></div>`;
 $("gback").onclick=()=>{stopGame();showTab(VIEW==="dash"?"games":VIEW);reloadDash()};
 TKE={};DISP={};Q=Promise.resolve()}
function openGame(gid,prac){
 stopGame();PRAC=!!prac;GID=gid;GS=null;
 for(const k in PANES)$(PANES[k]).style.display="none";
 $("g").style.display="block";$("ttl").style.display="none";document.body.classList.add("ing");
 shell();FIRST=true;SIG="";LSEQ=-1;STOPAT=0;
 if(PRAC){PON=true;applyG(PG)}
 else{$("gtop").innerHTML='<div class="msg">در حال بارگذاری…</div>';poll();PT=setInterval(poll,1300)}}
function stopGame(){clearInterval(PT);PT=null;PON=false;GS=null}
function reloadDash(){api("/api/me").then(s=>renderAll(s)).catch(()=>{})}
async function poll(){try{const r=await api("/api/snk/state",{gid:GID});
 if(!r.game){$("gtop").innerHTML=`<div class="msg">${GE[r.error]||"بازی فعالی نداری."}</div>`;clearInterval(PT);PT=null;return}
 applyG(r.game)}catch(e){}}
function nameOf(g,id){const p=g.players.find(x=>x.id===id);return p?p.name:"—"}
function applyG(g){
 GS=g;LEFT=g.left;LAT=Date.now();GID=g.id;
 const sig=JSON.stringify([g.status,g.turn,g.seq,g.players.map(p=>[p.pos,p.out]),g.winner]);
 if(sig!==SIG){SIG=sig;renderTop(g)}
 g.players.forEach(p=>{if(!TKE[p.id])setTok(p,FIRST?p.pos:0,false)});
 if(g.last&&g.last.seq!==LSEQ){
  if(FIRST)g.players.forEach(p=>setTok(p,p.pos,false));
  else{const d=$("dc");if(d){d.classList.add("shake");setTimeout(()=>d.classList.remove("shake"),600)}haptic("light");animMove(g,g.last)}
  LSEQ=g.last.seq}
 else if(FIRST)g.players.forEach(p=>setTok(p,p.pos,false));
 Q=Q.then(()=>{if(GS!==g)return;g.players.forEach(p=>{if(DISP[p.id]!==p.pos||TKE[p.id].style.opacity!==(p.out?".35":"1"))setTok(p,p.pos,false)})});
 FIRST=false;
 if(["done","cancelled","refunded"].includes(g.status)){
  if(g.status==="done"&&!g._shown){g._shown=1;Q=Q.then(()=>{toast(g.winner===g.me?"🏆 برنده شدی!":"🏁 "+nameOf(g,g.winner)+" برنده شد",3500);haptic(g.winner===g.me?"success":"warning")})}
  if(!PRAC){if(!STOPAT)STOPAT=Date.now()+8000;else if(Date.now()>STOPAT){clearInterval(PT);PT=null}}}}
function animMove(g,L){
 const p=g.players.find(x=>x.id===L.pid);if(!p)return;
 Q=Q.then(async()=>{
  if(!TKE[p.id])return;
  const land=Math.min(L.from+L.r,100);
  if(DISP[p.id]!==L.from){setTok(p,L.from,false);await sleep(60)}
  for(let k=L.from+1;k<=land;k++){setTok(p,k,false);await sleep(170)}
  if(L.ev){
   await sleep(350);
   const who=p.id===g.me?"تو":p.name;
   if(L.ev==="snake"){toast("🐍 "+(p.id===g.me?"مار گازت گرفت!":"مار "+who+" رو گاز گرفت!")+" "+fa(land)+" ← "+fa(L.to),2800);haptic("error")}
   else{toast("🪜 "+(p.id===g.me?"از نردبون رفتی بالا!":who+" از نردبون رفت بالا!")+" "+fa(land)+" ← "+fa(L.to),2800);haptic("success")}
   setTok(p,L.to,true);await sleep(1000);
  }
 })}
function renderTop(g){
 const me=g.me,mine=g.turn===me&&g.status==="playing";
 const nm=id=>esc(nameOf(g,id));
 let top;
 if(g.status==="lobby")top=`⏳ منتظر شروع بازی… <b id="tm">${fa(LEFT)}</b> ثانیه<br><span style="color:var(--hint);font-size:12px">شروع با دکمهٔ «شروع بازی» توی گروه یا خودکار</span>`;
 else if(g.status==="playing")top=g.practice?(mine?`🎲 <b>نوبت توئه!</b>`:`نوبت <b>${nm(g.turn)}</b>…`):(mine?`🎲 <b>نوبت توئه!</b> <span id="tm">${fa(LEFT)}</span> ثانیه`:`نوبت <b>${nm(g.turn)}</b> · <span id="tm">${fa(LEFT)}</span> ثانیه`);
 else if(g.status==="done")top=g.winner===me?(g.practice?`🏆 <b>بردی!</b> (تمرین)`:`🏆 <b>برنده شدی!</b><br>${fa(g.payout)} الماس به حسابت اضافه شد`):`🏁 <b>${nm(g.winner)}</b> برنده شد`;
 else top="❌ بازی لغو شد؛ شرط‌ها برگشت";
 const pl=g.players.map(p=>`<div class="pl ${p.id===g.turn&&g.status==="playing"?"cur":""} ${p.out?"out":""}"><u style="background:${COLORS[p.idx%4]}"></u><span>${esc(p.name)}${p.id===me&&!g.practice?" (تو)":""}</span><b>${p.pos?fa(p.pos):"شروع"}</b></div>`).join("");
 const L=g.last,ev=L?(L.ev==="ladder"?" 🪜":L.ev==="snake"?" 🐍":""):"";
 const lt=L?`${nm(L.pid)}: ${fa(L.r)} ← ${fa(L.from)} به ${fa(L.to)}${ev}${L.auto?" (خودکار)":""}`:"";
 const act=g.status==="playing"?`<div class="dice" id="dc">${L?FACE[L.r-1]:"🎲"}</div><div style="text-align:center;color:var(--hint);font-size:13px">${lt}</div>`+(mine?`<button id="rb">🎲 تاس بنداز</button>`:""):"";
 const money=g.practice?`<div class="row"><span>🤖 حالت تمرین</span><span>بدون الماس</span></div>`:`<div class="row"><span>💎 شرط هر نفر</span><span>${num(g.bet)}</span></div><div class="row"><span>🎁 جایزهٔ کل</span><span>${num(g.pot)}</span></div>`;
 $("gtop").innerHTML=`<div class="top">${top}</div>${money}${act}${pl}`;
 $("glog").innerHTML=g.log.map(x=>esc(x)).join("<br>");
 const rb=$("rb");if(rb)rb.onclick=roll}
async function roll(){
 if(RB)return;RB=true;const b=$("rb");if(b)b.disabled=true;
 try{
  if(PRAC){pRoll();return}
  const r=await api("/api/snk/roll",{gid:GID});if(r.game)applyG(r.game);if(r.error)toast(GE[r.error]||"خطا")}
 catch(e){toast(e.message)}finally{RB=false}}
setInterval(()=>{const e=$("tm");if(e&&GS&&!GS.practice)e.textContent=fa(Math.max(0,LEFT-Math.floor((Date.now()-LAT)/1000)))},500);

/* ---------- حالت تمرین (سمت کلاینت، بدون الماس) ---------- */
function startPractice(){
 PG={id:0,practice:true,status:"playing",bet:0,pot:0,me:1,creator:1,
  players:[{id:1,idx:0,name:"تو",pos:0,out:false},{id:2,idx:1,name:"ربات 🤖",pos:0,out:false}],
  turn:1,left:0,seq:0,last:null,log:["🎲 تمرین شروع شد"],winner:null,payout:null,snakes:SNK.snakes,ladders:SNK.ladders};
 openGame(0,true)}
function pRoll(){
 const g=PG;if(!PON||!g||g.status!=="playing"){RB=false;return}
 const p=g.players.find(x=>x.id===g.turn),r=1+Math.floor(Math.random()*6),frm=p.pos;
 let pos=frm+r,ev=null;
 if(pos<100){if(SNK.ladders[pos]){pos=SNK.ladders[pos];ev="ladder"}else if(SNK.snakes[pos]){pos=SNK.snakes[pos];ev="snake"}}
 else pos=100;
 p.pos=pos;g.seq++;g.last={seq:g.seq,pid:p.id,r:r,from:frm,to:pos,ev:ev,auto:false};
 g.log.push(p.name+" 🎲"+r+": "+frm+"→"+pos+(ev==="ladder"?" 🪜":ev==="snake"?" 🐍":""));g.log=g.log.slice(-8);
 if(pos>=100){g.status="done";g.winner=p.id}
 else if(r!==6)g.turn=(g.turn===1)?2:1;
 RB=false;applyG(g);
 if(g.status==="playing"&&g.turn===2)setTimeout(()=>{if(PON&&PG===g){RB=true;pRoll()}},(g.last&&g.last.ev?3200:1900))}

/* ---------- شروع ---------- */
(async()=>{
 const qp=new URLSearchParams(location.search);
 const sp=(tg&&tg.initDataUnsafe&&tg.initDataUnsafe.start_param)||"";
 const g0=parseInt(qp.get("g")||(sp.startsWith("snk_")?sp.slice(4):""),10);
 if(qp.get("tab")&&PANES[qp.get("tab")])VIEW=qp.get("tab");
 try{const s=await api("/api/me");renderAll(s);showTab(VIEW);
  if(g0)openGame(g0,false);else if(s.game&&sp==="snk")openGame(s.game.id,false)}
 catch(e){$("v").className="";$("v").innerHTML=`<div class="msg">${e.message}<br><br>مینی‌اپ رو از داخل ربات باز کن.</div>`}
})();
</script></body></html>
"""
