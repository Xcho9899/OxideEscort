import logging
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ConversationHandler, MessageHandler, filters, ContextTypes
from datetime import datetime
import config
import asyncio

logging.basicConfig(level=logging.INFO)

AD_QUANTITY, AD_PRICE = range(2)
DEPOSIT_AMOUNT = 2
WITHDRAW_AMOUNT, WITHDRAW_ADDRESS = range(3, 5)

CATEGORIES = {
    "farm_sulfur": "⚒️ Фарм серы",
    "farm_metal": "🔩 Фарм металла",
    "farm_wood": "🪵 Фарм дерева",
    "build_base": "🏗️ Постройка базы",
    "farm_fuel": "⛽ Фарм топливо",
    "raid_help": "🛡️ Помощь в рейдах",
    "farm_scrap": "🔧 Фарм металалома",
    "install_turrets": "🔫 Установка турелей",
    "hide_cabinet": "🚪 Скидка шкафа",
}

offers_storage = {}
offer_counter = 1
user_wallet = {}
user_ratings = {}
user_history = {}

def cleanup_memory():
    global offers_storage, user_history
    if len(user_history) > 500:
        user_history.clear()
    if len(offers_storage) > 100:
        old_offers = list(offers_storage.keys())[:-50]
        for key in old_offers:
            del offers_storage[key]

def get_usdt_rub_rate():
    try:
        response = requests.get('https://api.coingecko.com/api/v3/simple/price?ids=tether&vs_currencies=rub', timeout=5)
        return response.json()['tether']['rub']
    except:
        return 81.0

def convert_usd_to_rub(amount_usd):
    rate = get_usdt_rub_rate()
    return round(amount_usd * rate, 2)

