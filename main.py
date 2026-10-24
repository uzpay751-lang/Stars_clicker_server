import os, json, hmac, hashlib, time, asyncio, sqlite3
from urllib.parse import parse_qsl
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from aiogram import Bot, Dispatcher, F
from aiogram.types import Update, LabeledPrice, InlineKeyboardMarkup, InlineKeyboardButton, PreCheckoutQuery, Message, CallbackQuery
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "8836465969"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "change-this-secret")
DB_PATH = os.getenv("DB_PATH", "stars_clicker.sqlite3")
WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip()
PRICE_40 = int(os.getenv("PRICE_40", "10"))  # Telegram Stars (XTR), configure before launch
PRICE_50 = int(os.getenv("PRICE_50", "15"))
PRICE_70 = int(os.getenv("PRICE_70", "20"))
MIN_WITHDRAW = int(os.getenv("MIN_WITHDRAW", "10"))

if not BOT_TOKEN:
    print("WARNING: BOT_TOKEN is missing. Set it in environment variables before starting.")

def db():
    con = sqlite3.connect(DB_PATH, timeout=20)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users(
          user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
          balance REAL NOT NULL DEFAULT 0, level INTEGER NOT NULL DEFAULT 1,
          clicks_today INTEGER NOT NULL DEFAULT 0, day TEXT NOT NULL DEFAULT '',
          daily_cap INTEGER NOT NULL DEFAULT 20, updated_at INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS payments(
          payload TEXT PRIMARY KEY, user_id INTEGER NOT NULL, package_cap INTEGER NOT NULL,
          amount_xtr INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
          telegram_charge_id TEXT, created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS withdrawals(
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
          username TEXT, amount INTEGER NOT NULL, note TEXT,
          status TEXT NOT NULL DEFAULT 'pending', created_at INTEGER NOT NULL,
          decided_at INTEGER, admin_note TEXT
        );
        CREATE TABLE IF NOT EXISTS transactions(
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
          kind TEXT NOT NULL, amount REAL NOT NULL, details TEXT, created_at INTEGER NOT NULL
        );
        """)
init_db()

def utc_day():
    return time.strftime("%Y-%m-%d", time.gmtime())

def verify_init_data(init_data: str) -> dict:
    if not BOT_TOKEN or not init_data:
        raise HTTPException(401, "Telegram Mini App auth required")
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = pairs.pop("hash", "")
        auth_date = int(pairs.get("auth_date", "0"))
        if not received_hash or abs(time.time() - auth_date) > 86400:
            raise ValueError("expired")
        check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calculated, received_hash):
            raise ValueError("bad hash")
        user = json.loads(pairs["user"])
        return user
    except Exception:
        raise HTTPException(401, "Invalid or expired Telegram initData")

def ensure_user(user: dict):
    uid = int(user["id"])
    today = utc_day()
    with db() as con:
        row = con.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
        if not row:
            con.execute("INSERT INTO users(user_id,username,first_name,day,updated_at) VALUES(?,?,?,?,?)",
                        (uid, user.get("username"), user.get("first_name"), today, int(time.time())))
        else:
            con.execute("UPDATE users SET username=?,first_name=?,updated_at=? WHERE user_id=?",
                        (user.get("username"), user.get("first_name"), int(time.time()), uid))
            if row["day"] != today:
                con.execute("UPDATE users SET clicks_today=0,day=?,daily_cap=CASE WHEN daily_cap IN (40,50,70) THEN 20 ELSE 20 END WHERE user_id=?",
                            (today, uid))
    with db() as con:
        return dict(con.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone())

def get_user(uid):
    with db() as con:
        row=con.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
        return dict(row) if row else None

def click_reward(level):
    return round(min(5.0, 0.10 + (level - 1) * 0.05), 2)

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML)) if BOT_TOKEN else None
dp = Dispatcher()

class ClickBody(BaseModel):
    pass
class UpgradeBody(BaseModel):
    pass
class WithdrawBody(BaseModel):
    amount: int = Field(ge=1, le=100000)
    note: str = Field(default="", max_length=200)

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = None
    if bot:
        task = asyncio.create_task(dp.start_polling(bot, handle_signals=False))
    yield
    if task:
        dp.stop_polling()
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass
        await bot.session.close()

app = FastAPI(title="Stars Clicker API", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])

def auth_user(authorization: str | None):
    if not authorization or not authorization.startswith("tma "):
        raise HTTPException(401, "Missing Telegram initData")
    return verify_init_data(authorization[4:])

@app.get("/")
def root():
    return {"app":"Stars Clicker API","status":"ok","telegram_bot_configured":bool(BOT_TOKEN)}

@app.get("/api/me")
def me(authorization: str | None = Header(default=None)):
    u=auth_user(authorization); row=ensure_user(u)
    return public_user(row)

def public_user(row):
    return {"user_id":row["user_id"],"username":row["username"],"first_name":row["first_name"],
            "balance":row["balance"],"level":row["level"],"clicks_today":row["clicks_today"],
            "daily_cap":row["daily_cap"],"clicks_left":max(0,row["daily_cap"]-row["clicks_today"]),
            "reward":click_reward(row["level"]),"max_level":20}

@app.post("/api/click")
def click(authorization: str | None = Header(default=None)):
    u=auth_user(authorization); row=ensure_user(u); uid=int(u["id"])
    if row["clicks_today"] >= row["daily_cap"]:
        raise HTTPException(429, "Daily click limit reached")
    reward=click_reward(row["level"])
    with db() as con:
        con.execute("UPDATE users SET balance=ROUND(balance+?,2), clicks_today=clicks_today+1,updated_at=? WHERE user_id=?",
                    (reward,int(time.time()),uid))
        con.execute("INSERT INTO transactions(user_id,kind,amount,details,created_at) VALUES(?,?,?,?,?)",
                    (uid,"click",reward,"click reward",int(time.time())))
        updated=dict(con.execute("SELECT * FROM users WHERE user_id=?",(uid,)).fetchone())
    return public_user(updated)

@app.post("/api/upgrade")
def upgrade(authorization: str | None = Header(default=None)):
    u=auth_user(authorization); row=ensure_user(u); uid=int(u["id"])
    if row["level"]>=20: raise HTTPException(400,"Maximum level reached")
    cost=round(25*(row["level"]**1.7),2)
    if row["balance"]<cost: raise HTTPException(400,"Not enough balance")
    with db() as con:
        con.execute("UPDATE users SET balance=ROUND(balance-?,2),level=level+1,updated_at=? WHERE user_id=?",(cost,int(time.time()),uid))
        con.execute("INSERT INTO transactions(user_id,kind,amount,details,created_at) VALUES(?,?,?,?,?)",(uid,"upgrade",-cost,f"level {row['level']} -> {row['level']+1}",int(time.time())))
        updated=dict(con.execute("SELECT * FROM users WHERE user_id=?",(uid,)).fetchone())
    return {"user":public_user(updated),"cost":cost}

@app.post("/api/withdraw")
async def withdraw(body: WithdrawBody, authorization: str | None = Header(default=None)):
    u=auth_user(authorization); row=ensure_user(u); uid=int(u["id"])
    if body.amount < MIN_WITHDRAW: raise HTTPException(400,f"Minimum withdrawal is {MIN_WITHDRAW} Stars")
    # Internal points are not automatically equivalent to real Telegram Stars; admin reviews every request.
    if row["balance"] < body.amount: raise HTTPException(400,"Insufficient game balance")
    with db() as con:
        cur=con.execute("INSERT INTO withdrawals(user_id,username,amount,note,created_at) VALUES(?,?,?,?,?)",
                        (uid,u.get("username"),body.amount,body.note,int(time.time())))
        req_id=cur.lastrowid
    if bot:
        kb=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Одобрить",callback_data=f"wd:approve:{req_id}"),
            InlineKeyboardButton(text="❌ Отклонить",callback_data=f"wd:reject:{req_id}")
        ]])
        try:
            await bot.send_message(ADMIN_ID,
                f"⭐ <b>Новая заявка Stars Clicker #{req_id}</b>\n"
                f"Пользователь: <code>{uid}</code> (@{u.get('username') or 'нет'})\n"
                f"Сумма: <b>{body.amount} ⭐</b>\nБаланс игры: {row['balance']:.2f}\n"
                f"Комментарий: {body.note or '—'}\n\n"
                "Проверь данные и выдай настоящие Stars вручную. Одобрение в боте не отправляет Stars автоматически.",
                reply_markup=kb)
        except Exception as e:
            raise HTTPException(502,"Request saved but admin notification failed")
    return {"ok":True,"request_id":req_id,"status":"pending"}

@app.post("/api/payments/invoice")
async def invoice(request: Request, authorization: str | None = Header(default=None)):
    if not bot: raise HTTPException(503,"Bot token not configured")
    u=auth_user(authorization); ensure_user(u)
    body=await request.json()
    cap=int(body.get("cap",0))
    prices={40:PRICE_40,50:PRICE_50,70:PRICE_70}
    if cap not in prices: raise HTTPException(400,"Invalid package")
    amount=prices[cap]
    payload=f"limit:{u['id']}:{cap}:{int(time.time())}"
    with db() as con:
        con.execute("INSERT INTO payments(payload,user_id,package_cap,amount_xtr,status,created_at) VALUES(?,?,?,?,?,?)",
                    (payload,int(u["id"]),cap,amount,"pending",int(time.time())))
    link=await bot.create_invoice_link(title=f"{cap} кликов в день",description=f"Увеличить дневной лимит до {cap} кликов на сегодня",
        payload=payload,currency="XTR",prices=[LabeledPrice(label=f"Лимит {cap} кликов",amount=amount)])
    return {"invoice_url":link,"amount_xtr":amount}

@dp.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    with db() as con:
        row=con.execute("SELECT * FROM payments WHERE payload=?",(q.invoice_payload,)).fetchone()
    if not row or row["status"]!="pending" or row["user_id"]!=q.from_user.id or row["amount_xtr"]!=q.total_amount or q.currency!="XTR":
        await q.answer(ok=False,error_message="Платёж не найден или уже обработан.")
    else:
        await q.answer(ok=True)

@dp.message(F.successful_payment)
async def successful_payment(message: Message):
    payment=message.successful_payment
    with db() as con:
        row=con.execute("SELECT * FROM payments WHERE payload=?",(payment.invoice_payload,)).fetchone()
        if not row or row["status"]!="pending": return
        if payment.currency!="XTR" or payment.total_amount!=row["amount_xtr"]: return
        # Purchased cap is active for the current UTC day; no balance is added by a payment.
        con.execute("UPDATE payments SET status='paid',telegram_charge_id=? WHERE payload=?",
                    (payment.telegram_payment_charge_id,payment.invoice_payload))
        con.execute("UPDATE users SET daily_cap=?,clicks_today=0,day=?,updated_at=? WHERE user_id=?",
                    (row["package_cap"],utc_day(),int(time.time()),row["user_id"]))
    await message.answer(f"✅ Оплата принята! Дневной лимит установлен: <b>{row['package_cap']} кликов</b>.")

@dp.callback_query(F.data.startswith("wd:"))
async def withdrawal_decision(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        await call.answer("Недостаточно прав", show_alert=True); return
    _,action,raw_id=call.data.split(":"); req_id=int(raw_id)
    with db() as con:
        row=con.execute("SELECT * FROM withdrawals WHERE id=?",(req_id,)).fetchone()
        if not row or row["status"]!="pending":
            await call.answer("Заявка уже обработана",show_alert=True); return
        status="approved" if action=="approve" else "rejected"
        con.execute("UPDATE withdrawals SET status=?,decided_at=? WHERE id=?",(status,int(time.time()),req_id))
    if action=="approve":
        await call.message.edit_text(call.message.text+"\n\n✅ <b>Одобрено администратором.</b>\nВажно: настоящие Stars нужно выдать вручную.")
        try: await bot.send_message(row["user_id"],f"✅ Заявка #{req_id} одобрена. Выдай/ожидай выдачу настоящих Stars от администратора.")
        except Exception: pass
    else:
        await call.message.edit_text(call.message.text+"\n\n❌ <b>Отклонено администратором.</b>")
        try: await bot.send_message(row["user_id"],f"❌ Заявка #{req_id} отклонена. Обратись к поддержке.")
        except Exception: pass
    await call.answer("Статус обновлён")

@dp.message(F.text == "/start")
async def start(message: Message):
    text="⭐ <b>Stars Clicker</b>\n\nОткрой Mini App и нажимай на звезду. Игровые очки не являются настоящими Telegram Stars."
    if WEBAPP_URL:
        kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Открыть Stars Clicker ⭐",web_app={"url":WEBAPP_URL})]])
        await message.answer(text,reply_markup=kb)
    else:
        await message.answer(text+"\n\nАдминистратор: настрой WEBAPP_URL в переменных окружения.")

def main():
    import uvicorn
    uvicorn.run("main:app",host="0.0.0.0",port=int(os.getenv("PORT","8000")))
if __name__=="__main__": main()
