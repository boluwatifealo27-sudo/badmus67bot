import re

CURRENCY_ALIASES = {
    "naira": "NGN", "dollar": "USD", "dollars": "USD", "usd": "USD",
    "euro": "EUR", "euros": "EUR", "pound": "GBP", "pounds": "GBP",
    "yen": "JPY", "cedis": "GHS", "rand": "ZAR", "ngn": "NGN",
}

def normalize_currency(word):
    word = word.lower().strip()
    if word in CURRENCY_ALIASES:
        return CURRENCY_ALIASES[word]
    return word.upper()  # assume it's already a 3-letter code like USD, GBP

async def convert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Join all args back into one string so we can parse loosely
    text = " ".join(context.args)
    if not text:
        await update.message.reply_text(
            "Tell me what to convert — examples:\n"
            "/convert 300 usd to naira\n"
            "/convert 300 USD NGN\n"
            "/convert 50 pounds to dollars"
        )
        return

    # Extract the number
    match = re.search(r"[\d,.]+", text)
    if not match:
        await update.message.reply_text("I couldn't find an amount. Try: /convert 300 usd to naira")
        return
    amount = float(match.group().replace(",", ""))

    # Extract remaining words (currencies), ignoring "to"/"in"
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
