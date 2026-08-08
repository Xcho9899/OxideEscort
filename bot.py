import logging
import requests
import os
import asyncio
import psycopg2
from psycopg2 import pool
from datetime import datetime
from uuid import uuid4
from aiohttp import web
from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, StateFilter
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
CRYPTOBOT_API = "https://pay.crypt.bot/api"
CRYPTOBOT_TOKEN = os.environ.get('CRYPTO_BOT_TOKEN')
DATABASE_URL = os.environ.get('DATABASE_URL')

WEBHOOK_URL = "/webhook/telegram"
WEBHOOK_HOST = "oxideescort-3.onrender.com"
BASE_WEBHOOK_URL = f"https://{WEBHOOK_HOST}"
WEB_SERVER_HOST = "0.0.0.0"
WEB_SERVER_PORT = int(os.environ.get('PORT', 8080))

class DepositStates(StatesGroup):
    amount = State()

class WithdrawStates(StatesGroup):
    amount = State()
    address = State()

class OfferStates(StatesGroup):
    category = State()
    quantity = State()
    price = State()

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

try:
    db_pool = psycopg2.pool.SimpleConnectionPool(1, 20, DATABASE_URL)
    logger.info("✅ PostgreSQL connection pool created")
except Exception as e:
    logger.error(f"❌ Database error: {e}")
    db_pool = None

def get_db():
    return db_pool.getconn() if db_pool else None

def return_db(conn):
    if db_pool and conn:
        db_pool.putconn(conn)

