import logging
import requests
import os
import json
import asyncio
from aiohttp import web
from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, StateFilter
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
    "farm_sulfur": "⚒️ Фарм серы", "farm_metal": "🔩 Фарм металла", "farm_wood": "🪵 Фарм дерева",
    "build_base": "🏗️ Постройка базы", "farm_fuel": "⛽ Фарм топливо", "raid_help": "🛡️ Помощь в рейдах",
    "farm_scrap": "🔧 Фарм металалома", "install_turrets": "🔫 Установка турелей", "hide_cabinet": "🚪 Скидка шкафа",
}

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
dp.include_router(router)

def get_usdt_rub_rate():
    try:
        response = requests.get('https://api.coingecko.com/api/v3/simple/price?ids=tether&vs_currencies=rub', timeout=5)
        return response.json()['tether']['rub']
    except:
        return 81.0

def convert_usd_to_rub(amount_usd):
    return round(amount_usd * get_usdt_rub_rate(), 2)

def convert_rub_to_usd(amount_rub):
    return round(amount_rub / get_usdt_rub_rate(), 2)

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
                invoices_map[invoice_id] = {'user_id': user_id, 'amount_usd': amount_usd, 'status': 'pending', 'created_at': datetime.now()}
                logger.info(f"✅ Invoice created: {invoice_id}")
                return pay_url, invoice_id
        return None, None
    except Exception as e:
        logger.error(f"Error creating invoice: {e}")
        return None, None

def check_invoice_paid(invoice_id: str):
    try:
        headers = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN}
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

def get_main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛍️ Доска услуг", callback_data="board")],
        [InlineKeyboardButton(text="📊 Мои предложения", callback_data="my_offers")],
        [InlineKeyboardButton(text="💳 Кошелек", callback_data="wallet")],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="help")],
    ])

@router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    logger.info(f"✅ START from {message.from_user.id}")
    user_id = message.from_user.id
    if user_id not in user_wallet:
        user_wallet[user_id] = 0
    if user_id not in user_ratings:
        user_ratings[user_id] = {"total_rating": 0, "count": 0, "deals": 0}
    if user_id not in user_history:
        user_history[user_id] = []
    await state.clear()
    await message.answer("👋 <b>OxideEscort</b>\n\n🎮 Oxide Survival Island\n💵 USDT USD", reply_markup=get_main_menu())

@router.callback_query(F.data == "wallet")
async def wallet_handler(query: CallbackQuery):
    await query.answer()
    user_id = query.from_user.id
    rate = get_usdt_rub_rate()
    balance = user_wallet.get(user_id, 0)
    balance_usd = balance / rate if rate else 0
    keyboard = [[InlineKeyboardButton(text="💳 Пополнить", callback_data="deposit")], [InlineKeyboardButton(text="🏠 Меню", callback_data="return_main")]]
    try:
        await query.message.edit_text(f"💳 <b>Кошелек</b>\n\n💵 Баланс: {balance:.0f}р (${balance_usd:.2f})", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            logger.error(f"Error: {e}")

@router.callback_query(F.data == "deposit")
async def deposit_start(query: CallbackQuery, state: FSMContext):
    logger.info(f"🔵 DEPOSIT START - {query.from_user.id}")
    await query.answer()
    rate = get_usdt_rub_rate()
    try:
        await query.message.edit_text(f"💵 <b>Введите сумму в РУБЛЯХ</b>\n\nКурс: 1 USD = {rate:.2f}р")
    except TelegramBadRequest:
        pass
    await state.set_state(DepositStates.amount)

@router.message(StateFilter(DepositStates.amount), F.text)
async def deposit_amount(message: Message, state: FSMContext):
    logger.info(f"🔵 DEPOSIT AMOUNT - {message.from_user.id}: {message.text}")
    try:
        amount_rub = float(message.text)
        user_id = message.from_user.id
        if amount_rub < 81:
            await message.answer("❌ Минимум 81р!", reply_markup=get_main_menu())
            await state.clear()
            return
        amount_usd = convert_rub_to_usd(amount_rub)
        pay_url, invoice_id = create_cryptobot_invoice(amount_usd, "Пополнение", user_id)
        if pay_url:
            keyboard = [[InlineKeyboardButton(text="💳 Оплатить", url=pay_url)], [InlineKeyboardButton(text="🏠 Меню", callback_data="return_main")]]
            await message.answer(f"✅ Счет создан!\n\n💰 {amount_rub:.0f}р = ${amount_usd}", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
            await state.clear()
        else:
            await message.answer("❌ Ошибка создания счета!", reply_markup=get_main_menu())
            await state.clear()
    except ValueError:
        await message.answer("❌ Введите число!", reply_markup=get_main_menu())
        await state.clear()

@router.callback_query(F.data == "help")
async def help_handler(query: CallbackQuery):
    await query.answer()
    try:
        await query.message.edit_text("❓ Маркетплейс услуг Oxide\n\n💰 Комиссия 5%", reply_markup=get_main_menu())
    except TelegramBadRequest:
        pass

@router.callback_query(F.data == "return_main")
async def return_main(query: CallbackQuery):
    await query.answer()
    try:
        await query.message.edit_text("🎯 <b>Меню</b>", reply_markup=get_main_menu())
    except TelegramBadRequest:
        pass

async def on_startup(bot: Bot) -> None:
    try:
        await bot.delete_webhook()
        logger.info("✅ Webhook deleted")
        webhook_url = f"{BASE_WEBHOOK_URL}{WEBHOOK_URL}"
        await bot.set_webhook(url=webhook_url, drop_pending_updates=True)
        logger.info(f"✅ Webhook set: {webhook_url}")
    except Exception as e:
        logger.error(f"Error: {e}")

async def main():
    dp.startup.register(on_startup)
    app = web.Application()
    webhook_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_handler.register(app, path=WEBHOOK_URL)
    setup_application(app, dp, bot=bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=WEB_SERVER_HOST, port=WEB_SERVER_PORT)
    await site.start()
    logger.info(f"🚀 Server started on {WEB_SERVER_HOST}:{WEB_SERVER_PORT}")
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен")
