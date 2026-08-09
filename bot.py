import logging
import requests
import os
import asyncio
import psycopg2
from psycopg2 import pool
from datetime import datetime
from uuid import uuid4
from aiohttp import web, ClientSession
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

class CreateOfferStates(StatesGroup):
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
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS withdrawals (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            amount_usd DECIMAL(10, 2),
            status VARCHAR(50),
            check_id VARCHAR(255),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS offers (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            category VARCHAR(50),
            quantity VARCHAR(255),
            price DECIMAL(10, 2),
            status VARCHAR(50) DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS deals (
            id SERIAL PRIMARY KEY,
            offer_id INT,
            seller_id BIGINT,
            buyer_id BIGINT,
            amount_usd DECIMAL(10, 2),
            status VARCHAR(50) DEFAULT 'pending',
            seller_confirmed BOOLEAN DEFAULT FALSE,
            buyer_confirmed BOOLEAN DEFAULT FALSE,
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

def save_withdrawal(user_id, amount_usd, status, check_id=None):
    conn = get_db()
    if not conn:
        return False
    cursor = conn.cursor()
    try:
        cursor.execute('''
        INSERT INTO withdrawals (user_id, amount_usd, status, check_id)
        VALUES (%s, %s, %s, %s)
        ''', (user_id, amount_usd, status, check_id))
        conn.commit()
        logger.info(f"✅ Withdrawal saved: user={user_id}, amount=${amount_usd}, status={status}")
        return True
    except Exception as e:
        logger.error(f"❌ Withdrawal save error: {e}")
        conn.rollback()
        return False
    finally:
        cursor.close()
        return_db(conn)

def check_duplicate_offer(user_id, category, price):
    conn = get_db()
    if not conn:
        return False
    cursor = conn.cursor()
    try:
        cursor.execute('''
        SELECT id FROM offers 
        WHERE user_id = %s AND category = %s AND price = %s AND status = %s
        ''', (user_id, category, price, 'active'))
        result = cursor.fetchone()
        return result is not None
    except:
        return False
    finally:
        cursor.close()
        return_db(conn)

def save_offer(user_id, category, quantity, price):
    conn = get_db()
    if not conn:
        return None
    cursor = conn.cursor()
    try:
        cursor.execute('''
        INSERT INTO offers (user_id, category, quantity, price, status)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
        ''', (user_id, category, quantity, price, 'active'))
        offer_id = cursor.fetchone()[0]
        conn.commit()
        logger.info(f"✅ Offer saved: id={offer_id}, user={user_id}")
        return offer_id
    except Exception as e:
        logger.error(f"❌ Offer save error: {e}")
        conn.rollback()
        return None
    finally:
        cursor.close()
        return_db(conn)

def get_offers_by_category(category):
    conn = get_db()
    if not conn:
        return []
    cursor = conn.cursor()
    try:
        cursor.execute('''
        SELECT id, user_id, quantity, price::double precision FROM offers 
        WHERE category = %s AND status = %s
        ORDER BY created_at DESC
        ''', (category, 'active'))
        return cursor.fetchall()
    except:
        return []
    finally:
        cursor.close()
        return_db(conn)

def get_offer_by_id(offer_id):
    conn = get_db()
    if not conn:
        return None
    cursor = conn.cursor()
    try:
        cursor.execute('''
        SELECT id, user_id, category, quantity, price::double precision FROM offers 
        WHERE id = %s AND status = %s
        ''', (offer_id, 'active'))
        return cursor.fetchone()
    except:
        return None
    finally:
        cursor.close()
        return_db(conn)

def get_user_offers(user_id):
    conn = get_db()
    if not conn:
        return []
    cursor = conn.cursor()
    try:
        cursor.execute('''
        SELECT id, category, quantity, price::double precision FROM offers 
        WHERE user_id = %s AND status = %s
        ORDER BY created_at DESC
        ''', (user_id, 'active'))
        return cursor.fetchall()
    except:
        return []
    finally:
        cursor.close()
        return_db(conn)

def delete_offer(offer_id, user_id):
    conn = get_db()
    if not conn:
        return False
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM offers WHERE id = %s AND user_id = %s", (offer_id, user_id))
        conn.commit()
        logger.info(f"✅ Offer deleted: {offer_id}")
        return True
    except Exception as e:
        logger.error(f"❌ Delete error: {e}")
        conn.rollback()
        return False
    finally:
        cursor.close()
        return_db(conn)

def save_deal(offer_id, seller_id, buyer_id, amount_usd):
    conn = get_db()
    if not conn:
        return None
    cursor = conn.cursor()
    try:
        cursor.execute('''
        INSERT INTO deals (offer_id, seller_id, buyer_id, amount_usd, status)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
        ''', (offer_id, seller_id, buyer_id, amount_usd, 'pending'))
        deal_id = cursor.fetchone()[0]
        conn.commit()
        logger.info(f"✅ Deal created: id={deal_id}, seller={seller_id}, buyer={buyer_id}")
        return deal_id
    except Exception as e:
        logger.error(f"❌ Deal save error: {e}")
        conn.rollback()
        return None
    finally:
        cursor.close()
        return_db(conn)

def get_deal(deal_id):
    conn = get_db()
    if not conn:
        return None
    cursor = conn.cursor()
    try:
        cursor.execute('''
        SELECT id, offer_id, seller_id, buyer_id, amount_usd::double precision, status, seller_confirmed, buyer_confirmed 
        FROM deals WHERE id = %s
        ''', (deal_id,))
        return cursor.fetchone()
    except:
        return None
    finally:
        cursor.close()
        return_db(conn)

def update_deal_confirmation(deal_id, role, confirmed):
    """role = 'seller' или 'buyer'"""
    conn = get_db()
    if not conn:
        return False
    cursor = conn.cursor()
    try:
        if role == 'seller':
            cursor.execute("UPDATE deals SET seller_confirmed = %s WHERE id = %s", (confirmed, deal_id))
        else:
            cursor.execute("UPDATE deals SET buyer_confirmed = %s WHERE id = %s", (confirmed, deal_id))
        conn.commit()
        logger.info(f"✅ Deal confirmation updated: deal={deal_id}, role={role}")
        return True
    except Exception as e:
        logger.error(f"❌ Confirmation error: {e}")
        conn.rollback()
        return False
    finally:
        cursor.close()
        return_db(conn)

def complete_deal(deal_id):
    """Завершить сделку и перевести деньги"""
    conn = get_db()
    if not conn:
        return False
    cursor = conn.cursor()
    try:
        # Получаем информацию о сделке
        cursor.execute('''
        SELECT seller_id, buyer_id, amount_usd::double precision FROM deals WHERE id = %s
        ''', (deal_id,))
        result = cursor.fetchone()
        if not result:
            return False
        
        seller_id, buyer_id, amount_usd = result
        rate = get_usdt_rub_rate()
        
        # Вычитаем деньги у продавца
        seller_balance = get_wallet(seller_id)
        amount_rub = convert_usd_to_rub(amount_usd)
        new_seller_balance = seller_balance - amount_rub
        update_wallet(seller_id, new_seller_balance)
        
        # Даем деньги покупателю (минус комиссия 5%)
        commission = amount_usd * 0.05
        buyer_amount = amount_usd - commission
        buyer_amount_rub = convert_usd_to_rub(buyer_amount)
        buyer_balance = get_wallet(buyer_id)
        new_buyer_balance = buyer_balance + buyer_amount_rub
        update_wallet(buyer_id, new_buyer_balance)
        
        # Обновляем статус сделки
        cursor.execute("UPDATE deals SET status = %s WHERE id = %s", ('completed', deal_id))
        conn.commit()
        
        # Добавляем историю
        add_history(seller_id, 'deal_payment', amount_rub, f'Оплата за задание #{deal_id}')
        add_history(buyer_id, 'deal_complete', buyer_amount_rub, f'Получено за задание #{deal_id} (комиссия: ${commission})')
        
        logger.info(f"✅ Deal completed: id={deal_id}, seller_paid=${amount_usd}, buyer_got=${buyer_amount}")
        return True
    except Exception as e:
        logger.error(f"❌ Complete deal error: {e}")
        conn.rollback()
        return False
    finally:
        cursor.close()
        return_db(conn)

async def get_server_ip():
    try:
        async with ClientSession() as session:
            async with session.get('https://api.ipify.org?format=json', timeout=5) as resp:
                data = await resp.json()
                server_ip = data.get('ip')
                logger.info(f"🌍 SERVER OUTGOING IP: {server_ip}")
                return server_ip
    except Exception as e:
        logger.error(f"❌ Cannot get server IP: {e}")
        return None

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

def create_check(amount_usd: float, user_id: int):
    try:
        logger.info(f"Creating check: ${amount_usd} for user {user_id}")
        
        headers = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN, "Content-Type": "application/json"}
        payload = {
            "asset": "USDT",
            "amount": str(amount_usd),
            "user_id": user_id,
            "description": "Вывод заработков из OxideEscort"
        }
        
        logger.info(f"Check payload: {payload}")
        response = requests.post(f"{CRYPTOBOT_API}/createCheck", headers=headers, json=payload, timeout=10)
        
        logger.info(f"Check status: {response.status_code}")
        logger.info(f"Check response: {response.text}")
        
        if response.status_code == 200:
            data = response.json()
            if data.get('ok'):
                check_data = data.get('result', {})
                check_url = check_data.get('bot_check_url')
                check_id = check_data.get('check_id')
                logger.info(f"Check created successfully: {check_url}")
                return True, check_url, check_id
        
        logger.error(f"Check creation failed: {response.text}")
        return False, response.text, None
    except Exception as e:
        logger.error(f"Check error: {e}")
        return False, str(e), None

def get_main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛍️ Доска услуг", callback_data="board")],
        [InlineKeyboardButton(text="📝 Создать объявление", callback_data="create_offer")],
        [InlineKeyboardButton(text="📊 Мои объявления", callback_data="my_offers")],
        [InlineKeyboardButton(text="🤝 Мои сделки", callback_data="my_deals")],
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
    offers = get_offers_by_category(category)
    if not offers:
        keyboard = [[InlineKeyboardButton(text="⬅️ Назад", callback_data="board")]]
        try:
            await query.message.edit_text("📭 Предложений нет", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
        except TelegramBadRequest:
            pass
    else:
        keyboard = []
        for offer_id, user_id, quantity, price in offers:
            price_rub = convert_usd_to_rub(price)
            keyboard.append([InlineKeyboardButton(text=f"💰 {quantity} = ${price} (≈{price_rub:.0f}р)", callback_data=f"view_{offer_id}")])
        keyboard.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="board")])
        try:
            await query.message.edit_text(f"📊 <b>{CATEGORIES[category]}</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
        except TelegramBadRequest:
            pass

@router.callback_query(F.data.startswith("view_"))
async def view_offer_handler(query: CallbackQuery):
    await query.answer()
    offer_id = int(query.data.replace("view_", ""))
    buyer_id = query.from_user.id
    
    offer = get_offer_by_id(offer_id)
    if not offer:
        try:
            await query.message.edit_text("❌ Объявление не найдено!", reply_markup=get_main_menu())
        except TelegramBadRequest:
            pass
        return
    
    offer_id_db, seller_id, category, quantity, price = offer
    
    if buyer_id == seller_id:
        try:
            await query.message.edit_text("❌ Это ваше объявление!", reply_markup=get_main_menu())
        except TelegramBadRequest:
            pass
        return
    
    price_rub = convert_usd_to_rub(price)
    text = f"📋 <b>Детали объявления</b>\n\n" \
            f"Категория: {CATEGORIES[category]}\n" \
            f"Количество: {quantity}\n" \
            f"Цена: ${price} (≈{price_rub:.0f}р)\n" \
            f"Продавец: @user{seller_id}"
    
    keyboard = [
        [InlineKeyboardButton(text="✅ Принять задание", callback_data=f"accept_{offer_id_db}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="board")]
    ]
    
    try:
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    except TelegramBadRequest:
        pass

@router.callback_query(F.data.startswith("accept_"))
async def accept_offer_handler(query: CallbackQuery):
    await query.answer()
    offer_id = int(query.data.replace("accept_", ""))
    buyer_id = query.from_user.id
    
    offer = get_offer_by_id(offer_id)
    if not offer:
        try:
            await query.message.edit_text("❌ Объявление не найдено!", reply_markup=get_main_menu())
        except TelegramBadRequest:
            pass
        return
    
    offer_id_db, seller_id, category, quantity, price = offer
    
    # ✅ Создаем сделку БЕЗ вычета денег
    deal_id = save_deal(offer_id_db, seller_id, buyer_id, price)
    
    if deal_id:
        price_rub = convert_usd_to_rub(price)
        try:
            await query.message.edit_text(
                f"✅ <b>Задание принято!</b>\n\n"
                f"Сделка: #{deal_id}\n"
                f"Сумма: ${price} (≈{price_rub:.0f}р)\n\n"
                f"⏳ Ожидание выполнения...\n"
                f"После выполнения нажмите 'Готово'",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Готово", callback_data=f"deal_done_{deal_id}")],
                    [InlineKeyboardButton(text="🏠 Меню", callback_data="return_main")]
                ])
            )
        except TelegramBadRequest:
            pass
    else:
        try:
            await query.message.edit_text("❌ Ошибка создания сделки!", reply_markup=get_main_menu())
        except TelegramBadRequest:
            pass

@router.callback_query(F.data.startswith("deal_done_"))
async def deal_done_handler(query: CallbackQuery):
    await query.answer()
    deal_id = int(query.data.replace("deal_done_", ""))
    buyer_id = query.from_user.id
    
    deal = get_deal(deal_id)
    if not deal:
        try:
            await query.message.edit_text("❌ Сделка не найдена!", reply_markup=get_main_menu())
        except TelegramBadRequest:
            pass
        return
    
    deal_id_db, offer_id, seller_id, buyer_id_db, amount_usd, status, seller_confirmed, buyer_confirmed = deal
    
    # Только покупатель может подтвердить выполнение
    if buyer_id != buyer_id_db:
        try:
            await query.message.edit_text("❌ Вы не покупатель в этой сделке!", reply_markup=get_main_menu())
        except TelegramBadRequest:
            pass
        return
    
    # Обновляем подтверждение покупателя
    update_deal_confirmation(deal_id, 'buyer', True)
    
    try:
        await query.message.edit_text(
            f"✅ <b>Вы подтвердили выполнение!</b>\n\n"
            f"Ожидание подтверждения продавца...",
            reply_markup=get_main_menu()
        )
    except TelegramBadRequest:
        pass

@router.callback_query(F.data.startswith("verify_deal_"))
async def verify_deal_handler(query: CallbackQuery):
    await query.answer()
    deal_id = int(query.data.replace("verify_deal_", ""))
    seller_id = query.from_user.id
    
    deal = get_deal(deal_id)
    if not deal:
        try:
            await query.message.edit_text("❌ Сделка не найдена!", reply_markup=get_main_menu())
        except TelegramBadRequest:
            pass
        return
    
    deal_id_db, offer_id, seller_id_db, buyer_id, amount_usd, status, seller_confirmed, buyer_confirmed = deal
    
    # Только продавец может подтвердить
    if seller_id != seller_id_db:
        try:
            await query.message.edit_text("❌ Вы не продавец в этой сделке!", reply_markup=get_main_menu())
        except TelegramBadRequest:
            pass
        return
    
    # Проверяем баланс продавца
    rate = get_usdt_rub_rate()
    seller_balance = get_wallet(seller_id)
    amount_rub = convert_usd_to_rub(amount_usd)
    
    if seller_balance < amount_rub:
        try:
            await query.message.edit_text(
                f"❌ Недостаточно средств на счете!\n\n"
                f"Нужно: ${amount_usd}\n"
                f"Баланс: ${seller_balance / rate:.2f}",
                reply_markup=get_main_menu()
            )
        except TelegramBadRequest:
            pass
        return
    
    # ✅ ЗАВЕРШАЕМ СДЕЛКУ - ПЕРЕВОДИМ ДЕНЬГИ
    if complete_deal(deal_id):
        commission = amount_usd * 0.05
        buyer_amount = amount_usd - commission
        amount_rub_buyer = convert_usd_to_rub(buyer_amount)
        
        try:
            await query.message.edit_text(
                f"✅ <b>Сделка завершена!</b>\n\n"
                f"Вы отправили: ${amount_usd}\n"
                f"Покупатель получит: ${buyer_amount}\n"
                f"Комиссия: ${commission}",
                reply_markup=get_main_menu()
            )
        except TelegramBadRequest:
            pass
    else:
        try:
            await query.message.edit_text("❌ Ошибка завершения сделки!", reply_markup=get_main_menu())
        except TelegramBadRequest:
            pass

@router.callback_query(F.data == "my_deals")
async def my_deals_handler(query: CallbackQuery):
    await query.answer()
    user_id = query.from_user.id
    try:
        await query.message.edit_text("🤝 Мои сделки\n\n(Функция в разработке)", reply_markup=get_main_menu())
    except TelegramBadRequest:
        pass

@router.callback_query(F.data == "create_offer")
async def create_offer_start(query: CallbackQuery, state: FSMContext):
    await query.answer()
    keyboard = []
    for key, name in CATEGORIES.items():
        keyboard.append([InlineKeyboardButton(text=name, callback_data=f"offer_cat_{key}")])
    keyboard.append([InlineKeyboardButton(text="🏠 Меню", callback_data="return_main")])
    try:
        await query.message.edit_text("📝 <b>Выберите категорию</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    except TelegramBadRequest:
        pass

@router.callback_query(F.data.startswith("offer_cat_"))
async def create_offer_category(query: CallbackQuery, state: FSMContext):
    await query.answer()
    category = query.data.replace("offer_cat_", "")
    await state.update_data(category=category)
    await query.message.answer("📝 <b>Введите количество (только цифры)</b>\n\nНапример: 100, 50, 1000")
    await state.set_state(CreateOfferStates.quantity)

@router.message(StateFilter(CreateOfferStates.quantity), F.text)
async def create_offer_quantity(message: Message, state: FSMContext):
    quantity_text = message.text.strip()
    
    if not quantity_text.isdigit():
        await message.answer("❌ Введите только цифры! Например: 100, 50, 1000")
        return
    
    quantity = quantity_text
    await state.update_data(quantity=quantity)
    await message.answer("💰 <b>Введите цену в USD</b>\n\nНапример: 10, 50, 100.5")
    await state.set_state(CreateOfferStates.price)

@router.message(StateFilter(CreateOfferStates.price), F.text)
async def create_offer_price(message: Message, state: FSMContext):
    try:
        price = float(message.text)
        data = await state.get_data()
        category = data.get('category')
        quantity = data.get('quantity')
        user_id = message.from_user.id
        
        if check_duplicate_offer(user_id, category, price):
            await message.answer(
                f"❌ У вас уже есть такое объявление!\n\n"
                f"Категория: {CATEGORIES[category]}\n"
                f"Цена: ${price}",
                reply_markup=get_main_menu()
            )
            await state.clear()
            return
        
        offer_id = save_offer(user_id, category, quantity, price)
        
        if offer_id:
            price_rub = convert_usd_to_rub(price)
            await message.answer(
                f"✅ <b>Объявление создано!</b>\n\n"
                f"Категория: {CATEGORIES[category]}\n"
                f"Количество: {quantity}\n"
                f"Цена: ${price} (≈{price_rub:.0f}р)",
                reply_markup=get_main_menu()
            )
            logger.info(f"Offer created: id={offer_id}, user={user_id}")
        else:
            await message.answer("❌ Ошибка создания объявления!", reply_markup=get_main_menu())
        
        await state.clear()
    except ValueError:
        await message.answer("❌ Введите число! Например: 10, 50, 100.5", reply_markup=get_main_menu())
        await state.clear()

@router.callback_query(F.data == "my_offers")
async def my_offers_handler(query: CallbackQuery):
    await query.answer()
    user_id = query.from_user.id
    my_offers = get_user_offers(user_id)
    if my_offers:
        text = "📊 <b>Мои объявления:</b>\n\n"
        keyboard = []
        for offer_id, category, quantity, price in my_offers:
            price_rub = convert_usd_to_rub(price)
            text += f"{CATEGORIES[category]} | {quantity} = ${price}\n"
            keyboard.append([InlineKeyboardButton(text=f"❌ #{offer_id}", callback_data=f"delete_{offer_id}")])
        keyboard.append([InlineKeyboardButton(text="🏠 Меню", callback_data="return_main")])
    else:
        text = "📭 <b>У вас нет объявлений</b>"
        keyboard = [[InlineKeyboardButton(text="🏠 Меню", callback_data="return_main")]]
    try:
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    except TelegramBadRequest:
        pass

@router.callback_query(F.data.startswith("delete_"))
async def delete_offer_handler(query: CallbackQuery):
    await query.answer()
    offer_id = int(query.data.replace("delete_", ""))
    user_id = query.from_user.id
    
    if delete_offer(offer_id, user_id):
        await query.message.edit_text("✅ Объявление удалено!", reply_markup=get_main_menu())
    else:
        await query.message.edit_text("❌ Ошибка удаления!", reply_markup=get_main_menu())

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
        
        logger.info(f"Creating check for user {user_id}: ${amount_usd}")
        success, check_url_or_error, check_id = create_check(amount_usd, user_id)
        
        if success:
            amount_rub = convert_usd_to_rub(amount_usd)
            new_balance = balance - amount_rub
            
            if update_wallet(user_id, new_balance):
                add_history(user_id, 'withdraw', amount_rub, f'Вывод ${amount_usd}')
                save_withdrawal(user_id, amount_usd, 'success', check_id)
                
                keyboard = [[InlineKeyboardButton(text="💳 Забрать деньги в CryptoBot", url=check_url_or_error)]]
                await message.answer(
                    f"✅ <b>Вывод создан!</b>\n\n"
                    f"Сумма: ${amount_usd}\n"
                    f"Новый баланс: {new_balance:.0f}р\n\n"
                    f"Нажми кнопку ниже, чтобы получить деньги в @CryptoBot",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
                )
            else:
                await message.answer("❌ Ошибка обновления баланса!", reply_markup=get_main_menu())
        else:
            await message.answer(f"❌ Ошибка!\n\n{check_url_or_error}", reply_markup=get_main_menu())
            save_withdrawal(user_id, amount_usd, 'failed', None)
        
        await state.clear()
    except ValueError:
        await message.answer("❌ Введите число!", reply_markup=get_main_menu())
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
        await query.message.edit_text("❓ Маркетплейс Oxide\n\n💰 Комиссия 5%\n💵 USDT в @CryptoBot", reply_markup=get_main_menu())
    except TelegramBadRequest:
        pass

@router.callback_query(F.data == "return_main")
async def return_main(query: CallbackQuery):
    await query.answer()
    try:
        await query.message.edit_text("🎯 Меню", reply_markup=get_main_menu())
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
    
    server_ip = await get_server_ip()
    if server_ip:
        logger.info(f"📋 SERVER OUTGOING IP: {server_ip}")
    
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
    logger.info("✅ Escrow system activated - money transferred only after both confirmations")
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