def init_db():
    conn = get_db()
    if not conn:
        return
    cursor = conn.cursor()
    try:
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS invoices (
            invoice_id BIGINT PRIMARY KEY,
            user_id BIGINT,
            amount_usd DECIMAL(10, 2),
            status VARCHAR(50),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_wallet (
            user_id BIGINT PRIMARY KEY,
            balance DECIMAL(15, 2) DEFAULT 0
        )
        ''')
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_history (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            type VARCHAR(50),
            amount DECIMAL(15, 2),
            description VARCHAR(255),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        conn.commit()
        logger.info("✅ Database tables created")
    except Exception as e:
        logger.error(f"❌ Init error: {e}")
        conn.rollback()
    finally:
        cursor.close()
        return_db(conn)

def get_invoice(invoice_id):
    conn = get_db()
    if not conn:
        return None
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT user_id, amount_usd::double precision, status FROM invoices WHERE invoice_id = %s", (invoice_id,))
        return cursor.fetchone()
    except Exception as e:
        logger.error(f"❌ Get invoice error: {e}")
        return None
    finally:
        cursor.close()
        return_db(conn)

def save_invoice(invoice_id, user_id, amount_usd, status='pending'):
    conn = get_db()
    if not conn:
        return False
    cursor = conn.cursor()
    try:
        cursor.execute('''
        INSERT INTO invoices (invoice_id, user_id, amount_usd, status)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (invoice_id) DO UPDATE SET status = %s
        ''', (invoice_id, user_id, amount_usd, status, status))
        conn.commit()
        logger.info(f"✅ Invoice saved: {invoice_id}")
        return True
    except Exception as e:
        logger.error(f"❌ Save error: {e}")
        conn.rollback()
        return False
    finally:
        cursor.close()
        return_db(conn)

def update_invoice_status(invoice_id, status):
    conn = get_db()
    if not conn:
        return False
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE invoices SET status = %s WHERE invoice_id = %s", (status, invoice_id))
        conn.commit()
        logger.info(f"✅ Status updated: {invoice_id}")
        return True
    except Exception as e:
        logger.error(f"❌ Update error: {e}")
        conn.rollback()
        return False
    finally:
        cursor.close()
        return_db(conn)

def get_wallet(user_id):
    conn = get_db()
    if not conn:
        return 0
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT balance::double precision FROM user_wallet WHERE user_id = %s", (user_id,))
        result = cursor.fetchone()
        return float(result[0]) if result else 0
    except:
        return 0
    finally:
        cursor.close()
        return_db(conn)

def update_wallet(user_id, amount):
    conn = get_db()
    if not conn:
        return False
    cursor = conn.cursor()
    try:
        cursor.execute('''
        INSERT INTO user_wallet (user_id, balance) VALUES (%s, %s)
        ON CONFLICT (user_id) DO UPDATE SET balance = %s
        ''', (user_id, amount, amount))
        conn.commit()
        logger.info(f"✅ Wallet updated: {user_id}")
        return True
    except Exception as e:
        logger.error(f"❌ Wallet error: {e}")
        conn.rollback()
        return False
    finally:
        cursor.close()
        return_db(conn)

def add_history(user_id, type_str, amount, description):
    conn = get_db()
    if not conn:
        return False
    cursor = conn.cursor()
    try:
        cursor.execute('''
        INSERT INTO user_history (user_id, type, amount, description)
        VALUES (%s, %s, %s, %s)
        ''', (user_id, type_str, amount, description))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"❌ History error: {e}")
        conn.rollback()
        return False
    finally:
        cursor.close()
        return_db(conn)

def get_history(user_id, limit=10):
    conn = get_db()
    if not conn:
        return []
    cursor = conn.cursor()
    try:
        cursor.execute('''
        SELECT description FROM user_history 
        WHERE user_id = %s ORDER BY created_at DESC LIMIT %s
        ''', (user_id, limit))
        return [row[0] for row in cursor.fetchall()]
    except:
        return []
    finally:
        cursor.close()
        return_db(conn)

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
        logger.info(f"Creating invoice: {amount_usd}")
        response = requests.post(f"{CRYPTOBOT_API}/createInvoice", headers=headers, json=payload, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get('ok'):
                invoice = data.get('result', {})
                invoice_id = invoice.get('invoice_id')
                pay_url = invoice.get('pay_url')
                save_invoice(invoice_id, user_id, amount_usd, 'pending')
                logger.info(f"Invoice created: {invoice_id}")
                return pay_url, invoice_id
        return None, None
    except Exception as e:
        logger.error(f"Invoice error: {e}")
        return None, None

def check_invoice_paid(invoice_id: str):
    try:
        logger.info(f"Checking: {invoice_id}")
        headers = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN}
        response = requests.get(f"{CRYPTOBOT_API}/getInvoices?invoice_ids={invoice_id}", headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get('ok'):
                invoices = data.get('result', {}).get('items', [])
                if invoices:
                    status = invoices[0].get('status')
                    logger.info(f"Invoice status: {status}")
                    return status == 'paid'
        return False
    except Exception as e:
        logger.error(f"Check error: {e}")
        return False

def withdraw_to_address(amount_usd: float, address: str):
    try:
        invoice_id = str(uuid4())
        logger.info(f"Withdraw: ${amount_usd} to {address}, invoice_id={invoice_id}")
        
        headers = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN, "Content-Type": "application/json"}
        payload = {
            "asset": "USDT",
            "amount": str(amount_usd),
            "address": address,
            "invoice_id": invoice_id
        }
        
        logger.info(f"Withdraw payload: {payload}")
        response = requests.post(f"{CRYPTOBOT_API}/withdraw", headers=headers, json=payload, timeout=10)
        
        logger.info(f"Withdraw status: {response.status_code}")
        logger.info(f"Withdraw response: {response.text}")
        
        if response.status_code == 200:
            data = response.json()
            if data.get('ok'):
                logger.info(f"Withdraw success: {data}")
                return True, data.get('result')
        
        logger.error(f"Withdraw failed: {response.text}")
        return False, response.text
    except Exception as e:
        logger.error(f"Withdraw error: {e}")
        return False, str(e)

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
async def start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("👋 <b>OxideEscort - Маркетплейс услуг</b>\n\n🎮 Oxide Survival Island\n💵 USDT USD\n💰 Комиссия 5%", reply_markup=get_main_menu())

@router.callback_query(F.data == "board")
async def board_handler(query: CallbackQuery):
    await query.answer()
    keyboard = []
    for key, name in CATEGORIES.items():
        keyboard.append([InlineKeyboardButton(text=f"{name}", callback_data=f"cat_{key}")])
    keyboard.append([InlineKeyboardButton(text="🏠 Меню", callback_data="return_main")])
    try:
        await query.message.edit_text("🛍️ <b>Доска услуг</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    except TelegramBadRequest:
        pass

@router.callback_query(F.data.startswith("cat_"))
async def category_handler(query: CallbackQuery):
    await query.answer()
    category = query.data.replace("cat_", "")
    offers = [o for o in offers_storage.values() if o['category'] == category]
    if not offers:
        keyboard = [[InlineKeyboardButton(text="⬅️ Назад", callback_data="board")]]
        try:
            await query.message.edit_text("📭 Предложений нет", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
        except TelegramBadRequest:
            pass
    else:
        keyboard = []
        for offer in offers:
            price_rub = convert_usd_to_rub(offer['price'])
            keyboard.append([InlineKeyboardButton(text=f"💰 {offer['quantity']} = ${offer['price']} (≈{price_rub:.0f}р)", callback_data=f"offer_{offer['id']}")])
        keyboard.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="board")])
        try:
            await query.message.edit_text(f"📊 <b>{CATEGORIES[category]}</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
        except TelegramBadRequest:
            pass

@router.callback_query(F.data == "wallet")
async def wallet_handler(query: CallbackQuery):
    await query.answer()
    user_id = query.from_user.id
    rate = get_usdt_rub_rate()
    balance = get_wallet(user_id)
    balance_usd = balance / rate if rate else 0
    keyboard = [
        [InlineKeyboardButton(text="💳 Пополнить", callback_data="deposit")],
        [InlineKeyboardButton(text="💰 Вывести", callback_data="withdraw")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="return_main")]
    ]
    try:
        await query.message.edit_text(f"💳 <b>Кошелек</b>\n\n💵 Баланс: {balance:.0f}р (${balance_usd:.2f})", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    except TelegramBadRequest:
        pass

@router.callback_query(F.data == "deposit")
async def deposit_start(query: CallbackQuery, state: FSMContext):
    await query.answer()
    await query.message.answer("💵 <b>Введите сумму (рубли)</b>\n\nМинимум: 81р")
    await state.set_state(DepositStates.amount)

@router.message(StateFilter(DepositStates.amount), F.text)
async def deposit_amount(message: Message, state: FSMContext):
    try:
        amount_rub = float(message.text)
        user_id = message.from_user.id
        if amount_rub < 81:
            await message.answer("❌ Минимум 81р!", reply_markup=get_main_menu())
            await state.clear()
            return
        amount_usd = convert_rub_to_usd(amount_rub)
        pay_url, invoice_id = create_cryptobot_invoice(amount_usd, "Пополнение", user_id)
        if pay_url and invoice_id:
            keyboard = [
                [InlineKeyboardButton(text="💳 Оплатить", url=pay_url)],
                [InlineKeyboardButton(text="✅ Проверить платеж", callback_data=f"check_{invoice_id}")],
                [InlineKeyboardButton(text="🏠 Меню", callback_data="return_main")]
            ]
            await message.answer(f"💵 <b>Счет создан!</b>\n\n{amount_rub:.0f}р = ${amount_usd}\n\n1️⃣ Нажми 'Оплатить'\n2️⃣ Оплати в CryptoBot\n3️⃣ Вернись и нажми 'Проверить платеж'", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
            await state.clear()
        else:
            await message.answer("❌ Ошибка создания счета!", reply_markup=get_main_menu())
            await state.clear()
    except ValueError:
        await message.answer("❌ Введите число!", reply_markup=get_main_menu())
        await state.clear()

@router.callback_query(F.data == "withdraw")
async def withdraw_start(query: CallbackQuery, state: FSMContext):
    await query.answer()
    user_id = query.from_user.id
    rate = get_usdt_rub_rate()
    balance = get_wallet(user_id)
    balance_usd = balance / rate if rate else 0
    if balance_usd < 1:
        try:
            await query.message.edit_text(f"❌ Минимум $1 USD\n\nБаланс: ${balance_usd:.2f}", reply_markup=get_main_menu())
        except TelegramBadRequest:
            pass
    else:
        await query.message.answer(f"💰 <b>Введите сумму в USD</b>\n\nБаланс: ${balance_usd:.2f}\nМинимум: $1")
        await state.set_state(WithdrawStates.amount)

@router.message(StateFilter(WithdrawStates.amount), F.text)
async def withdraw_amount(message: Message, state: FSMContext):
    try:
        amount_usd = float(message.text)
        user_id = message.from_user.id
        rate = get_usdt_rub_rate()
        balance = get_wallet(user_id)
        balance_usd = balance / rate if rate else 0
        
        if amount_usd < 1:
            await message.answer("❌ Минимум $1!", reply_markup=get_main_menu())
            await state.clear()
            return
        
        if amount_usd > balance_usd:
            await message.answer(f"❌ Недостаточно средств!\n\nБаланс: ${balance_usd:.2f}", reply_markup=get_main_menu())
            await state.clear()
            return
        
        await state.update_data(withdraw_amount=amount_usd)
        await message.answer("💰 Введите TRC-20 адрес\n\n(начинается с T, 34 символа)")
        await state.set_state(WithdrawStates.address)
    except ValueError:
        await message.answer("❌ Введите число!", reply_markup=get_main_menu())
        await state.clear()

@router.message(StateFilter(WithdrawStates.address), F.text)
async def withdraw_address(message: Message, state: FSMContext):
    try:
        address = message.text.strip()
        if not (len(address) == 34 and address.startswith('T')):
            await message.answer("❌ Неверный адрес! (T + 34 символа)", reply_markup=get_main_menu())
            await state.clear()
            return
        
        user_id = message.from_user.id
        data = await state.get_data()
        amount_usd = data.get('withdraw_amount')
        rate = get_usdt_rub_rate()
        amount_rub = convert_usd_to_rub(amount_usd)
        balance = get_wallet(user_id)
        
        if balance < amount_rub:
            await message.answer(f"❌ Недостаточно средств!\n\nБаланс: {balance:.0f}р", reply_markup=get_main_menu())
            await state.clear()
            return
        
        logger.info(f"Withdrawing ${amount_usd} from user {user_id} to {address}")
        success, result = withdraw_to_address(amount_usd, address)
        
        if success:
            new_balance = balance - amount_rub
            update_wallet(user_id, new_balance)
            add_history(user_id, 'withdraw', amount_rub, f'Вывод ${amount_usd} на {address[:10]}...')
            await message.answer(f"✅ Отправлено ${amount_usd} на адрес\n\nНовый баланс: {new_balance:.0f}р", reply_markup=get_main_menu())
            logger.info(f"Withdraw success: ${amount_usd}")
        else:
            await message.answer(f"❌ Ошибка!\n\n{result}", reply_markup=get_main_menu())
            logger.error(f"Withdraw failed: {result}")
        
        await state.clear()
    except Exception as e:
        logger.error(f"Withdraw error: {e}")
        await message.answer("❌ Ошибка!", reply_markup=get_main_menu())
        await state.clear()

@router.callback_query(F.data.startswith("check_"))
async def check_payment(query: CallbackQuery):
    await query.answer()
    invoice_id = int(query.data.replace("check_", ""))
    logger.info(f"User checking payment: {invoice_id}")
    
    invoice_data = get_invoice(invoice_id)
    if not invoice_data:
        logger.error(f"Invoice not found: {invoice_id}")
        try:
            await query.message.edit_text("❌ Счет не найден или истек!", reply_markup=get_main_menu())
        except TelegramBadRequest:
            pass
        return
    
    user_id, amount_usd, status = invoice_data
    logger.info(f"Invoice data: user={user_id}, amount={amount_usd}, status={status}")
    
    if status == 'paid':
        logger.info(f"Invoice already paid: {invoice_id}")
        try:
            await query.message.edit_text(f"✅ Уже оплачено! +${amount_usd}", reply_markup=get_main_menu())
        except TelegramBadRequest:
            pass
        return
    
    logger.info(f"Checking with CryptoBot: {invoice_id}")
    is_paid = check_invoice_paid(invoice_id)
    logger.info(f"CryptoBot result: is_paid={is_paid}")
    
    if is_paid:
        logger.info(f"Payment confirmed! Processing: {invoice_id}")
        amount_rub = convert_usd_to_rub(amount_usd)
        new_balance = get_wallet(user_id) + amount_rub
        update_wallet(user_id, new_balance)
        add_history(user_id, 'deposit', amount_rub, f'Платеж ${amount_usd}')
        update_invoice_status(invoice_id, 'paid')
        try:
            await query.message.edit_text(f"✅ Платеж прошел!\n\n+${amount_usd}\n💵 Новый баланс: {new_balance:.0f}р", reply_markup=get_main_menu())
        except TelegramBadRequest:
            pass
    else:
        logger.info(f"Payment NOT confirmed yet: {invoice_id}")
        try:
            await query.message.edit_text("⏳ Платеж еще не поступил\n\nПроверьте позже", reply_markup=get_main_menu())
        except TelegramBadRequest:
            pass

@router.callback_query(F.data == "my_offers")
async def my_offers_handler(query: CallbackQuery):
    await query.answer()
    user_id = query.from_user.id
    my_offers = [o for o in offers_storage.values() if o['author_id'] == user_id]
    if my_offers:
        text = "📊 Мои предложения:\n\n"
        for offer in my_offers:
            price_rub = convert_usd_to_rub(offer['price'])
            text += f"{offer['quantity']} = ${offer['price']} (≈{price_rub:.0f}р)\n"
        keyboard = []
        for offer in my_offers:
            keyboard.append([InlineKeyboardButton(text=f"❌ #{offer['id']}", callback_data=f"cancel_{offer['id']}")])
        keyboard.append([InlineKeyboardButton(text="🏠 Меню", callback_data="return_main")])
    else:
        text = "📭 Нет предложений"
        keyboard = [[InlineKeyboardButton(text="🏠 Меню", callback_data="return_main")]]
    try:
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    except TelegramBadRequest:
        pass

@router.callback_query(F.data == "my_deals")
async def my_deals_handler(query: CallbackQuery):
    await query.answer()
    try:
        await query.message.edit_text("📋 Нет сделок", reply_markup=get_main_menu())
    except TelegramBadRequest:
        pass

@router.callback_query(F.data == "profile")
async def profile_handler(query: CallbackQuery):
    await query.answer()
    try:
        await query.message.edit_text("👤 Профиль\n\n⭐ Рейтинг: 0/5", reply_markup=get_main_menu())
    except TelegramBadRequest:
        pass

@router.callback_query(F.data == "history")
async def history_handler(query: CallbackQuery):
    await query.answer()
    user_id = query.from_user.id
    transactions = get_history(user_id, 10)
    if not transactions:
        text = "📜 История пуста"
    else:
        text = "📜 История:\n\n"
        for trans in transactions:
            text += f"{trans}\n"
    try:
        await query.message.edit_text(text, reply_markup=get_main_menu())
    except TelegramBadRequest:
        pass

@router.callback_query(F.data == "help")
async def help_handler(query: CallbackQuery):
    await query.answer()
    try:
        await query.message.edit_text("❓ Маркетплейс Oxide\n\n💰 Комиссия 5%\n💵 USDT TRC-20", reply_markup=get_main_menu())
    except TelegramBadRequest:
        pass

@router.callback_query(F.data == "return_main")
async def return_main(query: CallbackQuery):
    await query.answer()
    try:
        await query.message.edit_text("🎯 Меню", reply_markup=get_main_menu())
    except TelegramBadRequest:
        pass

@router.callback_query(F.data.startswith("cancel_"))
async def cancel_offer(query: CallbackQuery):
    await query.answer()
    offer_id = int(query.data.replace("cancel_", ""))
    user_id = query.from_user.id
    if offer_id in offers_storage:
        if offers_storage[offer_id]['author_id'] == user_id:
            del offers_storage[offer_id]
            try:
                await query.message.edit_text("✅ Отменено", reply_markup=get_main_menu())
            except TelegramBadRequest:
                pass

async def on_startup(bot: Bot) -> None:
    try:
        await bot.delete_webhook()
        webhook_url = f"{BASE_WEBHOOK_URL}{WEBHOOK_URL}"
        await bot.set_webhook(url=webhook_url, drop_pending_updates=True)
        logger.info("Webhook set")
    except Exception as e:
        logger.error(f"Startup error: {e}")

async def main():
    init_db()
    dp.startup.register(on_startup)
    app = web.Application()
    webhook_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_handler.register(app, path=WEBHOOK_URL)
    setup_application(app, dp, bot=bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=WEB_SERVER_HOST, port=WEB_SERVER_PORT)
    await site.start()
    logger.info("✅ PostgreSQL")
    logger.info("✅ Database")
    logger.info("✅ All tokens loaded from environment variables")
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
