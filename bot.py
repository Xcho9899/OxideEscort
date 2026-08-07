import logging
import requests
import os
import json
import asyncio
from aiohttp import web
from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import Update, Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest
from datetime import datetime, timedelta
import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = config.TELEGRAM_TOKEN
CRYPTOBOT_API = "https://pay.crypt.bot/api"
CRYPTOBOT_TOKEN = config.CRYPTO_BOT_TOKEN

WEBHOOK_URL = "/webhook/telegram"
WEBHOOK_HOST = "oxideescort-3.onrender.com"
BASE_WEBHOOK_URL = f"https://{WEBHOOK_HOST}"
WEB_SERVER_HOST = "0.0.0.0"
WEB_SERVER_PORT = int(os.environ.get('PORT', 8080))

INVOICE_TIMEOUT = 300
MIN_WITHDRAW = 1

class DepositStates(StatesGroup):
    amount = State()

class WithdrawStates(StatesGroup):
    amount = State()
    address = State()

class OfferStates(StatesGroup):
    category = State()
    quantity = State()
    price = State()

invoices_map = {}
offers_storage = {}
offer_counter = 1
user_wallet = {}
user_ratings = {}
user_history = {}

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

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
dp.include_router(router)

def save_invoice_user_map(invoice_id, user_id):
    try:
        data = {}
        if os.path.exists('invoice_users.json'):
            with open('invoice_users.json', 'r') as f:
                data = json.load(f)
        data[str(invoice_id)] = user_id
        with open('invoice_users.json', 'w') as f:
            json.dump(data, f)
    except:
        pass

def load_invoice_user(invoice_id):
    try:
        if os.path.exists('invoice_users.json'):
            with open('invoice_users.json', 'r') as f:
                data = json.load(f)
                return data.get(str(invoice_id))
    except:
        pass
    return None

def save_invoices():
    try:
        data = {}
        for k, v in invoices_map.items():
            data[str(k)] = {
                'user_id': v.get('user_id'),
                'amount_usd': v.get('amount_usd'),
                'status': v.get('status'),
                'created_at': v.get('created_at').isoformat() if isinstance(v.get('created_at'), datetime) else v.get('created_at')
            }
        with open('invoices.json', 'w') as f:
            json.dump(data, f)
    except Exception as e:
        logger.error(f"Error saving invoices: {e}")

def load_invoices():
    try:
        if os.path.exists('invoices.json'):
            with open('invoices.json', 'r') as f:
                data = json.load(f)
                loaded = {}
                for k, v in data.items():
                    try:
                        loaded[int(k)] = {
                            'user_id': v.get('user_id'),
                            'amount_usd': v.get('amount_usd'),
                            'status': v.get('status'),
                            'created_at': datetime.fromisoformat(v.get('created_at')) if v.get('created_at') else datetime.now()
                        }
                    except:
                        pass
                return loaded
    except:
        pass
    return {}

def cleanup_memory():
    global offers_storage, user_history, invoices_map
    if len(user_history) > 500:
        user_history.clear()
    if len(offers_storage) > 100:
        old_offers = list(offers_storage.keys())[:-50]
        for key in old_offers:
            del offers_storage[key]
    now = datetime.now()
    expired_invoices = []
    for invoice_id, invoice in invoices_map.items():
        created_at = invoice.get('created_at')
        if created_at and (now - created_at).total_seconds() > INVOICE_TIMEOUT:
            if invoice.get('status') != 'paid':
                expired_invoices.append(invoice_id)
    for invoice_id in expired_invoices:
        del invoices_map[invoice_id]
    if expired_invoices:
        save_invoices()

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

def user_has_offer_in_category(user_id, category):
    for offer in offers_storage.values():
        if offer['author_id'] == user_id and offer['category'] == category:
            return True
    return False