def get_main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛍️ Доска услуг", callback_data="board")],
        [InlineKeyboardButton("📊 Мои предложения", callback_data="my_offers")],
        [InlineKeyboardButton("📋 Мои сделки", callback_data="my_deals")],
        [InlineKeyboardButton("👤 Мой профиль", callback_data="profile")],
        [InlineKeyboardButton("💳 Кошелек", callback_data="wallet")],
        [InlineKeyboardButton("📜 История", callback_data="history")],
        [InlineKeyboardButton("❓ Помощь", callback_data="help")],
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_wallet:
        user_wallet[user_id] = 0
    if user_id not in user_ratings:
        user_ratings[user_id] = {"total_rating": 0, "count": 0, "deals": 0}
    if user_id not in user_history:
        user_history[user_id] = []
    
    cleanup_memory()
    
    await update.message.reply_text(
        "👋 *OxideEscort - Маркетплейс услуг*\n\n🎮 Oxide Survival Island\n💵 USDT TRC-20\n💰 Комиссия 5%",
        reply_markup=get_main_menu(),
        parse_mode="Markdown"
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global offer_counter
    
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data
    rate = get_usdt_rub_rate()
    
    cleanup_memory()
    
    if data == "board":
        keyboard = []
        for key, name in CATEGORIES.items():
            count = len([o for o in offers_storage.values() if o['category'] == key])
            keyboard.append([InlineKeyboardButton(f"{name} ({count})", callback_data=f"cat_{key}")])
        keyboard.append([InlineKeyboardButton("🏠 Меню", callback_data="return_main")])
        
        await query.edit_message_text(
            "🛍️ *Доска услуг*\n\nВыберите категорию:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    
    elif data.startswith("cat_"):
        category = data.replace("cat_", "")
        offers = [o for o in offers_storage.values() if o['category'] == category]
        
        if not offers:
            keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="board")]]
            await query.edit_message_text("📭 Предложений нет", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            keyboard = []
            for offer in offers:
                price_rub = convert_usd_to_rub(offer['price'])
                keyboard.append([InlineKeyboardButton(
                    f"💰 {offer['quantity']} {offer['unit']} = ${offer['price']} (≈{price_rub:.0f}р)",
                    callback_data=f"offer_{offer['id']}"
                )])
            keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="board")])
            
            await query.edit_message_text(
                f"📊 *{CATEGORIES[category]}*",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
    
    elif data.startswith("create_"):
        category = data.replace("create_", "")
        context.user_data['category'] = category
        await query.edit_message_text(f"📊 Введите количество для {CATEGORIES[category]}")
        return AD_QUANTITY
    
    elif data == "wallet":
        balance = user_wallet.get(user_id, 0)
        keyboard = [
            [InlineKeyboardButton("💵 Пополнить", callback_data="deposit")],
            [InlineKeyboardButton("🏠 Меню", callback_data="return_main")]
        ]
        
        await query.edit_message_text(
            f"💳 *Кошелек*\n\n💵 Баланс: {balance:.0f}р (${balance / rate:.2f})",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    
    elif data == "deposit":
        await query.edit_message_text("💵 Введите сумму в РУБЛЯХ:")
        return DEPOSIT_AMOUNT
    
    elif data == "my_offers":
        my_offers = [o for o in offers_storage.values() if o['author_id'] == user_id]
        
        if my_offers:
            text = "📊 *Мои предложения:*\n\n"
            for offer in my_offers:
                price_rub = convert_usd_to_rub(offer['price'])
                text += f"💰 {offer['quantity']} {offer['unit']} = ${offer['price']} (≈{price_rub:.0f}р)\n"
        else:
            text = "📭 Предложений нет\n\n"
        
        keyboard = []
        for key, name in CATEGORIES.items():
            keyboard.append([InlineKeyboardButton(f"➕ {name}", callback_data=f"create_{key}")])
        keyboard.append([InlineKeyboardButton("🏠 Меню", callback_data="return_main")])
        
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    
    elif data == "my_deals":
        await query.edit_message_text("📋 У вас нет сделок", reply_markup=get_main_menu())
    
    elif data == "profile":
        rating_data = user_ratings.get(user_id, {})
        avg_rating = rating_data.get('total_rating', 0) / max(rating_data.get('count', 1), 1)
        my_offers_count = len([o for o in offers_storage.values() if o['author_id'] == user_id])
        deals_count = rating_data.get('deals', 0)
        
        await query.edit_message_text(
            f"👤 *Профиль*\n\n⭐ Рейтинг: {avg_rating:.1f}/5\n📊 Сделок: {deals_count}\n📋 Предложений: {my_offers_count}",
            reply_markup=get_main_menu(),
            parse_mode="Markdown"
        )
    
    elif data == "history":
        transactions = user_history.get(user_id, [])
        
        if not transactions:
            await query.edit_message_text("📜 История пуста", reply_markup=get_main_menu())
        else:
            text = "📜 *История:*\n\n"
            for trans in transactions[-10:]:
                text += f"💰 {trans['description']}\n{trans['amount']:.0f}р\n\n"
            
            await query.edit_message_text(text, reply_markup=get_main_menu(), parse_mode="Markdown")
    
    elif data == "help":
        await query.edit_message_text(
            "❓ *Справка*\n\n1. Найди услугу\n2. Пополни баланс\n3. Создай предложение\n\n💰 Комиссия 5%\n💵 USDT TRC-20",
            reply_markup=get_main_menu(),
            parse_mode="Markdown"
        )
    
    elif data == "return_main":
        await query.edit_message_text("🎯 *Меню*", reply_markup=get_main_menu(), parse_mode="Markdown")

async def get_deposit_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount_rub = float(update.message.text)
        user_id = update.effective_user.id
        
        if amount_rub < 81:
            await update.message.reply_text("❌ Минимум 81р", reply_markup=get_main_menu())
            return DEPOSIT_AMOUNT
        
        user_wallet[user_id] = user_wallet.get(user_id, 0) + amount_rub
        user_history[user_id].append({'type': 'deposit', 'amount': amount_rub, 'description': 'Пополнение'})
        
        await update.message.reply_text(f"✅ Баланс пополнен на {amount_rub:.0f}р!", reply_markup=get_main_menu())
        return ConversationHandler.END
    except:
        await update.message.reply_text("❌ Ошибка!", reply_markup=get_main_menu())
        return DEPOSIT_AMOUNT

async def get_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        quantity = float(update.message.text)
        context.user_data['quantity'] = quantity
        await update.message.reply_text("💰 Введите цену в $")
        return AD_PRICE
    except:
        await update.message.reply_text("❌ Ошибка!", reply_markup=get_main_menu())
        return AD_QUANTITY

async def get_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global offer_counter
    
    try:
        price = float(update.message.text)
    except:
        await update.message.reply_text("❌ Ошибка!", reply_markup=get_main_menu())
        return AD_PRICE
    
    user = update.effective_user
    user_id = user.id
    username = user.username or user.first_name
    category = context.user_data.get('category')
    quantity = context.user_data.get('quantity')
    
    units = {
        "farm_sulfur": "серы",
        "farm_metal": "металла",
        "farm_wood": "дерева",
        "build_base": "базы",
        "farm_fuel": "топлива",
        "raid_help": "рейдов",
        "farm_scrap": "металалома",
        "install_turrets": "турелей",
        "hide_cabinet": "шкафов",
    }
    unit = units.get(category, "ед")
    
    offer_id = offer_counter
    offers_storage[offer_id] = {
        'id': offer_id,
        'category': category,
        'quantity': quantity,
        'unit': unit,
        'price': price,
        'author_id': user_id,
        'author': username
    }
    offer_counter += 1
    
    if user_id not in user_ratings:
        user_ratings[user_id] = {"total_rating": 0, "count": 0, "deals": 0}
    
    price_rub = convert_usd_to_rub(price)
    
    await update.message.reply_text(
        f"✅ *Создано!*\n\n{CATEGORIES[category]}\n📊 {quantity} {unit}\n💰 ${price} (≈{price_rub:.0f}р)",
        reply_markup=get_main_menu(),
        parse_mode="Markdown"
    )
    return ConversationHandler.END

def main():
    app = Application.builder().token(config.TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    
    deposit_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern="deposit")],
        states={
            DEPOSIT_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_deposit_amount)],
        },
        fallbacks=[CommandHandler("start", start), CallbackQueryHandler(button_handler)]
    )
    
    quantity_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern="create_")],
        states={
            AD_QUANTITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_quantity)],
            AD_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_price)],
        },
        fallbacks=[CommandHandler("start", start), CallbackQueryHandler(button_handler)]
    )
    
    app.add_handler(deposit_conv)
    app.add_handler(quantity_conv)
    app.add_handler(CallbackQueryHandler(button_handler))
    
    print("🚀 OxideEscort БОТ ЗАПУЩЕН!")
    print("✅ Автоудаление старых сообщений")
    print("💵 USDT TRC-20")
    app.run_polling()

if __name__ == '__main__':
    import asyncio
    try:
        asyncio.run(main())
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(main())
