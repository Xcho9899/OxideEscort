import logging
import requests
import os
import asyncio
import psycopg2
from psycopg2 import pool
from datetime import datetime
from aiohttp import web, ClientSession
from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, StateFilter, Command
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
MODERATOR_ID = 8563207482
MODERATOR_USERNAME = "Aiassistant1"

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

class BanStates(StatesGroup):
    nickname = State()
    reason = State()

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
        CREATE TABLE IF NOT EXISTS user_profile (
            user_id BIGINT PRIMARY KEY,
            username VARCHAR(255),
            nickname VARCHAR(255),
            rating DECIMAL(2, 1) DEFAULT 2.0,
            completed_deals INT DEFAULT 0
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
        cursor.execute('DROP TABLE IF EXISTS deals')
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS deals (
            id SERIAL PRIMARY KEY,
            offer_id INT,
            seller_id BIGINT,
            buyer_id BIGINT,
            amount_usd DECIMAL(10, 2),
            category VARCHAR(50),
            quantity VARCHAR(255),
            status VARCHAR(50) DEFAULT 'pending',
            seller_confirmed BOOLEAN DEFAULT FALSE,
            buyer_confirmed BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS banned_players (
            id SERIAL PRIMARY KEY,
            nickname VARCHAR(255) UNIQUE,
            reason VARCHAR(500),
            banned_by BIGINT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        cursor.execute('''
        UPDATE user_profile SET rating = 2.0 WHERE user_id = %s
        ''', (MODERATOR_ID,))
        conn.commit()
        logger.info("✅ Database tables created")
    except Exception as e:
        logger.error(f"❌ Init error: {e}")
        conn.rollback()
    finally:
        cursor.close()
        return_db(conn)

def get_or_create_profile(user_id, username):
    conn = get_db()
    if not conn:
        logger.error(f"❌ No DB connection")
        return False
    cursor = conn.cursor()
    try:
        cursor.execute('''
        INSERT INTO user_profile (user_id, username) VALUES (%s, %s)
        ON CONFLICT (user_id) DO UPDATE SET username = EXCLUDED.username
        ''', (user_id, username))
        conn.commit()
        logger.info(f"✅ Profile created/updated: {user_id}")
        return True
    except Exception as e:
        logger.error(f"❌ Profile error: {e}")
        conn.rollback()
        return False
    finally:
        cursor.close()
        return_db(conn)

def get_profile(user_id):
    conn = get_db()
    if not conn:
        logger.error(f"❌ No DB connection for profile {user_id}")
        return None
    cursor = conn.cursor()
    try:
        cursor.execute('''
        SELECT user_id, username, nickname, rating, completed_deals FROM user_profile WHERE user_id = %s
        ''', (user_id,))
        result = cursor.fetchone()
        if result:
            logger.info(f"✅ Profile found: {user_id}")
            return result
        else:
            logger.warning(f"⚠️ Profile not found: {user_id}")
            return None
    except Exception as e:
        logger.error(f"❌ Get profile error: {e}")
        return None
    finally:
        cursor.close()
        return_db(conn)


    conn = get_db()
    if not conn:
        return []
    cursor = conn.cursor()
    try:
        cursor.execute('''
        SELECT invoice_id, amount_usd::double precision, status, created_at 
        FROM invoices WHERE user_id = %s AND status = %s
        ORDER BY created_at DESC
        ''', (user_id, 'pending'))
        return cursor.fetchall()
    except:
        return []
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
        return True
    except Exception as e:
        logger.error(f"❌ Update error: {e}")
        conn.rollback()
        return False
    finally:
        cursor.close()
        return_db(conn)

def delete_expired_invoices(user_id):
    """Удалить инвойсы которые не оплачены более 5 минут"""
    conn = get_db()
    if not conn:
        logger.error(f"❌ No DB connection for delete expired")
        return 0
    cursor = conn.cursor()
    try:
        cursor.execute('''
        DELETE FROM invoices 
        WHERE user_id = %s 
        AND status = %s 
        AND created_at < NOW() - INTERVAL '5 minutes'
        ''', (user_id, 'pending'))
        deleted_count = cursor.rowcount
        conn.commit()
        if deleted_count > 0:
            logger.info(f"✅ Deleted {deleted_count} expired invoices for user {user_id}")
        return deleted_count
    except Exception as e:
        logger.error(f"❌ Delete expired error: {e}")
        conn.rollback()
        return 0
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
        logger.info(f"✅ Withdrawal saved: user={user_id}, amount=${amount_usd}")
        return True
    except Exception as e:
        logger.error(f"❌ Withdrawal save error: {e}")
        conn.rollback()
        return False
    finally:
        cursor.close()
        return_db(conn)

def check_duplicate_offer(user_id, category):
    conn = get_db()
    if not conn:
        return False
    cursor = conn.cursor()
    try:
        cursor.execute('''
        SELECT id FROM offers 
        WHERE user_id = %s AND category = %s AND status = %s
        ''', (user_id, category, 'active'))
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

def save_deal(offer_id, seller_id, buyer_id, amount_usd, category, quantity):
    conn = get_db()
    if not conn:
        return None
    cursor = conn.cursor()
    try:
        cursor.execute('''
        INSERT INTO deals (offer_id, seller_id, buyer_id, amount_usd, category, quantity, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        ''', (offer_id, seller_id, buyer_id, amount_usd, category, quantity, 'pending'))
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
        SELECT id, offer_id, seller_id, buyer_id, amount_usd::double precision, category, quantity, status, seller_confirmed, buyer_confirmed 
        FROM deals WHERE id = %s
        ''', (deal_id,))
        return cursor.fetchone()
    except:
        return None
    finally:
        cursor.close()
        return_db(conn)

def get_seller_active_deals(seller_id):
    conn = get_db()
    if not conn:
        return []
    cursor = conn.cursor()
    try:
        cursor.execute('''
        SELECT id, buyer_id, amount_usd::double precision, category, quantity, status, buyer_confirmed 
        FROM deals WHERE seller_id = %s AND status = %s
        ORDER BY created_at DESC
        ''', (seller_id, 'pending'))
        return cursor.fetchall()
    except:
        return []
    finally:
        cursor.close()
        return_db(conn)

def get_buyer_active_deals(buyer_id):
    conn = get_db()
    if not conn:
        return []
    cursor = conn.cursor()
    try:
        cursor.execute('''
        SELECT id, seller_id, amount_usd::double precision, category, quantity, status, buyer_confirmed 
        FROM deals WHERE buyer_id = %s AND status = %s
        ORDER BY created_at DESC
        ''', (buyer_id, 'pending'))
        return cursor.fetchall()
    except:
        return []
    finally:
        cursor.close()
        return_db(conn)

def update_deal_confirmation(deal_id, role, confirmed):
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
        logger.info(f"✅ Deal confirmation: deal={deal_id}, role={role}")
        return True
    except Exception as e:
        logger.error(f"❌ Confirmation error: {e}")
        conn.rollback()
        return False
    finally:
        cursor.close()
        return_db(conn)

def complete_deal(deal_id):
    conn = get_db()
    if not conn:
        return False
    cursor = conn.cursor()
    try:
        cursor.execute('''
        SELECT seller_id, buyer_id, amount_usd::double precision, offer_id FROM deals WHERE id = %s
        ''', (deal_id,))
        result = cursor.fetchone()
        if not result:
            return False
        
        seller_id, buyer_id, amount_usd, offer_id = result
        rate = get_usdt_rub_rate()
        
        seller_balance = get_wallet(seller_id)
        amount_rub = convert_usd_to_rub(amount_usd)
        new_seller_balance = seller_balance - amount_rub
        update_wallet(seller_id, new_seller_balance)
        
        commission = amount_usd * 0.05
        buyer_amount = amount_usd - commission
        buyer_amount_rub = convert_usd_to_rub(buyer_amount)
        buyer_balance = get_wallet(buyer_id)
        new_buyer_balance = buyer_balance + buyer_amount_rub
        update_wallet(buyer_id, new_buyer_balance)
        
        cursor.execute("UPDATE deals SET status = %s WHERE id = %s", ('completed', deal_id))
        cursor.execute("DELETE FROM offers WHERE id = %s", (offer_id,))
        conn.commit()
        
        add_history(seller_id, 'deal_payment', amount_rub, f'Оплата за задание #{deal_id}')
        add_history(buyer_id, 'deal_complete', buyer_amount_rub, f'Получено за задание #{deal_id}')
        
        logger.info(f"✅ Deal completed: id={deal_id}")
        return True
    except Exception as e:
        logger.error(f"❌ Complete deal error: {e}")
        conn.rollback()
        return False
    finally:
        cursor.close()
        return_db(conn)

def update_user_rating(user_id, new_rating):
    conn = get_db()
    if not conn:
        return False
    cursor = conn.cursor()
    try:
        cursor.execute('''
        UPDATE user_profile SET rating = %s WHERE user_id = %s
        ''', (new_rating, user_id))
        conn.commit()
        logger.info(f"✅ Rating updated: user={user_id}, rating={new_rating}")
        return True
    except Exception as e:
        logger.error(f"❌ Rating update error: {e}")
        conn.rollback()
        return False
    finally:
        cursor.close()
        return_db(conn)

def add_completed_deal(user_id):
    conn = get_db()
    if not conn:
        return False
    cursor = conn.cursor()
    try:
        cursor.execute('''
        UPDATE user_profile SET completed_deals = completed_deals + 1 WHERE user_id = %s
        ''', (user_id,))
        conn.commit()
        logger.info(f"✅ Completed deals +1: user={user_id}")
        return True
    except Exception as e:
        logger.error(f"❌ Completed deals error: {e}")
        conn.rollback()
        return False
    finally:
        cursor.close()
        return_db(conn)

def ban_player(nickname, reason, banned_by):
    conn = get_db()
    if not conn:
        return False
    cursor = conn.cursor()
    try:
        cursor.execute('''
        INSERT INTO banned_players (nickname, reason, banned_by)
        VALUES (%s, %s, %s)
        ''', (nickname, reason, banned_by))
        conn.commit()
        logger.info(f"✅ Player banned: {nickname}")
        return True
    except:
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
                logger.info(f"🌍 SERVER IP: {server_ip}")
                return server_ip
    except:
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
        response = requests.post(f"{CRYPTOBOT_API}/createInvoice", headers=headers, json=payload, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get('ok'):
                invoice = data.get('result', {})
                invoice_id = invoice.get('invoice_id')
                pay_url = invoice.get('pay_url')
                save_invoice(invoice_id, user_id, amount_usd, 'pending')
                return pay_url, invoice_id
        return None, None
    except Exception as e:
        logger.error(f"Invoice error: {e}")
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
                    status = invoices[0].get('status')
                    return status == 'paid'
        return False
    except:
        return False

def create_check(amount_usd: float, user_id: int):
    try:
        logger.info(f"🔄 Creating check: ${amount_usd} for user {user_id}")
        headers = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN, "Content-Type": "application/json"}
        payload = {
            "asset": "USDT",
            "amount": str(amount_usd),
            "user_id": user_id,
            "description": "Вывод заработков из OxideEscort"
        }
        
        logger.info(f"📤 Check payload: {payload}")
        response = requests.post(f"{CRYPTOBOT_API}/createCheck", headers=headers, json=payload, timeout=10)
        
        logger.info(f"📥 CryptoBot status: {response.status_code}")
        logger.info(f"📥 CryptoBot response: {response.text}")
        
        if response.status_code == 200:
            data = response.json()
            if data.get('ok'):
                check_data = data.get('result', {})
                check_url = check_data.get('bot_check_url')
                check_id = check_data.get('check_id')
                logger.info(f"✅ Check created: {check_url}")
                return True, check_url, check_id
        
        logger.error(f"❌ Check creation failed: {response.text}")
        return False, response.text, None
    except Exception as e:
        logger.error(f"❌ Check error: {e}")
        return False, str(e), None

def get_main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛍️ Доска услуг", callback_data="board")],
        [InlineKeyboardButton(text="📝 Создать объявление", callback_data="create_offer")],
        [InlineKeyboardButton(text="📊 Мои объявления", callback_data="my_offers")],
        [InlineKeyboardButton(text="🎯 Активные сделки", callback_data="active_deals")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile")],
        [InlineKeyboardButton(text="💳 Кошелек", callback_data="wallet")],
        [InlineKeyboardButton(text="💰 Мои пополнения", callback_data="my_deposits")],
        [InlineKeyboardButton(text="📜 История", callback_data="history")],
        [InlineKeyboardButton(text="📚 Помощь", callback_data="help_menu")],
    ])

@router.message(Command("mod"))
async def mod_command(message: Message):
    user_id = message.from_user.id
    
    if user_id != MODERATOR_ID:
        await message.answer("❌ Только модератор может использовать эту команду!", reply_markup=get_main_menu())
        return
    
    await message.answer(
        "🔒 <b>ПАНЕЛЬ МОДЕРАТОРА</b>\n\n"
        f"Модератор: @{message.from_user.username or f'user{user_id}'}\n"
        f"ID: {user_id}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚫 Забанить игрока", callback_data="ban_player")],
            [InlineKeyboardButton(text="🏠 Меню", callback_data="return_main")]
        ])
    )

@router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    username = message.from_user.username or f"user{user_id}"
    
    # Гарантируем что профиль существует
    get_or_create_profile(user_id, username)
    
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
            await query.message.edit_text("📭 Нет предложений", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
        except TelegramBadRequest:
            pass
    else:
        keyboard = []
        for offer_id, user_id, quantity, price in offers:
            price_rub = convert_usd_to_rub(price)
            keyboard.append([InlineKeyboardButton(text=f"💰 {quantity} = ${price}", callback_data=f"view_{offer_id}")])
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
            await query.message.edit_text("❌ Не найдено!", reply_markup=get_main_menu())
        except TelegramBadRequest:
            pass
        return
    
    offer_id_db, seller_id, category, quantity, price = offer
    
    if buyer_id == seller_id:
        try:
            await query.message.edit_text("❌ Ваше объявление!", reply_markup=get_main_menu())
        except TelegramBadRequest:
            pass
        return
    
    # Гарантируем что профили существуют
    get_or_create_profile(buyer_id, query.from_user.username or f"user{buyer_id}")
    get_or_create_profile(seller_id, "unknown")
    
    price_rub = convert_usd_to_rub(price)
    text = f"📋 <b>Задание</b>\n\n" \
            f"📌 {CATEGORIES[category]}\n" \
            f"Количество: {quantity}\n" \
            f"💰 ${price}"
    
    keyboard = [
        [InlineKeyboardButton(text="✅ Принять", callback_data=f"accept_{offer_id_db}")],
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
            await query.message.edit_text("❌ Не найдено!", reply_markup=get_main_menu())
        except TelegramBadRequest:
            pass
        return
    
    offer_id_db, seller_id, category, quantity, price = offer
    
    deal_id = save_deal(offer_id_db, seller_id, buyer_id, price, category, quantity)
    
    if deal_id:
        delete_offer(offer_id_db, seller_id)
        
        price_rub = convert_usd_to_rub(price)
        buyer_username = query.from_user.username or f"user{buyer_id}"
        
        try:
            await bot.send_message(
                seller_id,
                f"🔔 <b>НОВОЕ ЗАДАНИЕ!</b>\n\n"
                f"#{deal_id} | {CATEGORIES[category]} | {quantity} | ${price}\n"
                f"Исполнитель: @{buyer_username}\n\n"
                f"Смотрите в 'Активные сделки'!",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="👤 Профиль исполнителя", callback_data=f"profile_{buyer_id}")],
                    [InlineKeyboardButton(text="🔙 На доску", callback_data="board")]
                ])
            )
        except:
            pass
        
        try:
            await query.message.edit_text(
                f"✅ <b>Принято!</b>\n\n"
                f"#{deal_id} | {CATEGORIES[category]}\n"
                f"${price}\n\n"
                f"Смотрите в 'Активные сделки'",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="👤 Профиль продавца", callback_data=f"profile_{seller_id}")],
                    [InlineKeyboardButton(text="🎯 Перейти в активные сделки", callback_data="active_deals")],
                    [InlineKeyboardButton(text="🏠 Меню", callback_data="return_main")]
                ])
            )
        except TelegramBadRequest:
            pass
    else:
        try:
            await query.message.edit_text("❌ Ошибка!", reply_markup=get_main_menu())
        except TelegramBadRequest:
            pass

@router.callback_query(F.data.startswith("profile_"))
async def view_profile_handler(query: CallbackQuery):
    await query.answer()
    user_id = int(query.data.replace("profile_", ""))
    
    # Гарантируем что профиль существует
    get_or_create_profile(user_id, f"user{user_id}")
    
    profile = get_profile(user_id)
    
    if profile:
        user_id_db, username, nickname, rating, completed_deals = profile
        text = f"👤 <b>Профиль</b>\n\n" \
                f"@{username or f'user{user_id}'}\n" \
                f"Ник: {nickname or 'Не установлен'}\n" \
                f"⭐ {rating}/3.0\n" \
                f"✅ Сделок: {completed_deals}"
        keyboard = [[InlineKeyboardButton(text="⬅️ Назад", callback_data="return_main")]]
    else:
        text = f"👤 <b>Профиль</b>\n\n" \
                f"@user{user_id}\n" \
                f"Ник: Не установлен\n" \
                f"⭐ 3.0/3.0\n" \
                f"✅ Сделок: 0"
        keyboard = [[InlineKeyboardButton(text="⬅️ Назад", callback_data="return_main")]]
    
    try:
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    except TelegramBadRequest:
        pass

@router.callback_query(F.data == "active_deals")
async def active_deals_handler(query: CallbackQuery):
    await query.answer()
    user_id = query.from_user.id
    
    seller_deals = get_seller_active_deals(user_id)
    buyer_deals = get_buyer_active_deals(user_id)
    
    text = ""
    keyboard = []
    
    if seller_deals:
        text += "📦 <b>СДЕЛКИ ГДЕ ТЫ ПРОДАВЕЦ:</b>\n\n"
        for deal_id, buyer_id, amount_usd, category, quantity, status, buyer_confirmed in seller_deals:
            status_icon = "✅" if buyer_confirmed else "⏳"
            text += f"#{deal_id} | {CATEGORIES.get(category, category)} | {quantity} | ${amount_usd} | @{buyer_id} | {status_icon}\n"
            keyboard.append([InlineKeyboardButton(text=f"Сделка #{deal_id} (Продавец)", callback_data=f"seller_deal_{deal_id}")])
    
    if buyer_deals:
        if text:
            text += "\n"
        text += "🎯 <b>СДЕЛКИ ГДЕ ТЫ ИСПОЛНИТЕЛЬ:</b>\n\n"
        for deal_id, seller_id, amount_usd, category, quantity, status, buyer_confirmed in buyer_deals:
            status_icon = "✅" if buyer_confirmed else "⏳"
            text += f"#{deal_id} | {CATEGORIES.get(category, category)} | {quantity} | ${amount_usd} | @{seller_id} | {status_icon}\n"
            keyboard.append([InlineKeyboardButton(text=f"Сделка #{deal_id} (Исполнитель)", callback_data=f"buyer_deal_{deal_id}")])
    
    if not seller_deals and not buyer_deals:
        text = "📭 <b>Нет активных сделок</b>"
    
    keyboard.append([InlineKeyboardButton(text="🏠 Меню", callback_data="return_main")])
    
    try:
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    except TelegramBadRequest:
        pass

@router.callback_query(F.data.startswith("seller_deal_"))
async def seller_deal_handler(query: CallbackQuery):
    await query.answer()
    deal_id = int(query.data.replace("seller_deal_", ""))
    seller_id = query.from_user.id
    
    deal = get_deal(deal_id)
    if not deal:
        try:
            await query.message.edit_text("❌ Не найдено!", reply_markup=get_main_menu())
        except TelegramBadRequest:
            pass
        return
    
    deal_id_db, offer_id, seller_id_db, buyer_id, amount_usd, category, quantity, status, seller_confirmed, buyer_confirmed = deal
    
    if seller_id != seller_id_db:
        try:
            await query.message.edit_text("❌ Не ваша сделка!", reply_markup=get_main_menu())
        except TelegramBadRequest:
            pass
        return
    
    text = f"📦 <b>Сделка #{deal_id}</b>\n\n" \
            f"📌 {CATEGORIES.get(category, category)}\n" \
            f"Количество: {quantity}\n" \
            f"💰 ${amount_usd}\n" \
            f"Исполнитель: @{buyer_id}\n\n"
    
    keyboard = []
    
    if not buyer_confirmed:
        text += "⏳ Исполнитель работает..."
        keyboard.append([InlineKeyboardButton(text="👤 Профиль", callback_data=f"profile_{buyer_id}")])
    else:
        text += "✅ <b>ЗАДАНИЕ ЗАВЕРШЕНО!</b>\n\n"
        text += "Исполнитель подтвердил выполнение.\n"
        text += "Нажмите кнопку ниже для подтверждения."
        keyboard.append([InlineKeyboardButton(text="✅ Подтверждаю", callback_data=f"approve_deal_{deal_id}")])
        keyboard.append([InlineKeyboardButton(text="⚠️ К модератору", callback_data="contact_mod")])
    
    keyboard.append([InlineKeyboardButton(text="🎯 Активные сделки", callback_data="active_deals")])
    
    try:
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    except TelegramBadRequest:
        pass

@router.callback_query(F.data.startswith("buyer_deal_"))
async def buyer_deal_handler(query: CallbackQuery):
    await query.answer()
    deal_id = int(query.data.replace("buyer_deal_", ""))
    buyer_id = query.from_user.id
    
    deal = get_deal(deal_id)
    if not deal:
        try:
            await query.message.edit_text("❌ Не найдено!", reply_markup=get_main_menu())
        except TelegramBadRequest:
            pass
        return
    
    deal_id_db, offer_id, seller_id, buyer_id_db, amount_usd, category, quantity, status, seller_confirmed, buyer_confirmed = deal
    
    if buyer_id != buyer_id_db:
        try:
            await query.message.edit_text("❌ Не ваша сделка!", reply_markup=get_main_menu())
        except TelegramBadRequest:
            pass
        return
    
    text = f"🎯 <b>Сделка #{deal_id}</b>\n\n" \
            f"📌 {CATEGORIES.get(category, category)}\n" \
            f"Количество: {quantity}\n" \
            f"💰 ${amount_usd}\n" \
            f"Продавец: @{seller_id}\n\n"
    
    keyboard = []
    
    if not buyer_confirmed:
        text += "⏳ <b>Работаю...</b>"
        keyboard.append([InlineKeyboardButton(text="✅ Задание завершено", callback_data=f"task_done_{deal_id}")])
    else:
        if seller_confirmed:
            text += "✅ <b>СДЕЛКА ЗАВЕРШЕНА!</b>\n\nВы получили деньги!"
        else:
            text += "✅ Вы подтвердили\n⏳ Ожидание проверки..."
    
    keyboard.append([InlineKeyboardButton(text="👤 Профиль продавца", callback_data=f"profile_{seller_id}")])
    keyboard.append([InlineKeyboardButton(text="🎯 Активные сделки", callback_data="active_deals")])
    
    try:
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    except TelegramBadRequest:
        pass

@router.callback_query(F.data.startswith("task_done_"))
async def task_done_handler(query: CallbackQuery):
    await query.answer()
    deal_id = int(query.data.replace("task_done_", ""))
    buyer_id = query.from_user.id
    
    deal = get_deal(deal_id)
    if not deal:
        try:
            await query.message.edit_text("❌ Не найдено!", reply_markup=get_main_menu())
        except TelegramBadRequest:
            pass
        return
    
    deal_id_db, offer_id, seller_id, buyer_id_db, amount_usd, category, quantity, status, seller_confirmed, buyer_confirmed = deal
    
    update_deal_confirmation(deal_id, 'buyer', True)
    
    try:
        await bot.send_message(
            seller_id,
            f"🔔 <b>ИСПОЛНИТЕЛЬ ГОТОВ!</b>\n\n"
            f"#{deal_id} | {CATEGORIES.get(category, category)}\n"
            f"${amount_usd}\n\n"
            f"⚠️ Проверьте в 'Активные сделки' и подтвердите!"
        )
    except:
        pass
    
    try:
        await query.message.edit_text(
            f"✅ <b>Вы подтвердили!</b>\n\n"
            f"#{deal_id}\n\n"
            f"⏳ Ожидание продавца...",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🎯 Перейти в активные сделки", callback_data="active_deals")],
                [InlineKeyboardButton(text="🏠 Меню", callback_data="return_main")]
            ])
        )
    except TelegramBadRequest:
        pass

@router.callback_query(F.data.startswith("rate_"))
async def rate_handler(query: CallbackQuery):
    await query.answer()
    parts = query.data.replace("rate_", "").split("_")
    deal_id = int(parts[0])
    buyer_id = int(parts[1])
    rating = int(parts[2])
    
    profile = get_profile(buyer_id)
    if profile:
        user_id_db, username, nickname, old_rating, completed_deals = profile
        
        new_rating = round((old_rating + rating) / 2, 2)
        
        if update_user_rating(buyer_id, new_rating):
            try:
                await query.message.edit_text(
                    f"✅ <b>СПАСИБО!</b>\n\n"
                    f"Вы оценили исполнителя на {rating}⭐\n"
                    f"Новый рейтинг: {new_rating}/3.0",
                    reply_markup=get_main_menu()
                )
            except TelegramBadRequest:
                pass
        else:
            try:
                await query.message.edit_text("❌ Ошибка оценки!", reply_markup=get_main_menu())
            except TelegramBadRequest:
                pass
    else:
        try:
            await query.message.edit_text("❌ Профиль не найден!", reply_markup=get_main_menu())
        except TelegramBadRequest:
            pass

@router.callback_query(F.data.startswith("approve_deal_"))
async def approve_deal_handler(query: CallbackQuery):
    await query.answer()
    deal_id = int(query.data.replace("approve_deal_", ""))
    seller_id = query.from_user.id
    
    deal = get_deal(deal_id)
    if not deal:
        try:
            await query.message.edit_text("❌ Не найдено!", reply_markup=get_main_menu())
        except TelegramBadRequest:
            pass
        return
    
    deal_id_db, offer_id, seller_id_db, buyer_id, amount_usd, category, quantity, status, seller_confirmed, buyer_confirmed = deal
    
    if seller_id != seller_id_db:
        try:
            await query.message.edit_text("❌ Вы не продавец!", reply_markup=get_main_menu())
        except TelegramBadRequest:
            pass
        return
    
    # ПРЕДУПРЕЖДЕНИЕ ДО ПОДТВЕРЖДЕНИЯ
    try:
        await query.message.edit_text(
            f"⚠️ <b>ВНИМАНИЕ!</b>\n\n"
            f"Вы уверены что задание #{deal_id} выполнено?\n\n"
            f"{CATEGORIES.get(category, category)}\n"
            f"${amount_usd}\n\n"
            f"❗ После подтверждения деньги будут отправлены исполнителю!",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ ДА, ПОДТВЕРЖДАЮ", callback_data=f"confirm_deal_{deal_id}")],
                [InlineKeyboardButton(text="❌ ОТМЕНА", callback_data="active_deals")],
            ])
        )
    except TelegramBadRequest:
        pass

@router.callback_query(F.data.startswith("confirm_deal_"))
async def confirm_deal_final(query: CallbackQuery):
    await query.answer()
    deal_id = int(query.data.replace("confirm_deal_", ""))
    seller_id = query.from_user.id
    
    deal = get_deal(deal_id)
    if not deal:
        try:
            await query.message.edit_text("❌ Не найдено!", reply_markup=get_main_menu())
        except TelegramBadRequest:
            pass
        return
    
    deal_id_db, offer_id, seller_id_db, buyer_id, amount_usd, category, quantity, status, seller_confirmed, buyer_confirmed = deal
    
    if seller_id != seller_id_db:
        try:
            await query.message.edit_text("❌ Вы не продавец!", reply_markup=get_main_menu())
        except TelegramBadRequest:
            pass
        return
    
    rate = get_usdt_rub_rate()
    seller_balance = get_wallet(seller_id)
    amount_rub = convert_usd_to_rub(amount_usd)
    
    if seller_balance < amount_rub:
        try:
            await query.message.edit_text(
                f"❌ Недостаточно денег!\n\n"
                f"Нужно: ${amount_usd}\n"
                f"Баланс: ${seller_balance / rate:.2f}",
                reply_markup=get_main_menu()
            )
        except TelegramBadRequest:
            pass
        return
    
    if complete_deal(deal_id):
        commission = amount_usd * 0.05
        buyer_amount = amount_usd - commission
        
        add_completed_deal(seller_id)
        add_completed_deal(buyer_id)
        
        try:
            await bot.send_message(
                buyer_id,
                f"✅ <b>СДЕЛКА ЗАВЕРШЕНА!</b>\n\n"
                f"#{deal_id}\n"
                f"Вы получили: ${buyer_amount}\n"
                f"Спасибо!"
            )
        except:
            pass
        
        keyboard = [
            [InlineKeyboardButton(text="⭐", callback_data=f"rate_{deal_id}_{buyer_id}_1")],
            [InlineKeyboardButton(text="⭐⭐", callback_data=f"rate_{deal_id}_{buyer_id}_2")],
            [InlineKeyboardButton(text="⭐⭐⭐", callback_data=f"rate_{deal_id}_{buyer_id}_3")],
            [InlineKeyboardButton(text="🎯 Активные сделки", callback_data="active_deals")],
            [InlineKeyboardButton(text="🏠 Меню", callback_data="return_main")]
        ]
        
        try:
            await query.message.edit_text(
                f"✅ <b>ЗАВЕРШЕНО!</b>\n\n"
                f"Вы отправили: ${amount_usd}\n"
                f"Исполнитель получил: ${buyer_amount}\n"
                f"Комиссия: ${commission}\n\n"
                f"⭐ <b>Оцените исполнителя (1-3⭐):</b>",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
            )
        except TelegramBadRequest:
            pass
    else:
        try:
            await query.message.edit_text("❌ Ошибка!", reply_markup=get_main_menu())
        except TelegramBadRequest:
            pass

@router.callback_query(F.data == "my_deposits")
async def my_deposits_handler(query: CallbackQuery):
    await query.answer()
    user_id = query.from_user.id
    
    # Удалить инвойсы старше 5 минут
    delete_expired_invoices(user_id)
    
    # Получить оставшиеся пополнения
    conn = get_db()
    if not conn:
        try:
            await query.message.edit_text("❌ Ошибка БД!", reply_markup=get_main_menu())
        except TelegramBadRequest:
            pass
        return
    
    cursor = conn.cursor()
    try:
        cursor.execute('''
        SELECT invoice_id, amount_usd::double precision, status, created_at 
        FROM invoices WHERE user_id = %s AND status = %s
        ORDER BY created_at DESC
        ''', (user_id, 'pending'))
        pending_invoices = cursor.fetchall()
    except:
        pending_invoices = []
    finally:
        cursor.close()
        return_db(conn)
    
    if not pending_invoices:
        try:
            await query.message.edit_text(
                "💰 <b>Мои пополнения</b>\n\n"
                "✅ Нет ожидающих пополнений",
                reply_markup=get_main_menu()
            )
        except TelegramBadRequest:
            pass
    else:
        text = "💰 <b>Мои пополнения (активные 5 мин):</b>\n\n"
        keyboard = []
        
        for invoice_id, amount_usd, status, created_at in pending_invoices:
            text += f"💵 ${amount_usd} | ⏳ {status}\n"
            keyboard.append([InlineKeyboardButton(text=f"✅ Проверить #{invoice_id}", callback_data=f"check_{invoice_id}")])
        
        keyboard.append([InlineKeyboardButton(text="🏠 Меню", callback_data="return_main")])
        
        try:
            await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
        except TelegramBadRequest:
            pass

@router.callback_query(F.data == "help_menu")
async def help_menu_handler(query: CallbackQuery):
    await query.answer()
    try:
        await query.message.edit_text(
            "📚 <b>ПОМОЩЬ И РУКОВОДСТВА</b>\n\n"
            "Выберите тему:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💳 Как пополнить баланс?", callback_data="help_deposit")],
                [InlineKeyboardButton(text="💰 Как вывести деньги?", callback_data="help_withdraw")],
                [InlineKeyboardButton(text="🎯 Как выполнять задания?", callback_data="help_tasks")],
                [InlineKeyboardButton(text="📝 Как создать объявление?", callback_data="help_create_offer")],
                [InlineKeyboardButton(text="⭐ Как работает рейтинг?", callback_data="help_rating")],
                [InlineKeyboardButton(text="📞 Написать поддержке", url=f"https://t.me/{MODERATOR_USERNAME}")],
                [InlineKeyboardButton(text="🏠 Меню", callback_data="return_main")]
            ])
        )
    except TelegramBadRequest:
        pass

@router.callback_query(F.data == "help_deposit")
async def help_deposit_handler(query: CallbackQuery):
    await query.answer()
    try:
        await query.message.edit_text(
            "💳 <b>КАК ПОПОЛНИТЬ БАЛАНС?</b>\n\n"
            "1️⃣ Нажмите кнопку '💳 Кошелек' в меню\n\n"
            "2️⃣ Нажмите '💳 Пополнить'\n\n"
            "3️⃣ Введите сумму в рублях\n"
            "   💡 Минимум: 81р (~$1)\n\n"
            "4️⃣ Нажмите '💳 Оплатить'\n"
            "   Откроется платеж через CryptoBot\n\n"
            "5️⃣ Оплатите счет в @CryptoBot\n\n"
            "6️⃣ Вернитесь в бот и нажмите '✅ Проверить платеж'\n\n"
            "7️⃣ Деньги зачислятся на баланс! ✅\n\n"
            "💡 Платежи приходят мгновенно!\n"
            "💡 У вас нет счета в CryptoBot? Он создастся автоматически!",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📚 Другие разделы", callback_data="help_menu")],
                [InlineKeyboardButton(text="🏠 Меню", callback_data="return_main")]
            ])
        )
    except TelegramBadRequest:
        pass

@router.callback_query(F.data == "help_withdraw")
async def help_withdraw_handler(query: CallbackQuery):
    await query.answer()
    try:
        await query.message.edit_text(
            "💰 <b>КАК ВЫВЕСТИ ДЕНЬГИ?</b>\n\n"
            "1️⃣ Нажмите '💳 Кошелек' в меню\n\n"
            "2️⃣ Нажмите '💰 Вывести'\n\n"
            "3️⃣ Введите сумму в долларах (USD)\n"
            "   💡 Минимум: $1\n"
            "   💡 Максимум: ваш баланс\n\n"
            "4️⃣ Будет создан чек для вывода\n\n"
            "5️⃣ Нажмите '💳 Забрать в CryptoBot'\n"
            "   Откроется ваш кошелек в @CryptoBot\n\n"
            "6️⃣ Деньги придут на ваш счет! ✅\n\n"
            "⚠️ ВАЖНО:\n"
            "• Вывод работает в USDT (криптовалюта)\n"
            "• Комиссия платформы: 5%\n"
            "• Деньги приходят за 1-2 минуты\n\n"
            "Пример:\n"
            "Вывод $10 → вы получите $9.50\n"
            "$0.50 комиссия платформы",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📚 Другие разделы", callback_data="help_menu")],
                [InlineKeyboardButton(text="🏠 Меню", callback_data="return_main")]
            ])
        )
    except TelegramBadRequest:
        pass

@router.callback_query(F.data == "help_tasks")
async def help_tasks_handler(query: CallbackQuery):
    await query.answer()
    try:
        await query.message.edit_text(
            "🎯 <b>КАК ВЫПОЛНЯТЬ ЗАДАНИЯ?</b>\n\n"
            "<b>ВАРИАНТ 1: Чужие задания (исполнитель)</b>\n\n"
            "1️⃣ Нажмите '🛍️ Доска услуг'\n\n"
            "2️⃣ Выберите категорию услуги\n"
            "   ⚒️ Фарм | 🏗️ Постройка | 🛡️ Помощь и другие\n\n"
            "3️⃣ Смотрите доступные задания\n"
            "   💰 $10 | 100 серы | 🏗️ Постройка\n\n"
            "4️⃣ Нажмите '✅ Принять'\n\n"
            "5️⃣ Выполните работу в игре\n\n"
            "6️⃣ Нажмите '✅ Задание завершено'\n\n"
            "7️⃣ Продавец проверит и подтвердит\n\n"
            "8️⃣ Вы получите деньги - 5% комиссия\n\n"
            "---\n\n"
            "<b>ВАРИАНТ 2: Свои объявления (продавец)</b>\n\n"
            "1️⃣ Нажмите '📝 Создать объявление'\n\n"
            "2️⃣ Выберите категорию услуги\n\n"
            "3️⃣ Введите количество (100, 500, 1000)\n\n"
            "4️⃣ Введите цену в долларах ($10, $50)\n\n"
            "5️⃣ Объявление появится на доске\n\n"
            "6️⃣ Люди принимают ваше задание\n\n"
            "7️⃣ После выполнения подтвердите\n\n"
            "8️⃣ Деньги уходят из вашего баланса\n\n"
            "💡 Или делайте ОБА ВАРИАНТА одновременно!",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📚 Другие разделы", callback_data="help_menu")],
                [InlineKeyboardButton(text="🏠 Меню", callback_data="return_main")]
            ])
        )
    except TelegramBadRequest:
        pass

@router.callback_query(F.data == "help_create_offer")
async def help_create_offer_handler(query: CallbackQuery):
    await query.answer()
    try:
        await query.message.edit_text(
            "📝 <b>КАК СОЗДАТЬ ОБЪЯВЛЕНИЕ?</b>\n\n"
            "1️⃣ Нажмите '📝 Создать объявление'\n\n"
            "2️⃣ Выберите категорию услуги:\n"
            "   ⚒️ Фарм серы / Фарм металла\n"
            "   🔩 Фарм дерева / 🏗️ Постройка\n"
            "   ⛽ Фарм топливо / 🛡️ Помощь в рейдах\n"
            "   🔧 Фарм металалома / 🔫 Установка турелей\n"
            "   🚪 Скидка шкафа\n\n"
            "3️⃣ Введите количество (только цифры)\n"
            "   Примеры: 100, 500, 1000, 5000\n\n"
            "4️⃣ Введите цену в долларах\n"
            "   Примеры: $10, $50, $100\n\n"
            "5️⃣ Объявление создано! ✅\n\n"
            "ℹ️ ВАЖНО:\n"
            "• У вас может быть только 1 объявление в категории\n"
            "• Снять объявление можно в разделе 'Мои объявления'\n"
            "• Когда кто-то принимает → объявление удаляется\n"
            "• Оставшиеся деньги на балансе в эскроу\n\n"
            "💡 ПРИМЕРЫ ПРЕДЛОЖЕНИЙ:\n"
            "⚒️ Фарм серы 500шт за $25\n"
            "🏗️ Постройка базы за $50\n"
            "🛡️ Помощь в рейдах за $15",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📚 Другие разделы", callback_data="help_menu")],
                [InlineKeyboardButton(text="🏠 Меню", callback_data="return_main")]
            ])
        )
    except TelegramBadRequest:
        pass

@router.callback_query(F.data == "help_rating")
async def help_rating_handler(query: CallbackQuery):
    await query.answer()
    try:
        await query.message.edit_text(
            "⭐ <b>КАК РАБОТАЕТ РЕЙТИНГ?</b>\n\n"
            "<b>ЧТО ТАКОЕ РЕЙТИНГ?</b>\n"
            "Это оценка вашей репутации в системе!\n\n"
            "<b>НАЧАЛЬНЫЙ РЕЙТИНГ: 2.0⭐</b>\n\n"
            "<b>МАКСИМАЛЬНЫЙ РЕЙТИНГ: 3.0⭐</b>\n\n"
            "<b>КАК ПОВЫСИТЬ РЕЙТИНГ?</b>\n"
            "1️⃣ Выполняйте задания хорошо\n"
            "2️⃣ После каждой завершенной сделки\n"
            "   продавец оценивает вас (1, 2 или 3⭐)\n"
            "3️⃣ Ваш новый рейтинг = (старый + оценка) / 2\n\n"
            "<b>ПРИМЕРЫ РАСЧЕТА:</b>\n"
            "Рейтинг: 2.0⭐\n"
            "Оценка: 3⭐ (отлично)\n"
            "Новый рейтинг = (2.0 + 3) / 2 = 2.5⭐ ✅\n\n"
            "Рейтинг: 2.5⭐\n"
            "Оценка: 3⭐ (отлично)\n"
            "Новый рейтинг = (2.5 + 3) / 2 = 2.75⭐\n\n"
            "Рейтинг: 2.75⭐\n"
            "Оценка: 3⭐ (отлично)\n"
            "Новый рейтинг = (2.75 + 3) / 2 = 2.88⭐\n\n"
            "Рейтинг: 2.0⭐\n"
            "Оценка: 1⭐ (плохо)\n"
            "Новый рейтинг = (2.0 + 1) / 2 = 1.5⭐\n\n"
            "<b>ЗАЧЕМ НУЖЕН ВЫСОКИЙ РЕЙТИНГ?</b>\n"
            "✅ Люди охотнее берут ваши задания\n"
            "✅ Вы кажетесь надежнее\n"
            "✅ Больше доверия от других игроков\n"
            "✅ Рост репутации в сообществе\n\n"
            "<b>РЕЙТИНГ ВИДЯТ ВСЕ!</b>\n"
            "При просмотре профиля видно:\n"
            "👤 @username\n"
            "⭐ 2.8/3.0 ← ВОТ ЗДЕСЬ!\n"
            "✅ Сделок: 25",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📚 Другие разделы", callback_data="help_menu")],
                [InlineKeyboardButton(text="🏠 Меню", callback_data="return_main")]
            ])
        )
    except TelegramBadRequest:
        pass

@router.callback_query(F.data == "contact_mod")
async def contact_mod_handler(query: CallbackQuery):
    await query.answer()
    profile = get_profile(MODERATOR_ID)
    
    if profile:
        user_id_db, username, nickname, rating, completed_deals = profile
        text = f"📞 <b>МОДЕРАТОР</b>\n\n" \
                f"@{username}\n" \
                f"Ник: {nickname or 'Не установлен'}\n" \
                f"⭐ {rating}/3.0\n" \
                f"✅ Сделок: {completed_deals}\n\n" \
                f"Обратитесь к модератору если возникли проблемы"
    else:
        text = "📞 <b>МОДЕРАТОР</b>\n\nПрофиль не найден"
    
    try:
        await query.message.edit_text(text, reply_markup=get_main_menu())
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
    user_id = query.from_user.id
    
    if check_duplicate_offer(user_id, category):
        try:
            await query.message.edit_text(
                f"❌ У вас уже есть!\n\n"
                f"{CATEGORIES[category]}",
                reply_markup=get_main_menu()
            )
        except TelegramBadRequest:
            pass
        return
    
    await state.update_data(category=category)
    await query.message.answer("📝 Количество (цифры):")
    await state.set_state(CreateOfferStates.quantity)

@router.message(StateFilter(CreateOfferStates.quantity), F.text)
async def create_offer_quantity(message: Message, state: FSMContext):
    quantity_text = message.text.strip()
    
    if not quantity_text.isdigit():
        await message.answer("❌ Только цифры!")
        return
    
    await state.update_data(quantity=quantity_text)
    await message.answer("💰 Цена (USD):")
    await state.set_state(CreateOfferStates.price)

@router.message(StateFilter(CreateOfferStates.price), F.text)
async def create_offer_price(message: Message, state: FSMContext):
    try:
        price = float(message.text)
        data = await state.get_data()
        category = data.get('category')
        quantity = data.get('quantity')
        user_id = message.from_user.id
        
        rate = get_usdt_rub_rate()
        balance = get_wallet(user_id)
        balance_usd = balance / rate if rate else 0
        
        if balance_usd < price:
            await message.answer(
                f"❌ Недостаточно денег!\n\n"
                f"Нужно: ${price}\n"
                f"Баланс: ${balance_usd:.2f}\n\n"
                f"Пополните в 'Кошелек'",
                reply_markup=get_main_menu()
            )
            await state.clear()
            return
        
        offer_id = save_offer(user_id, category, quantity, price)
        
        if offer_id:
            await message.answer(
                f"✅ <b>Создано!</b>\n\n"
                f"{CATEGORIES[category]}\n"
                f"Кол-во: {quantity}\n"
                f"Цена: ${price}",
                reply_markup=get_main_menu()
            )
        else:
            await message.answer("❌ Ошибка!", reply_markup=get_main_menu())
        
        await state.clear()
    except ValueError:
        await message.answer("❌ Число!", reply_markup=get_main_menu())
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
            text += f"{CATEGORIES[category]} | {quantity} | ${price}\n"
            keyboard.append([InlineKeyboardButton(text=f"❌ #{offer_id}", callback_data=f"delete_{offer_id}")])
        keyboard.append([InlineKeyboardButton(text="🏠 Меню", callback_data="return_main")])
    else:
        text = "📭 Нет"
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
        await query.message.edit_text("✅ Удалено!", reply_markup=get_main_menu())
    else:
        await query.message.edit_text("❌ Ошибка!", reply_markup=get_main_menu())

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
        await query.message.edit_text(f"💳 <b>Кошелек</b>\n\n💵 {balance:.0f}р (${balance_usd:.2f})", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    except TelegramBadRequest:
        pass

@router.callback_query(F.data == "deposit")
async def deposit_start(query: CallbackQuery, state: FSMContext):
    await query.answer()
    await query.message.answer("💵 Сумма (рубли):\n\nМин: 81р")
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
                [InlineKeyboardButton(text="💰 Мои пополнения", callback_data="my_deposits")],
                [InlineKeyboardButton(text="🏠 Меню", callback_data="return_main")]
            ]
            await message.answer(f"💵 {amount_rub:.0f}р = ${amount_usd}\n\n⏳ Ожидание оплаты...", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
            await state.clear()
        else:
            await message.answer("❌ Ошибка!", reply_markup=get_main_menu())
            await state.clear()
    except ValueError:
        await message.answer("❌ Число!", reply_markup=get_main_menu())
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
            await query.message.edit_text(
                f"❌ Минимум $1\n\n"
                f"Баланс: ${balance_usd:.2f}",
                reply_markup=get_main_menu()
            )
        except TelegramBadRequest:
            pass
    else:
        keyboard = [
            [InlineKeyboardButton(text="MAX", callback_data=f"withdraw_max_{balance_usd:.2f}")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="wallet")],
            [InlineKeyboardButton(text="🏠 Меню", callback_data="return_main")]
        ]
        await query.message.answer(
            f"💰 Сумма (USD):\n\n"
            f"💵 Баланс: ${balance_usd:.2f}\n"
            f"💡 Минимум: $1",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
        )
        await state.set_state(WithdrawStates.amount)

@router.callback_query(F.data.startswith("withdraw_max_"))
async def withdraw_max_handler(query: CallbackQuery, state: FSMContext):
    await query.answer()
    user_id = query.from_user.id
    
    # Парсим максимальную сумму из callback_data
    max_amount_str = query.data.replace("withdraw_max_", "")
    
    try:
        amount_usd = float(max_amount_str)
    except ValueError:
        try:
            await query.message.edit_text("❌ Ошибка!", reply_markup=get_main_menu())
        except TelegramBadRequest:
            pass
        await state.clear()
        return
    
    logger.info(f"🔄 MAX Withdraw from user {user_id}: ${amount_usd}")
    
    try:
        rate = get_usdt_rub_rate()
        balance = get_wallet(user_id)
        balance_usd = balance / rate if rate else 0
        
        logger.info(f"📊 Balance: ${balance_usd:.2f}, max_amount: ${amount_usd}")
        
        if amount_usd < 1:
            await query.message.edit_text("❌ Минимум $1!", reply_markup=get_main_menu())
            await state.clear()
            return
        
        if amount_usd > balance_usd:
            await query.message.edit_text(f"❌ Недостаточно!\n\nБаланс: ${balance_usd:.2f}", reply_markup=get_main_menu())
            await state.clear()
            return
        
        logger.info(f"🔄 Creating check for user {user_id}, amount ${amount_usd}")
        success, check_url_or_error, check_id = create_check(amount_usd, user_id)
        
        logger.info(f"📤 Check result: success={success}")
        
        if success:
            amount_rub = convert_usd_to_rub(amount_usd)
            new_balance = balance - amount_rub
            
            logger.info(f"💳 Updating wallet: new_balance={new_balance}")
            
            if update_wallet(user_id, new_balance):
                add_history(user_id, 'withdraw', amount_rub, f'Вывод ${amount_usd}')
                save_withdrawal(user_id, amount_usd, 'success', check_id)
                
                logger.info(f"✅ Withdrawal successful!")
                
                keyboard = [[InlineKeyboardButton(text="💳 Забрать в CryptoBot", url=check_url_or_error)]]
                try:
                    await query.message.edit_text(
                        f"✅ <b>ЧЕК СОЗДАН!</b>\n\n"
                        f"Сумма: ${amount_usd}\n"
                        f"Новый баланс: {new_balance:.0f}р\n\n"
                        f"Нажмите кнопку и заберите свои деньги в @CryptoBot! 🚀",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
                    )
                except TelegramBadRequest:
                    pass
            else:
                logger.error(f"❌ Wallet update failed")
                try:
                    await query.message.edit_text("❌ Ошибка баланса!", reply_markup=get_main_menu())
                except TelegramBadRequest:
                    pass
        else:
            logger.error(f"❌ Check creation failed: {check_url_or_error}")
            try:
                await query.message.edit_text(f"❌ Ошибка вывода!\n\n{check_url_or_error}", reply_markup=get_main_menu())
            except TelegramBadRequest:
                pass
        
        await state.clear()
    except Exception as e:
        logger.error(f"❌ Error in withdraw_max: {e}")
        import traceback
        logger.error(traceback.format_exc())
        try:
            await query.message.edit_text(f"❌ Ошибка! {str(e)}", reply_markup=get_main_menu())
        except TelegramBadRequest:
            pass
        await state.clear()

@router.message(StateFilter(WithdrawStates.amount), F.text)
async def withdraw_amount(message: Message, state: FSMContext):
    logger.info(f"🔄 Withdraw request from user {message.from_user.id}: {message.text}")
    try:
        amount_usd = float(message.text)
        logger.info(f"✅ Amount parsed: ${amount_usd}")
        
        user_id = message.from_user.id
        rate = get_usdt_rub_rate()
        balance = get_wallet(user_id)
        balance_usd = balance / rate if rate else 0
        
        logger.info(f"📊 Balance check: balance={balance}р, balance_usd=${balance_usd:.2f}, rate={rate}")
        
        if amount_usd < 1:
            logger.warning(f"❌ Amount too small: ${amount_usd}")
            await message.answer("❌ Минимум $1!", reply_markup=get_main_menu())
            await state.clear()
            return
        
        if amount_usd > balance_usd:
            logger.warning(f"❌ Insufficient balance: need ${amount_usd}, have ${balance_usd:.2f}")
            await message.answer(f"❌ Недостаточно!\n\nБаланс: ${balance_usd:.2f}", reply_markup=get_main_menu())
            await state.clear()
            return
        
        logger.info(f"🔄 Creating check for user {user_id}, amount ${amount_usd}")
        success, check_url_or_error, check_id = create_check(amount_usd, user_id)
        
        logger.info(f"📤 Check creation result: success={success}, check_id={check_id}")
        
        if success:
            amount_rub = convert_usd_to_rub(amount_usd)
            new_balance = balance - amount_rub
            
            logger.info(f"💳 Updating wallet: {user_id}, old_balance={balance}, new_balance={new_balance}")
            
            if update_wallet(user_id, new_balance):
                add_history(user_id, 'withdraw', amount_rub, f'Вывод ${amount_usd}')
                save_withdrawal(user_id, amount_usd, 'success', check_id)
                
                logger.info(f"✅ Withdrawal successful for user {user_id}")
                
                keyboard = [[InlineKeyboardButton(text="💳 Забрать в CryptoBot", url=check_url_or_error)]]
                await message.answer(
                    f"✅ <b>ЧЕК СОЗДАН!</b>\n\n"
                    f"Сумма: ${amount_usd}\n"
                    f"Новый баланс: {new_balance:.0f}р\n\n"
                    f"Нажмите кнопку и заберите свои деньги в @CryptoBot! 🚀",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
                )
            else:
                logger.error(f"❌ Failed to update wallet for user {user_id}")
                await message.answer("❌ Ошибка баланса!", reply_markup=get_main_menu())
        else:
            logger.error(f"❌ Check creation failed: {check_url_or_error}")
            await message.answer(f"❌ Ошибка вывода!\n\n{check_url_or_error}", reply_markup=get_main_menu())
        
        await state.clear()
    except ValueError as e:
        logger.error(f"❌ ValueError in withdraw: {e}")
        await message.answer("❌ Введите число (например: 10)!", reply_markup=get_main_menu())
        await state.clear()
    except Exception as e:
        logger.error(f"❌ Unexpected error in withdraw: {e}")
        import traceback
        logger.error(traceback.format_exc())
        await message.answer(f"❌ Ошибка! {str(e)}", reply_markup=get_main_menu())
        await state.clear()

@router.callback_query(F.data.startswith("check_"))
async def check_payment(query: CallbackQuery):
    await query.answer()
    invoice_id = int(query.data.replace("check_", ""))
    
    invoice_data = get_invoice(invoice_id)
    if not invoice_data:
        try:
            await query.message.edit_text("❌ Не найден!", reply_markup=get_main_menu())
        except TelegramBadRequest:
            pass
        return
    
    user_id, amount_usd, status = invoice_data
    
    if status == 'paid':
        try:
            await query.message.edit_text(
                f"✅ <b>ОПЛАЧЕНО!</b>\n\n"
                f"${amount_usd} успешно пополнено!\n\n"
                f"Баланс обновлен",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="💰 Мои пополнения", callback_data="my_deposits")],
                    [InlineKeyboardButton(text="🏠 Меню", callback_data="return_main")]
                ])
            )
        except TelegramBadRequest:
            pass
        return
    
    is_paid = check_invoice_paid(invoice_id)
    
    if is_paid:
        amount_rub = convert_usd_to_rub(amount_usd)
        new_balance = get_wallet(user_id) + amount_rub
        update_wallet(user_id, new_balance)
        add_history(user_id, 'deposit', amount_rub, f'Платеж ${amount_usd}')
        update_invoice_status(invoice_id, 'paid')
        try:
            await query.message.edit_text(
                f"✅ <b>ОПЛАЧЕНО!</b>\n\n"
                f"${amount_usd} успешно пополнено!\n"
                f"Баланс: {new_balance:.0f}р",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="💰 Мои пополнения", callback_data="my_deposits")],
                    [InlineKeyboardButton(text="🏠 Меню", callback_data="return_main")]
                ])
            )
        except TelegramBadRequest:
            pass
    else:
        try:
            await query.message.edit_text(
                f"⏳ <b>Ожидание платежа...</b>\n\n"
                f"${amount_usd}\n\n"
                f"Платеж еще не поступил.\n"
                f"Попробуйте позже.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔄 Проверить снова", callback_data=f"check_{invoice_id}")],
                    [InlineKeyboardButton(text="💰 Мои пополнения", callback_data="my_deposits")],
                    [InlineKeyboardButton(text="🏠 Меню", callback_data="return_main")]
                ])
            )
        except TelegramBadRequest:
            pass

@router.callback_query(F.data == "profile")
async def profile_handler(query: CallbackQuery):
    await query.answer()
    user_id = query.from_user.id
    username = query.from_user.username or f"user{user_id}"
    
    # Гарантируем что профиль существует
    get_or_create_profile(user_id, username)
    
    profile = get_profile(user_id)
    
    if profile:
        user_id_db, username_db, nickname, rating, completed_deals = profile
        text = f"👤 <b>Профиль</b>\n\n" \
                f"@{username_db or username}\n" \
                f"Ник: {nickname or 'Не установлен'}\n" \
                f"⭐ {rating}/3.0\n" \
                f"✅ {completed_deals}"
        keyboard = [[InlineKeyboardButton(text="🏠 Меню", callback_data="return_main")]]
    else:
        text = f"👤 <b>Профиль</b>\n\n" \
                f"@{username}\n" \
                f"Ник: Не установлен\n" \
                f"⭐ 2.0/3.0\n" \
                f"✅ 0"
        keyboard = [[InlineKeyboardButton(text="🏠 Меню", callback_data="return_main")]]
    
    # Добавляем кнопку модератора если это модератор
    if user_id == MODERATOR_ID:
        text += "\n\n🔒 <b>МОДЕРАТОР</b>"
        keyboard.insert(0, [InlineKeyboardButton(text="🔐 Модератор Панель", callback_data="mod_panel")])
    
    try:
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    except TelegramBadRequest:
        pass

@router.callback_query(F.data == "history")
async def history_handler(query: CallbackQuery):
    await query.answer()
    user_id = query.from_user.id
    transactions = get_history(user_id, 10)
    if not transactions:
        text = "📜 Пусто"
    else:
        text = "📜 История:\n\n"
        for trans in transactions:
            text += f"{trans}\n"
    try:
        await query.message.edit_text(text, reply_markup=get_main_menu())
    except TelegramBadRequest:
        pass

@router.callback_query(F.data == "return_main")
async def return_main(query: CallbackQuery):
    await query.answer()
    try:
        await query.message.edit_text("🎯 Меню", reply_markup=get_main_menu())
    except TelegramBadRequest:
        pass

@router.callback_query(F.data == "mod_panel")
async def mod_panel_handler(query: CallbackQuery):
    if query.from_user.id != MODERATOR_ID:
        await query.answer("❌ Только модератор!", show_alert=True)
        return
    
    try:
        await query.message.edit_text(
            "🔒 <b>МОДЕРАТОР</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🚫 Забанить", callback_data="ban_player")],
                [InlineKeyboardButton(text="🏠 Меню", callback_data="return_main")]
            ])
        )
    except TelegramBadRequest:
        pass

@router.callback_query(F.data == "ban_player")
async def ban_player_start(query: CallbackQuery, state: FSMContext):
    if query.from_user.id != MODERATOR_ID:
        await query.answer("❌ Только модератор!", show_alert=True)
        return
    
    await query.answer()
    await query.message.answer("🚫 Ник:")
    await state.set_state(BanStates.nickname)

@router.message(StateFilter(BanStates.nickname))
async def ban_nickname_handler(message: Message, state: FSMContext):
    nickname = message.text.strip()
    await state.update_data(nickname=nickname)
    await message.answer("Причина:")
    await state.set_state(BanStates.reason)

@router.message(StateFilter(BanStates.reason))
async def ban_reason_handler(message: Message, state: FSMContext):
    data = await state.get_data()
    nickname = data.get('nickname')
    reason = message.text.strip()
    
    if ban_player(nickname, reason, MODERATOR_ID):
        await message.answer(f"✅ Забанен: {nickname}", reply_markup=get_main_menu())
    else:
        await message.answer("❌ Ошибка!", reply_markup=get_main_menu())
    
    await state.clear()

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
        logger.info(f"🌍 SERVER IP: {server_ip}")
    
    dp.startup.register(on_startup)
    app = web.Application()
    webhook_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_handler.register(app, path=WEBHOOK_URL)
    setup_application(app, dp, bot=bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=WEB_SERVER_HOST, port=WEB_SERVER_PORT)
    await site.start()
    logger.info("✅ System ready")
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