def create_cryptobot_invoice(amount_usd: float, description: str, user_id: int):
    try:
        headers = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN, "Content-Type": "application/json"}
        payload = {"amount": str(amount_usd), "fiat_currency": "USD", "asset": "USDT", "description": description, "expires_in": 3600}
        response = requests.post(f"{CRYPTOBOT_API}/createInvoice", headers=headers, json=payload, timeout=10)
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
                save_invoices()
                save_invoice_user_map(invoice_id, user_id)
                logger.info(f"Invoice created: {invoice_id}")
                return pay_url, invoice_id
        return None, None
    except Exception as e:
        logger.error(f"Error creating invoice: {e}")
        return None, None

def check_invoice_paid(invoice_id: str):
    try:
        headers = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN, "Content-Type": "application/json"}
        response = requests.get(f"{CRYPTOBOT_API}/getInvoices?invoice_ids={invoice_id}", headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get('ok'):
                invoices = data.get('result', {}).get('items', [])
                if invoices:
                    return invoices[0].get('status') == 'paid'
        return False
    except Exception as e:
        logger.error(f"Error checking invoice: {e}")
        return False

def transfer_usdt(amount_usd: float, address: str):
    try:
        headers = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN, "Content-Type": "application/json"}
        payload = {"asset": "USDT", "amount": str(amount_usd), "address": address, "network": "tron"}
        response = requests.post(f"{CRYPTOBOT_API}/transfer", headers=headers, json=payload, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get('ok'):
                return True, data.get('result', {})
        return False, response.text
    except Exception as e:
        logger.error(f"Error transferring USDT: {e}")
        return False, str(e)

def is_valid_trc20_address(address: str):
    if len(address) == 34 and address.startswith('T'):
        return True
    return False

def get_main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛍️ Доска услуг", callback_data="board")],
        [InlineKeyboardButton(text="📊 Мои предложения", callback_data="my_offers")],
        [InlineKeyboardButton(text="📋 Мои сделки", callback_data="my_deals")],
        [InlineKeyboardButton(text="👤 Мой профиль", callback_data="profile")],
        [InlineKeyboardButton(text="💳 Кошелек", callback_data="wallet")],
        [InlineKeyboardButton(text="📜 История", callback_data="history")],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="help")],
    ])

@router.message(CommandStart())
async def start(message: Message):
    user_id = message.from_user.id
    if user_id not in user_wallet:
        user_wallet[user_id] = 0
    if user_id not in user_ratings:
        user_ratings[user_id] = {"total_rating": 0, "count": 0, "deals": 0}
    if user_id not in user_history:
        user_history[user_id] = []
    cleanup_memory()
    await message.answer("👋 <b>OxideEscort - Маркетплейс услуг</b>\n\n🎮 Oxide Survival Island\n💵 USDT USD\n💰 Комиссия 5%", reply_markup=get_main_menu())

