import os
import re
import sqlite3
import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from io import BytesIO
from datetime import datetime, timedelta
from bs4 import BeautifulSoup

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable not set")

FX_URL = "https://open.er-api.com/v6/latest/{}"
FRANKFURTER_URL = "https://api.frankfurter.app/{start}..{end}"
CRYPTO_URL = "https://api.coingecko.com/api/v3/simple/price"
DB_PATH = "bot_data.db"

CURRENCY_ALIASES = {
    "naira": "NGN", "dollar": "USD", "dollars": "USD", "usd": "USD",
    "euro": "EUR", "euros": "EUR", "pound": "GBP", "pounds": "GBP",
    "yen": "JPY", "cedis": "GHS", "rand": "ZAR", "ngn": "NGN",
}

def normalize_currency(word):
    word = word.lower().strip()
    if word in CURRENCY_ALIASES:
        return CURRENCY_ALIASES[word]
    return word.upper()

# ---------- Database setup ----------

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS subscriptions (
        chat_id INTEGER PRIMARY KEY
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER,
        base TEXT,
        target_currency TEXT,
        direction TEXT,
        target_value REAL
    )""")
    conn.commit()
    conn.close()

def db_execute(query, params=(), fetch=False):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(query, params)
    result = c.fetchall() if fetch else None
    conn.commit()
    conn.close()
    return result

# ---------- Helpers ----------

def get_rates(base="USD"):
    try:
        resp = requests.get(FX_URL.format(base), timeout=10).json()
    except Exception:
        return None
    return resp if resp.get("result") == "success" else None

def scrape_black_market_ngn():
    try:
        r = requests.get("https://abokiforex.app/", timeout=10,
                          headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(r.text, "html.parser")
        return soup.get_text()
    except Exception:
        return None

# ---------- Basic commands ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👋 Welcome to badmus67bot!\n\n"
        "/rates [BASE] - official exchange rates\n"
        "/naira <amount> <currency> - convert to ₦\n"
        "/convert <amount> <from> <to> - e.g. /convert 300 usd to naira\n"
        "/crypto - BTC/ETH price in USD & NGN\n"
        "/chart <from> <to> <days> - historical chart\n"
        "/subscribe - daily rate post here\n"
        "/unsubscribe - stop daily posts\n"
        "/alert <from> <to> <above|below> <value>\n"
        "/myalerts - list your active alerts\n"
        "/blackmarket - parallel market NGN rate (best-effort)\n"
    )
    await update.message.reply_text(msg)

async def rates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    base = context.args[0].upper() if context.args else "USD"
    resp = get_rates(base)
    if not resp:
        await update.message.reply_text(f"Couldn't find rates for '{base}'.")
        return
    r = resp["rates"]
    lines = [f"💱 Rates (base: {base})", f"Updated: {resp.get('time_last_update_utc')}", ""]
    for code in ["NGN", "EUR", "GBP", "JPY", "CAD"]:
        if code in r and code != base:
            lines.append(f"1 {base} = {r[code]:,.4f} {code}")
    await update.message.reply_text("\n".join(lines))

async def naira(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 2:
        await update.message.reply_text("Usage: /naira <amount> <currency>")
        return
    amount, frm = context.args
    frm = frm.upper()
    try:
        amount = float(amount)
    except ValueError:
        await update.message.reply_text("Amount must be a number.")
        return
    resp = get_rates(frm)
    if not resp or "NGN" not in resp["rates"]:
        await update.message.reply_text(f"Couldn't convert from '{frm}'.")
        return
    result = amount * resp["rates"]["NGN"]
    await update.message.reply_text(f"💵 {amount:,.2f} {frm} = ₦{result:,.2f}")

async def convert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args)
    if not text:
        await update.message.reply_text(
            "Tell me what to convert — examples:\n"
            "/convert 300 usd to naira\n"
            "/convert 300 USD NGN\n"
            "/convert 50 pounds to dollars"
        )
        return

    match = re.search(r"[\d,.]+", text)
    if not match:
        await update.message.reply_text("I couldn't find an amount. Try: /convert 300 usd to naira")
        return
    amount = float(match.group().replace(",", ""))

    words = re.sub(r"[\d,.]+", "", text).lower()
    words = [w for w in words.split() if w not in ("to", "in", "into")]

    if len(words) < 2:
        await update.message.reply_text("I need both currencies — e.g. /convert 300 usd to naira")
        return

    frm = normalize_currency(words[0])
    to = normalize_currency(words[1])

    resp = get_rates(frm)
    if not resp or to not in resp["rates"]:
        await update.message.reply_text(f"Couldn't find rate for {frm} → {to}. Check the currency names/codes.")
        return

    result = amount * resp["rates"][to]
    await update.message.reply_text(f"{amount:,.2f} {frm} = {result:,.2f} {to}")

async def crypto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    params = {"ids": "bitcoin,ethereum", "vs_currencies": "usd,ngn"}
    try:
        data = requests.get(CRYPTO_URL, params=params, timeout=10).json()
    except Exception:
        await update.message.reply_text("Couldn't reach crypto price service.")
        return
    lines = ["🪙 Crypto Prices", ""]
    for coin, label in [("bitcoin", "BTC"), ("ethereum", "ETH")]:
        if coin in data:
            lines.append(f"{label}: ${data[coin]['usd']:,.2f} | ₦{data[coin]['ngn']:,.2f}")
    await update.message.reply_text("\n".join(lines))

# ---------- Historical chart ----------

async def chart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 3:
        await update.message.reply_text("Usage: /chart <from> <to> <days>\nExample: /chart USD NGN 30")
        return
    frm, to, days = context.args
    frm, to = frm.upper(), to.upper()
    try:
        days = int(days)
    except ValueError:
        await update.message.reply_text("Days must be a number.")
        return

    end = datetime.utcnow().date()
    start = end - timedelta(days=days)
    url = FRANKFURTER_URL.format(start=start, end=end)
    try:
        resp = requests.get(url, params={"from": frm, "to": to}, timeout=10).json()
    except Exception:
        await update.message.reply_text("Couldn't fetch historical data.")
        return

    if "rates" not in resp:
        await update.message.reply_text(
            "Couldn't fetch historical data for that pair "
            "(Frankfurter only covers major fiat currencies via ECB)."
        )
        return

    dates = sorted(resp["rates"].keys())
    values = [resp["rates"][d][to] for d in dates]

    plt.figure(figsize=(8, 4))
    plt.plot(dates, values, marker="o", markersize=2)
    plt.title(f"{frm}/{to} — last {days} days")
    plt.xticks(rotation=45, fontsize=6)
    plt.tight_layout()

    buf = BytesIO()
    plt.savefig(buf, format="png")
    plt.close()
    buf.seek(0)

    await update.message.reply_photo(photo=buf, caption=f"{frm}/{to} over the last {days} days")

# ---------- Daily auto-post ----------

async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    db_execute("INSERT OR IGNORE INTO subscriptions (chat_id) VALUES (?)", (chat_id,))
    await update.message.reply_text("✅ Subscribed — you'll get a daily rate update at 7am UTC.")

async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    db_execute("DELETE FROM subscriptions WHERE chat_id = ?", (chat_id,))
    await update.message.reply_text("❌ Unsubscribed from daily updates.")

async def daily_post_job(context: ContextTypes.DEFAULT_TYPE):
    resp = get_rates("USD")
    if not resp:
        return
    r = resp["rates"]
    text = (
        f"🌅 Daily USD Rates\n"
        f"1 USD = ₦{r.get('NGN', 0):,.2f}\n"
        f"1 USD = €{r.get('EUR', 0):,.4f}\n"
        f"1 USD = £{r.get('GBP', 0):,.4f}"
    )
    rows = db_execute("SELECT chat_id FROM subscriptions", fetch=True)
    for (chat_id,) in rows:
        try:
            await context.bot.send_message(chat_id=chat_id, text=text)
        except Exception:
            db_execute("DELETE FROM subscriptions WHERE chat_id = ?", (chat_id,))

# ---------- Rate alerts ----------

async def alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 4:
        await update.message.reply_text("Usage: /alert <from> <to> <above|below> <value>")
        return
    frm, to, direction, target = context.args
    direction = direction.lower()
    if direction not in ("above", "below"):
        await update.message.reply_text("Direction must be 'above' or 'below'.")
        return
    try:
        target = float(target)
    except ValueError:
        await update.message.reply_text("Target value must be a number.")
        return

    chat_id = update.effective_chat.id
    db_execute(
        "INSERT INTO alerts (chat_id, base, target_currency, direction, target_value) VALUES (?, ?, ?, ?, ?)",
        (chat_id, frm.upper(), to.upper(), direction, target)
    )
    await update.message.reply_text(
        f"🔔 Alert set: notify when {frm.upper()}/{to.upper()} goes {direction} {target}"
    )

async def myalerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    rows = db_execute(
        "SELECT base, target_currency, direction, target_value FROM alerts WHERE chat_id = ?",
        (chat_id,), fetch=True
    )
    if not rows:
        await update.message.reply_text("You have no active alerts.")
        return
    lines = [f"{b}/{t} {d} {v}" for b, t, d, v in rows]
    await update.message.reply_text("Your alerts:\n" + "\n".join(lines))

async def check_alerts_job(context: ContextTypes.DEFAULT_TYPE):
    rows = db_execute(
        "SELECT id, chat_id, base, target_currency, direction, target_value FROM alerts",
        fetch=True
    )
    cache = {}
    for alert_id, chat_id, base, to, direction, target in rows:
        if base not in cache:
            cache[base] = get_rates(base)
        resp = cache[base]
        if not resp or to not in resp["rates"]:
            continue
        current = resp["rates"][to]
        triggered = (direction == "above" and current >= target) or \
                    (direction == "below" and current <= target)
        if triggered:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"🚨 Alert triggered: {base}/{to} = {current:,.2f} ({direction} {target})"
                )
            except Exception:
                pass
            db_execute("DELETE FROM alerts WHERE id = ?", (alert_id,))

# ---------- Black-market NGN rate (best-effort) ----------

async def blackmarket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = scrape_black_market_ngn()
    if not raw:
        await update.message.reply_text("Couldn't fetch black-market rate right now.")
        return
    await update.message.reply_text(
        "⚠️ No official free API exists for the parallel market rate — "
        "this feature needs manual tuning of the scraper for a specific source.\n"
        "Check https://abokiforex.app manually to confirm before relying on it."
    )

# ---------- App ----------

def main():
    init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("rates", rates))
    app.add_handler(CommandHandler("naira", naira))
    app.add_handler(CommandHandler("convert", convert))
    app.add_handler(CommandHandler("crypto", crypto))
    app.add_handler(CommandHandler("chart", chart))
    app.add_handler(CommandHandler("subscribe", subscribe))
    app.add_handler(CommandHandler("unsubscribe", unsubscribe))
    app.add_handler(CommandHandler("alert", alert))
    app.add_handler(CommandHandler("myalerts", myalerts))
    app.add_handler(CommandHandler("blackmarket", blackmarket))

    app.job_queue.run_daily(daily_post_job, time=datetime.strptime("07:00", "%H:%M").time())
    app.job_queue.run_repeating(check_alerts_job, interval=900, first=10)

    app.run_polling()

if __name__ == "__main__":
    main()
