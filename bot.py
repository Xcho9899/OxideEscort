import logging
import requests
import threading
import os
from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ConversationHandler, MessageHandler, filters, ContextTypes
from telegram.error import BadRequest
from datetime import datetime, timedelta
import config
import asyncio
import json
import re

logging.basicConfig(level=logging.INFO)

# Flask app для PORT
flask_app = Flask(__name__)

@flask_app.route('/')
def hello():
    return 'OxideEscort Bot is running!', 200

@flask_app.route('/webhook/cryptobot', methods=['POST'])
def webhook_cryptobot():
    """Обработчик вебхуков от CryptoBot"""
    try:
        data = request.get_json()
        logging.info(f"Webhook received: {data}")
        
        payload = data.get('payload', {})
        
        if payload and payload.get('status') == 'paid':
            invoice_id = payload.get('invoice_id')
            
            logging.info(f"Invoice {invoice_id} paid!")
            
            # Ищем счет в нашей памяти
            for inv_id, inv_data in invoices_map.items():
                if str(inv_id) == str(invoice_id) or inv_id == invoice_id:
                    user_id = inv_data['user_id']
                    amount_usd = inv_data['amount_usd']
                    amount_rub = convert_usd_to_rub(amount_usd)
                    
                    # Пополняем баланс
                    user_wallet[user_id] = user_wallet.get(user_id, 0) + amount_rub
                    user_history[user_id].append({
                        'type': 'deposit',
                        'amount': amount_rub,
                        'description': f'Платеж ${amount_usd} USD'
                    })
                    
                    invoices_map[inv_id]['status'] = 'paid'
                    logging.info(f"Payment confirmed for user {user_id}: +{amount_rub}р")
                    break
        
        return jsonify({'ok': True}), 200
    except Exception as e:
        logging.error(f"Webhook error: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500

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
invoices_map = {}

CRYPTOBOT_API = "https://pay.crypt.bot/api"
CRYPTOBOT_TOKEN = config.CRYPTO_BOT_TOKEN
INVOICE_TIMEOUT = 300  # 5 минут в секундах
MIN_WITHDRAW = 1  # Минимум для вывода $1

def cleanup_memory():
    global offers_storage, user_history, invoices_map
    if len(user_history) > 500:
        user_history.clear()
    if len(offers_storage) > 100:
        old_offers = list(offers_storage.keys())[:-50]
        for key in old_offers:
            del offers_storage[key]
    
    # Удаляем истекшие счета
    now = datetime.now()
    expired_invoices = []
    for invoice_id, invoice in invoices_map.items():
        created_at = invoice.get('created_at')
        if created_at and (now - created_at).total_seconds() > INVOICE_TIMEOUT:
            if invoice.get('status') != 'paid':
                expired_invoices.append(invoice_id)
                logging.info(f"Invoice {invoice_id} expired after 5 minutes")
    
    for invoice_id in expired_invoices:
        del invoices_map[invoice_id]

def get_usdt_rub_rate():
    try:
        response = requests.get('https://api.coingecko.com/api/v3/simple/price?ids=tether&vs_currencies=rub', timeout=5)
        return response.json()['tether']['rub']
    except:
        return 81.0

def convert_usd_to_rub(amount_usd):
    rate = get_usdt_rub_rate()
    return round(amount_usd * rate, 2)

def convert_rub_to_usd(amount_rub):
    rate = get_usdt_rub_rate()
    return round(amount_rub / rate, 2)

def create_cryptobot_invoice(amount_usd: float, description: str, user_id: int):
    """Создаёт счёт через CryptoBot API"""
    try:
        headers = {
            "Crypto-Pay-API-Token": CRYPTOBOT_TOKEN,
            "Content-Type": "application/json"
        }
        payload = {
            "amount": str(amount_usd),
            "fiat_currency": "USD",
            "asset": "USDT",
            "description": description,
            "expires_in": 3600
        }
        
        logging.info(f"Creating invoice: {payload}")
        
        response = requests.post(
            f"{CRYPTOBOT_API}/createInvoice",
            headers=headers,
            json=payload,
            timeout=10
        )
        
        logging.info(f"Response status: {response.status_code}")
        logging.info(f"Response: {response.text}")
        
        if response.status_code == 200:
            data = response.json()
            if data.get('ok'):
                invoice = data.get('result', {})
                invoice_id = invoice.get('invoice_id')
                pay_url = invoice.get('pay_url')
                
                invoices_map[invoice_id] = {
                    'user_id': user_id,
                    'amount_usd': amount_usd,
                    'status': 'pending',
                    'created_at': datetime.now()
                }
                
                logging.info(f"Invoice created: {invoice_id}")
                return pay_url, invoice_id
        return None, None
    except Exception as e:
        logging.error(f"Error creating invoice: {e}")
        return None, None

def check_invoice_paid(invoice_id: str):
    """Проверяет статус счёта"""
    try:
        headers = {
            "Crypto-Pay-API-Token": CRYPTOBOT_TOKEN,
            "Content-Type": "application/json"
        }
        
        response = requests.get(
            f"{CRYPTOBOT_API}/getInvoices?invoice_ids={invoice_id}",
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            if data.get('ok'):
                invoices = data.get('result', {}).get('items', [])
                if invoices:
                    invoice = invoices[0]
                    return invoice.get('status') == 'paid'
        return False
    except Exception as e:
        logging.error(f"Error checking invoice: {e}")
        return False

def transfer_usdt(amount_usd: float, address: str):
    """Отправляет USDT на адрес TRC-20 через CryptoBot"""
    try:
        headers = {
            "Crypto-Pay-API-Token": CRYPTOBOT_TOKEN,
            "Content-Type": "application/json"
        }
        payload = {
            "asset": "USDT",
            "amount": str(amount_usd),
            "address": address,
            "network": "tron"
        }
        
        logging.info(f"Transferring {amount_usd} USDT to {address}")
        
        response = requests.post(
            f"{CRYPTOBOT_API}/transfer",
            headers=headers,
            json=payload,
            timeout=10
        )
        
        logging.info(f"Transfer response status: {response.status_code}")
        logging.info(f"Transfer response: {response.text}")
        
        if response.status_code == 200:
            data = response.json()
            if data.get('ok'):
                logging.info(f"Transfer successful")
                return True, data.get('result', {})
        
        return False, response.text
    except Exception as e:
        logging.error(f"Error transferring USDT: {e}")
        return False, str(e)

def is_valid_trc20_address(address: str):
    """Проверяет валидность TRC-20 адреса"""
    if len(address) == 34 and address.startswith('T'):
        return True
    return False

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
        "👋 *OxideEscort - Маркетплейс услуг*\n\n🎮 Oxide Survival Island\n💵 USDT USD\n💰 Комиссия 5%",
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
        
        try:
            await query.edit_message_text(
                "🛍️ *Доска услуг*\n\nВыберите категорию:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
        except BadRequest as e:
            if "Message is not modified" not in str(e):
                raise e
    
    elif data.startswith("cat_"):
        category = data.replace("cat_", "")
        offers = [o for o in offers_storage.values() if o['category'] == category]
        
        if not offers:
            keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="board")]]
            try:
                await query.edit_message_text("📭 Предложений нет", reply_markup=InlineKeyboardMarkup(keyboard))
            except BadRequest as e:
                if "Message is not modified" not in str(e):
                    raise e
        else:
            keyboard = []
            for offer in offers:
                price_rub = convert_usd_to_rub(offer['price'])
                keyboard.append([InlineKeyboardButton(
                    f"💰 {offer['quantity']} {offer['unit']} = ${offer['price']} USD (≈{price_rub:.0f}р)",
                    callback_data=f"offer_{offer['id']}"
                )])
            keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="board")])
            
            try:
                await query.edit_message_text(
                    f"📊 *{CATEGORIES[category]}*",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown"
                )
            except BadRequest as e:
                if "Message is not modified" not in str(e):
                    raise e
    
    elif data.startswith("create_"):
        category = data.replace("create_", "")
        context.user_data['category'] = category
        try:
            await query.edit_message_text(f"📊 Введите количество для {CATEGORIES[category]}")
        except BadRequest as e:
            if "Message is not modified" not in str(e):
                raise e
        return AD_QUANTITY
    
    elif data == "wallet":
        balance = user_wallet.get(user_id, 0)
        balance_usd = balance / rate
        keyboard = [
            [InlineKeyboardButton("💳 Пополнить", callback_data="deposit")],
            [InlineKeyboardButton("💰 Вывести", callback_data="withdraw")],
            [InlineKeyboardButton("🏠 Меню", callback_data="return_main")]
        ]
        
        try:
            await query.edit_message_text(
                f"💳 *Кошелек*\n\n💵 Баланс: {balance:.0f}р (${balance_usd:.2f} USD)",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
        except BadRequest as e:
            if "Message is not modified" not in str(e):
                raise e
    
    elif data == "deposit":
        rate = get_usdt_rub_rate()
        keyboard = [
            [InlineKeyboardButton("🏠 Меню", callback_data="return_main")]
        ]
        try:
            await query.edit_message_text(
                f"💵 *Введите сумму в РУБЛЯХ*\n\nТекущий курс: 1 USD = {rate:.2f}р",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
        except BadRequest as e:
            if "Message is not modified" not in str(e):
                raise e
        return DEPOSIT_AMOUNT
    
    elif data == "withdraw":
        balance = user_wallet.get(user_id, 0)
        balance_usd = balance / rate
        
        if balance_usd < MIN_WITHDRAW:
            try:
                await query.edit_message_text(
                    f"❌ *Недостаточно средств!*\n\nМинимум для вывода: ${MIN_WITHDRAW} USD\nВаш баланс: ${balance_usd:.2f} USD",
                    reply_markup=get_main_menu()
                )
            except BadRequest as e:
                if "Message is not modified" not in str(e):
                    raise e
        else:
            try:
                await query.edit_message_text(
                    f"💰 *Введите сумму в USD*\n\nВаш баланс: ${balance_usd:.2f} USD\nМинимум: ${MIN_WITHDRAW} USD"
                )
            except BadRequest as e:
                if "Message is not modified" not in str(e):
                    raise e
            return WITHDRAW_AMOUNT
    
    elif data == "my_offers":
        my_offers = [o for o in offers_storage.values() if o['author_id'] == user_id]
        
        if my_offers:
            text = "📊 *Мои предложения:*\n\n"
            for offer in my_offers:
                price_rub = convert_usd_to_rub(offer['price'])
                text += f"💰 {offer['quantity']} {offer['unit']} = ${offer['price']} USD (≈{price_rub:.0f}р)\n"
        else:
            text = "📭 Предложений нет\n\n"
        
        keyboard = []
        for key, name in CATEGORIES.items():
            keyboard.append([InlineKeyboardButton(f"➕ {name}", callback_data=f"create_{key}")])
        keyboard.append([InlineKeyboardButton("🏠 Меню", callback_data="return_main")])
        
        try:
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        except BadRequest as e:
            if "Message is not modified" not in str(e):
                raise e
    
    elif data == "my_deals":
        try:
            await query.edit_message_text("📋 У вас нет сделок", reply_markup=get_main_menu())
        except BadRequest as e:
            if "Message is not modified" not in str(e):
                raise e
    
    elif data == "profile":
        rating_data = user_ratings.get(user_id, {})
        avg_rating = rating_data.get('total_rating', 0) / max(rating_data.get('count', 1), 1)
        my_offers_count = len([o for o in offers_storage.values() if o['author_id'] == user_id])
        deals_count = rating_data.get('deals', 0)
        
        try:
            await query.edit_message_text(
                f"👤 *Профиль*\n\n⭐ Рейтинг: {avg_rating:.1f}/5\n📊 Сделок: {deals_count}\n📋 Предложений: {my_offers_count}",
                reply_markup=get_main_menu(),
                parse_mode="Markdown"
            )
        except BadRequest as e:
            if "Message is not modified" not in str(e):
                raise e
    
    elif data == "history":
        transactions = user_history.get(user_id, [])
        
        if not transactions:
            try:
                await query.edit_message_text("📜 История пуста", reply_markup=get_main_menu())
            except BadRequest as e:
                if "Message is not modified" not in str(e):
                    raise e
        else:
            text = "📜 *История:*\n\n"
            for trans in transactions[-10:]:
                text += f"💰 {trans['description']}\n"
            
            try:
                await query.edit_message_text(text, reply_markup=get_main_menu(), parse_mode="Markdown")
            except BadRequest as e:
                if "Message is not modified" not in str(e):
                    raise e
    
    elif data == "help":
        try:
            await query.edit_message_text(
                "❓ *Справка*\n\n1. Найди услугу\n2. Пополни баланс в рублях\n3. Создай предложение\n\n💰 Комиссия 5%\n💵 USDT USD",
                reply_markup=get_main_menu(),
                parse_mode="Markdown"
            )
        except BadRequest as e:
            if "Message is not modified" not in str(e):
                raise e
    
    elif data == "return_main":
        try:
            await query.edit_message_text("🎯 *Меню*", reply_markup=get_main_menu(), parse_mode="Markdown")
        except BadRequest as e:
            if "Message is not modified" not in str(e):
                raise e

async def get_deposit_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount_rub = float(update.message.text)
        user_id = update.effective_user.id
        rate = get_usdt_rub_rate()
        
        if amount_rub < 81:
            await update.message.reply_text("❌ Минимум 81р (~$1 USD)!", reply_markup=get_main_menu())
            return DEPOSIT_AMOUNT
        
        amount_usd = convert_rub_to_usd(amount_rub)
        
        pay_url, invoice_id = create_cryptobot_invoice(amount_usd, f"Пополнение OxideEscort", user_id)
        
        if pay_url:
            keyboard = [
                [InlineKeyboardButton("💳 Оплатить через CryptoBot", url=pay_url)],
                [InlineKeyboardButton("✅ Проверить платеж", callback_data=f"check_{invoice_id}")],
                [InlineKeyboardButton("🏠 Меню", callback_data="return_main")]
            ]
            
            await update.message.reply_text(
                f"💵 *Счет создан!*\n\n📥 Вы ввели: {amount_rub:.0f}р\n📤 К оплате: ${amount_usd} USD\n📊 Курс: 1 USD = {rate:.2f}р\n⏱️ Счет действителен 5 минут\n\nНажми 'Оплатить' для перевода через CryptoBot",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
            return ConversationHandler.END
        else:
            await update.message.reply_text("❌ Ошибка создания счета!", reply_markup=get_main_menu())
            return DEPOSIT_AMOUNT
    except ValueError:
        await update.message.reply_text("❌ Ошибка! Введите число в рублях!", reply_markup=get_main_menu())
        return DEPOSIT_AMOUNT

async def get_withdraw_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount_usd = float(update.message.text)
        user_id = update.effective_user.id
        rate = get_usdt_rub_rate()
        balance = user_wallet.get(user_id, 0)
        balance_usd = balance / rate
        
        if amount_usd < MIN_WITHDRAW:
            await update.message.reply_text(f"❌ Минимум ${MIN_WITHDRAW} USD!", reply_markup=get_main_menu())
            return WITHDRAW_AMOUNT
        
        if amount_usd > balance_usd:
            await update.message.reply_text(f"❌ Недостаточно средств!\n\nВаш баланс: ${balance_usd:.2f} USD", reply_markup=get_main_menu())
            return WITHDRAW_AMOUNT
        
        context.user_data['withdraw_amount'] = amount_usd
        await update.message.reply_text("💰 Введите TRC-20 адрес кошелька\n\n(адрес начинается с 'T')")
        return WITHDRAW_ADDRESS
    except ValueError:
        await update.message.reply_text("❌ Ошибка! Введите число в USD!", reply_markup=get_main_menu())
        return WITHDRAW_AMOUNT

async def get_withdraw_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        address = update.message.text.strip()
        user_id = update.effective_user.id
        amount_usd = context.user_data.get('withdraw_amount')
        rate = get_usdt_rub_rate()
        
        # Проверяем валидность адреса
        if not is_valid_trc20_address(address):
            await update.message.reply_text("❌ Неверный TRC-20 адрес!\n\nАдрес должен начинаться с 'T' и содержать 34 символа", reply_markup=get_main_menu())
            return WITHDRAW_ADDRESS
        
        # Отправляем USDT через CryptoBot
        success, result = transfer_usdt(amount_usd, address)
        
        if success:
            # Уменьшаем баланс
            amount_rub = convert_usd_to_rub(amount_usd)
            user_wallet[user_id] = user_wallet.get(user_id, 0) - amount_rub
            
            # Добавляем в историю
            user_history[user_id].append({
                'type': 'withdraw',
                'amount': amount_rub,
                'description': f'Вывод ${amount_usd} USD на {address[:10]}...'
            })
            
            await update.message.reply_text(
                f"✅ *Транзакция прошла успешно!*\n\n💰 Отправлено: ${amount_usd} USD\n📍 На адрес: {address}\n\nБаланс обновлен!",
                reply_markup=get_main_menu(),
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                f"❌ *Ошибка при отправке!*\n\n{result}\n\nПопробуйте позже.",
                reply_markup=get_main_menu(),
                parse_mode="Markdown"
            )
        
        return ConversationHandler.END
    except Exception as e:
        logging.error(f"Withdraw error: {e}")
        await update.message.reply_text("❌ Ошибка!", reply_markup=get_main_menu())
        return WITHDRAW_ADDRESS

async def check_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    callback_data = query.data
    invoice_id = callback_data.replace("check_", "")
    
    # Проверяем не истек ли счет
    if invoice_id not in invoices_map:
        try:
            await query.edit_message_text(
                "❌ *Счет истек!*\n\nВремя на оплату составляет 5 минут. Создайте новый счет.",
                reply_markup=get_main_menu()
            )
        except BadRequest as e:
            if "Message is not modified" not in str(e):
                raise e
        return
    
    invoice = invoices_map[invoice_id]
    
    if invoice.get('status') == 'paid':
        try:
            await query.edit_message_text(
                f"✅ *Платеж уже обработан!*\n\n💰 +${invoice['amount_usd']} USD\n💵 +{convert_usd_to_rub(invoice['amount_usd']):.0f}р\n\nПроверьте баланс в Кошельке!",
                reply_markup=get_main_menu()
            )
        except BadRequest as e:
            if "Message is not modified" not in str(e):
                raise e
        return
    
    is_paid = check_invoice_paid(invoice_id)
    
    if is_paid:
        user_id = invoice['user_id']
        amount_usd = invoice['amount_usd']
        amount_rub = convert_usd_to_rub(amount_usd)
        
        user_wallet[user_id] = user_wallet.get(user_id, 0) + amount_rub
        user_history[user_id].append({
            'type': 'deposit',
            'amount': amount_rub,
            'description': f'Платеж ${amount_usd} USD'
        })
        
        invoices_map[invoice_id]['status'] = 'paid'
        
        try:
            await query.edit_message_text(
                f"✅ *Платеж успешен!*\n\n💰 +${amount_usd} USD\n💵 +{amount_rub:.0f}р",
                reply_markup=get_main_menu()
            )
        except BadRequest as e:
            if "Message is not modified" not in str(e):
                raise e
    else:
        try:
            await query.edit_message_text(
                "⏳ *Платеж еще не поступил*\n\nПожалуйста подождите или проверьте позже.",
                reply_markup=get_main_menu()
            )
        except BadRequest as e:
            if "Message is not modified" not in str(e):
                raise e

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
        f"✅ *Создано!*\n\n{CATEGORIES[category]}\n📊 {quantity} {unit}\n💰 ${price} USD (≈{price_rub:.0f}р)",
        reply_markup=get_main_menu(),
        parse_mode="Markdown"
    )
    return ConversationHandler.END

def run_flask():
    """Запуск Flask в отдельном потоке"""
    port = int(os.environ.get('PORT', 8080))
    flask_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

def main():
    # Запуск Flask в фоновом потоке
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print(f"🌐 Flask запущен на порту {os.environ.get('PORT', 8080)}")
    
    # Основной бот
    app = Application.builder().token(config.TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    
    deposit_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern="deposit")],
        states={
            DEPOSIT_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_deposit_amount)],
        },
        fallbacks=[CommandHandler("start", start), CallbackQueryHandler(button_handler)]
    )
    
    withdraw_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern="withdraw")],
        states={
            WITHDRAW_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_withdraw_amount)],
            WITHDRAW_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_withdraw_address)],
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
    app.add_handler(withdraw_conv)
    app.add_handler(quantity_conv)
    app.add_handler(CallbackQueryHandler(check_payment, pattern="check_"))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    print("🚀 OxideEscort БОТ ЗАПУЩЕН!")
    print("✅ CryptoBot API + Webhooks")
    print("💵 Пополнение + Вывод")
    print("⏱️ Счета истекают через 5 минут")
    app.run_polling()

if __name__ == '__main__':
    import asyncio
    try:
        asyncio.run(main())
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(main())