@router.callback_query(F.data == "board")
async def board_handler(query: CallbackQuery):
    await query.answer()
    keyboard = []
    for key, name in CATEGORIES.items():
        count = len([o for o in offers_storage.values() if o['category'] == key])
        keyboard.append([InlineKeyboardButton(text=f"{name} ({count})", callback_data=f"cat_{key}")])
    keyboard.append([InlineKeyboardButton(text="🏠 Меню", callback_data="return_main")])
    try:
        await query.message.edit_text("🛍️ <b>Доска услуг</b>\n\nВыберите категорию:", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise e

@router.callback_query(F.data.startswith("cat_"))
async def category_handler(query: CallbackQuery):
    await query.answer()
    category = query.data.replace("cat_", "")
    offers = [o for o in offers_storage.values() if o['category'] == category]
    if not offers:
        keyboard = [[InlineKeyboardButton(text="⬅️ Назад", callback_data="board")]]
        try:
            await query.message.edit_text("📭 Предложений нет", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise e
    else:
        keyboard = []
        for offer in offers:
            price_rub = convert_usd_to_rub(offer['price'])
            keyboard.append([InlineKeyboardButton(text=f"💰 {offer['quantity']} {offer['unit']} = ${offer['price']} USD (≈{price_rub:.0f}р)", callback_data=f"offer_{offer['id']}")])
        keyboard.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="board")])
        try:
            await query.message.edit_text(f"📊 <b>{CATEGORIES[category]}</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise e

@router.callback_query(F.data == "wallet")
async def wallet_handler(query: CallbackQuery):
    await query.answer()
    user_id = query.from_user.id
    rate = get_usdt_rub_rate()
    balance = user_wallet.get(user_id, 0)
    balance_usd = balance / rate
    keyboard = [[InlineKeyboardButton(text="💳 Пополнить", callback_data="deposit")], [InlineKeyboardButton(text="💰 Вывести", callback_data="withdraw")], [InlineKeyboardButton(text="🏠 Меню", callback_data="return_main")]]
    try:
        await query.message.edit_text(f"💳 <b>Кошелек</b>\n\n💵 Баланс: {balance:.0f}р (${balance_usd:.2f} USD)", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise e

@router.callback_query(F.data == "deposit")
async def deposit_start(query: CallbackQuery, state: FSMContext):
    await query.answer()
    rate = get_usdt_rub_rate()
    keyboard = [[InlineKeyboardButton(text="🏠 Меню", callback_data="return_main")]]
    try:
        await query.message.edit_text(f"💵 <b>Введите сумму в РУБЛЯХ</b>\n\nТекущий курс: 1 USD = {rate:.2f}р", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise e
    await state.set_state(DepositStates.amount)

@router.message(DepositStates.amount)
async def deposit_amount(message: Message, state: FSMContext):
    try:
        amount_rub = float(message.text)
        user_id = message.from_user.id
        rate = get_usdt_rub_rate()
        if amount_rub < 81:
            await message.answer("❌ Минимум 81р (~$1 USD)!", reply_markup=get_main_menu())
            return
        amount_usd = convert_rub_to_usd(amount_rub)
        pay_url, invoice_id = create_cryptobot_invoice(amount_usd, f"Пополнение OxideEscort", user_id)
        if pay_url:
            keyboard = [[InlineKeyboardButton(text="💳 Оплатить через CryptoBot", url=pay_url)], [InlineKeyboardButton(text="✅ Проверить платеж", callback_data=f"check_{invoice_id}")], [InlineKeyboardButton(text="🏠 Меню", callback_data="return_main")]]
            await message.answer(f"💵 <b>Счет создан!</b>\n\n📥 Вы ввели: {amount_rub:.0f}р\n📤 К оплате: ${amount_usd} USD\n📊 Курс: 1 USD = {rate:.2f}р\n⏱️ Счет действителен 5 минут", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
            await state.clear()
        else:
            await message.answer("❌ Ошибка создания счета!", reply_markup=get_main_menu())
    except ValueError:
        await message.answer("❌ Ошибка! Введите число в рублях!", reply_markup=get_main_menu())

@router.callback_query(F.data == "withdraw")
async def withdraw_start(query: CallbackQuery, state: FSMContext):
    await query.answer()
    user_id = query.from_user.id
    rate = get_usdt_rub_rate()
    balance = user_wallet.get(user_id, 0)
    balance_usd = balance / rate
    if balance_usd < MIN_WITHDRAW:
        try:
            await query.message.edit_text(f"❌ <b>Недостаточно средств!</b>\n\nМинимум для вывода: ${MIN_WITHDRAW} USD\nВаш баланс: ${balance_usd:.2f} USD", reply_markup=get_main_menu())
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise e
    else:
        try:
            await query.message.edit_text(f"💰 <b>Введите сумму в USD</b>\n\nВаш баланс: ${balance_usd:.2f} USD\nМинимум: ${MIN_WITHDRAW} USD")
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise e
        await state.set_state(WithdrawStates.amount)

@router.message(WithdrawStates.amount)
async def withdraw_amount(message: Message, state: FSMContext):
    try:
        amount_usd = float(message.text)
        user_id = message.from_user.id
        rate = get_usdt_rub_rate()
        balance = user_wallet.get(user_id, 0)
        balance_usd = balance / rate
        if amount_usd < MIN_WITHDRAW:
            await message.answer(f"❌ Минимум ${MIN_WITHDRAW} USD!", reply_markup=get_main_menu())
            return
        if amount_usd > balance_usd:
            await message.answer(f"❌ Недостаточно средств!\n\nВаш баланс: ${balance_usd:.2f} USD", reply_markup=get_main_menu())
            return
        await state.update_data(withdraw_amount=amount_usd)
        await message.answer("💰 Введите TRC-20 адрес кошелька\n\n(адрес начинается с 'T')")
        await state.set_state(WithdrawStates.address)
    except ValueError:
        await message.answer("❌ Ошибка! Введите число в USD!", reply_markup=get_main_menu())

@router.message(WithdrawStates.address)
async def withdraw_address(message: Message, state: FSMContext):
    try:
        address = message.text.strip()
        user_id = message.from_user.id
        data = await state.get_data()
        amount_usd = data.get('withdraw_amount')
        if not is_valid_trc20_address(address):
            await message.answer("❌ Неверный TRC-20 адрес!\n\nАдрес должен начинаться с 'T' и содержать 34 символа", reply_markup=get_main_menu())
            return
        success, result = transfer_usdt(amount_usd, address)
        if success:
            amount_rub = convert_usd_to_rub(amount_usd)
            user_wallet[user_id] = user_wallet.get(user_id, 0) - amount_rub
            user_history[user_id].append({'type': 'withdraw', 'amount': amount_rub, 'description': f'Вывод ${amount_usd} USD на {address[:10]}...'})
            await message.answer(f"✅ <b>Транзакция прошла успешно!</b>\n\n💰 Отправлено: ${amount_usd} USD\n📍 На адрес: {address}\n\nБаланс обновлен!", reply_markup=get_main_menu())
        else:
            await message.answer(f"❌ <b>Ошибка при отправке!</b>\n\n{result}\n\nПопробуйте позже.", reply_markup=get_main_menu())
        await state.clear()
    except Exception as e:
        logger.error(f"Withdraw error: {e}")
        await message.answer("❌ Ошибка!", reply_markup=get_main_menu())

@router.callback_query(F.data.startswith("check_"))
async def check_payment(query: CallbackQuery):
    await query.answer()
    invoice_id = query.data.replace("check_", "")
    if invoice_id not in invoices_map:
        try:
            await query.message.edit_text("❌ <b>Счет истек!</b>\n\nВремя на оплату составляет 5 минут. Создайте новый счет.", reply_markup=get_main_menu())
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise e
        return
    invoice = invoices_map[invoice_id]
    if invoice.get('status') == 'paid':
        try:
            await query.message.edit_text(f"✅ <b>Платеж уже обработан!</b>\n\n💰 +${invoice['amount_usd']} USD\n💵 +{convert_usd_to_rub(invoice['amount_usd']):.0f}р\n\nПроверьте баланс в Кошельке!", reply_markup=get_main_menu())
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise e
        return
    is_paid = check_invoice_paid(invoice_id)
    if is_paid:
        user_id = invoice['user_id']
        amount_usd = invoice['amount_usd']
        amount_rub = convert_usd_to_rub(amount_usd)
        user_wallet[user_id] = user_wallet.get(user_id, 0) + amount_rub
        user_history[user_id].append({'type': 'deposit', 'amount': amount_rub, 'description': f'Платеж ${amount_usd} USD'})
        invoices_map[invoice_id]['status'] = 'paid'
        save_invoices()
        try:
            await query.message.edit_text(f"✅ <b>Платеж успешен!</b>\n\n💰 +${amount_usd} USD\n💵 +{amount_rub:.0f}р", reply_markup=get_main_menu())
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise e
    else:
        try:
            await query.message.edit_text("⏳ <b>Платеж еще не поступил</b>\n\nПожалуйста подождите или проверьте позже.", reply_markup=get_main_menu())
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise e

@router.callback_query(F.data == "my_offers")
async def my_offers_handler(query: CallbackQuery):
    await query.answer()
    user_id = query.from_user.id
    my_offers = [o for o in offers_storage.values() if o['author_id'] == user_id]
    if my_offers:
        text = "📊 <b>Мои предложения:</b>\n\n"
        for offer in my_offers:
            price_rub = convert_usd_to_rub(offer['price'])
            text += f"💰 {offer['quantity']} {offer['unit']} = ${offer['price']} USD (≈{price_rub:.0f}р)\n"
        keyboard = []
        for offer in my_offers:
            keyboard.append([InlineKeyboardButton(text=f"❌ Отменить #{offer['id']}", callback_data=f"cancel_{offer['id']}")])
        my_categories = set(offer['category'] for offer in my_offers)
        available_cats = [key for key in CATEGORIES.keys() if key not in my_categories]
        if available_cats:
            keyboard.append([InlineKeyboardButton(text="➕ Новое:", callback_data="none")])
            for key in available_cats:
                keyboard.append([InlineKeyboardButton(text=f"  {CATEGORIES[key]}", callback_data=f"create_{key}")])
        keyboard.append([InlineKeyboardButton(text="🏠 Меню", callback_data="return_main")])
    else:
        text = "📭 Предложений нет\n\n"
        keyboard = []
        for key, name in CATEGORIES.items():
            keyboard.append([InlineKeyboardButton(text=f"➕ {name}", callback_data=f"create_{key}")])
        keyboard.append([InlineKeyboardButton(text="🏠 Меню", callback_data="return_main")])
    try:
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise e

@router.callback_query(F.data.startswith("create_"))
async def create_offer_start(query: CallbackQuery, state: FSMContext):
    await query.answer()
    category = query.data.replace("create_", "")
    user_id = query.from_user.id
    if user_has_offer_in_category(user_id, category):
        try:
            await query.message.edit_text(f"❌ <b>У вас уже есть объявление в этой категории!</b>\n\n{CATEGORIES[category]}\n\nМаксимум 1 объявление на категорию!", reply_markup=get_main_menu())
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise e
    else:
        await state.update_data(category=category)
        try:
            await query.message.edit_text(f"📊 Введите количество для {CATEGORIES[category]}")
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise e
        await state.set_state(OfferStates.quantity)

@router.message(OfferStates.quantity)
async def offer_quantity(message: Message, state: FSMContext):
    try:
        quantity = float(message.text)
        await state.update_data(quantity=quantity)
        await message.answer("💰 Введите цену в $")
        await state.set_state(OfferStates.price)
    except:
        await message.answer("❌ Ошибка!", reply_markup=get_main_menu())

@router.message(OfferStates.price)
async def offer_price(message: Message, state: FSMContext):
    global offer_counter
    try:
        price = float(message.text)
    except:
        await message.answer("❌ Ошибка!", reply_markup=get_main_menu())
        return
    user = message.from_user
    user_id = user.id
    username = user.username or user.first_name
    data = await state.get_data()
    category = data.get('category')
    quantity = data.get('quantity')
    units = {"farm_sulfur": "серы", "farm_metal": "металла", "farm_wood": "дерева", "build_base": "базы", "farm_fuel": "топлива", "raid_help": "рейдов", "farm_scrap": "металалома", "install_turrets": "турелей", "hide_cabinet": "шкафов"}
    unit = units.get(category, "ед")
    offer_id = offer_counter
    offers_storage[offer_id] = {'id': offer_id, 'category': category, 'quantity': quantity, 'unit': unit, 'price': price, 'author_id': user_id, 'author': username}
    offer_counter += 1
    if user_id not in user_ratings:
        user_ratings[user_id] = {"total_rating": 0, "count": 0, "deals": 0}
    price_rub = convert_usd_to_rub(price)
    await message.answer(f"✅ <b>Создано!</b>\n\n{CATEGORIES[category]}\n📊 {quantity} {unit}\n💰 ${price} USD (≈{price_rub:.0f}р)", reply_markup=get_main_menu())
    await state.clear()

@router.callback_query(F.data.startswith("cancel_"))
async def cancel_offer(query: CallbackQuery):
    await query.answer()
    offer_id = int(query.data.replace("cancel_", ""))
    user_id = query.from_user.id
    if offer_id in offers_storage:
        offer = offers_storage[offer_id]
        if offer['author_id'] == user_id:
            del offers_storage[offer_id]
            try:
                await query.message.edit_text(f"✅ <b>Объявление отменено!</b>\n\n{CATEGORIES[offer['category']]} удалено", reply_markup=get_main_menu())
            except TelegramBadRequest as e:
                if "message is not modified" not in str(e):
                    raise e

@router.callback_query(F.data == "my_deals")
async def my_deals_handler(query: CallbackQuery):
    await query.answer()
    try:
        await query.message.edit_text("📋 У вас нет сделок", reply_markup=get_main_menu())
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise e

@router.callback_query(F.data == "profile")
async def profile_handler(query: CallbackQuery):
    await query.answer()
    user_id = query.from_user.id
    rating_data = user_ratings.get(user_id, {})
    avg_rating = rating_data.get('total_rating', 0) / max(rating_data.get('count', 1), 1)
    my_offers_count = len([o for o in offers_storage.values() if o['author_id'] == user_id])
    deals_count = rating_data.get('deals', 0)
    try:
        await query.message.edit_text(f"👤 <b>Профиль</b>\n\n⭐ Рейтинг: {avg_rating:.1f}/5\n📊 Сделок: {deals_count}\n📋 Предложений: {my_offers_count}", reply_markup=get_main_menu())
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise e

@router.callback_query(F.data == "history")
async def history_handler(query: CallbackQuery):
    await query.answer()
    user_id = query.from_user.id
    transactions = user_history.get(user_id, [])
    if not transactions:
        try:
            await query.message.edit_text("📜 История пуста", reply_markup=get_main_menu())
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise e
    else:
        text = "📜 <b>История:</b>\n\n"
        for trans in transactions[-10:]:
            text += f"💰 {trans['description']}\n"
        try:
            await query.message.edit_text(text, reply_markup=get_main_menu())
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise e

@router.callback_query(F.data == "help")
async def help_handler(query: CallbackQuery):
    await query.answer()
    try:
        await query.message.edit_text("❓ <b>Справка</b>\n\n1. Найди услугу\n2. Пополни баланс в рублях\n3. Создай предложение\n\n💰 Комиссия 5%\n💵 USDT USD", reply_markup=get_main_menu())
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise e

@router.callback_query(F.data == "return_main")
async def return_main(query: CallbackQuery):
    await query.answer()
    try:
        await query.message.edit_text("🎯 <b>Меню</b>", reply_markup=get_main_menu())
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise e

async def on_startup(bot: Bot) -> None:
    try:
        await bot.delete_webhook()
        logger.info("✅ Old webhook deleted")
        webhook_url = f"{BASE_WEBHOOK_URL}{WEBHOOK_URL}"
        await bot.set_webhook(url=webhook_url, drop_pending_updates=True)
        logger.info(f"✅ Telegram webhook set: {webhook_url}")
    except Exception as e:
        logger.error(f"Error setting webhook: {e}")

async def main():
    global invoices_map
    invoices_map.update(load_invoices())
    dp.startup.register(on_startup)
    app = web.Application()
    webhook_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_handler.register(app, path=WEBHOOK_URL)
    logger.info("✅ Webhook handler registered")
    setup_application(app, dp, bot=bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=WEB_SERVER_HOST, port=WEB_SERVER_PORT)
    await site.start()
    logger.info(f"🚀 Webhook server started on {WEB_SERVER_HOST}:{WEB_SERVER_PORT}")
    logger.info(f"✅ Webhook URL: {BASE_WEBHOOK_URL}{WEBHOOK_URL}")
    logger.info("❌ БЕЗ POLLING - ТОЛЬКО WEBHOOK!")
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен")
