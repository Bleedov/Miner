import os
import asyncio
import logging
from datetime import datetime, timedelta
import random
import sqlite3
from contextlib import contextmanager
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery, FSInputFile
from aiocryptopay import AioCryptoPay, Network
import uuid
import time

# ===================== ЗАГРУЗКА ПЕРЕМЕННЫХ ИЗ .env =====================
load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID', '0'))  # 0 если не указан
CRYPTO_BOT_TOKEN = os.getenv('CRYPTO_BOT_TOKEN')

# Проверка наличия токенов
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в .env файле!")
if not CRYPTO_BOT_TOKEN:
    print("⚠️ CRYPTO_BOT_TOKEN не найден в .env файле. CryptoBot функции работать не будут!")

# ===================== ПСИХОЛОГИЧЕСКИЕ ЦЕНЫ =====================
GOBLINS = {
    1: {"name": "🧑‍🦱 Гном-стажер", "price_silver": 50, "amethyst_per_hour": 2, "emoji": "⛏️"},
    2: {"name": "👨‍🦰 Гном-шахтер", "price_silver": 200, "amethyst_per_hour": 10, "emoji": "⚒️"},
    3: {"name": "👨‍🦳 Гном-прораб", "price_silver": 800, "amethyst_per_hour": 50, "emoji": "🔨"},
    4: {"name": "🧙‍♂️ Гном-шаман", "price_silver": 3000, "amethyst_per_hour": 200, "emoji": "💎"},
    5: {"name": "🧝‍♂️ Гном-король", "price_silver": 10000, "amethyst_per_hour": 800, "emoji": "👑"}
}

# ===================== ИНИЦИАЛИЗАЦИЯ =====================
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Инициализация Aiocryptopay (если есть токен)
crypto = None
if CRYPTO_BOT_TOKEN:
    crypto = AioCryptoPay(token=CRYPTO_BOT_TOKEN, network=Network.MAIN_NET)

# ===================== БАЗА ДАННЫХ =====================
class Database:
    def __init__(self):
        self.conn = sqlite3.connect('amethyst_mines.db', check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.create_tables()
        self.init_settings()
        self.load_goblins()
    
    def create_tables(self):
        # Пользователи
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                silver_balance INTEGER DEFAULT 0,
                gold_balance INTEGER DEFAULT 0,
                points_balance INTEGER DEFAULT 0,
                amethyst_balance INTEGER DEFAULT 0,
                total_donated INTEGER DEFAULT 0,
                total_withdrawn INTEGER DEFAULT 0,
                is_vip BOOLEAN DEFAULT 0,
                referrer_id INTEGER,
                referral_points INTEGER DEFAULT 0,
                last_collect TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Гоблины
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS miners (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                goblin_type INTEGER,
                amethyst_per_hour INTEGER,
                purchased_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        
        # Настройки
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT
            )
        ''')
        
        # Настройки гоблинов
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS goblin_settings (
                goblin_type INTEGER PRIMARY KEY,
                price_silver INTEGER,
                amethyst_per_hour INTEGER,
                updated_at TEXT
            )
        ''')
        
        # Транзакции CryptoBot
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS crypto_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                invoice_id TEXT UNIQUE,
                amount_crypto REAL,
                currency TEXT,
                amount_rub INTEGER,
                amount_points INTEGER,
                status TEXT DEFAULT 'pending',
                created_at TEXT,
                confirmed_at TEXT
            )
        ''')
        
        # Транзакции Stars
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS stars_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                charge_id TEXT UNIQUE,
                amount_stars INTEGER,
                amount_rub INTEGER,
                amount_points INTEGER,
                status TEXT DEFAULT 'pending',
                created_at TEXT,
                confirmed_at TEXT
            )
        ''')
        
        # Заявки на вывод
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount_gold INTEGER,
                amount_rub INTEGER,
                wallet TEXT,
                spent_points INTEGER,
                status TEXT DEFAULT 'pending',
                created_at TEXT,
                completed_at TEXT
            )
        ''')
        
        # Забаненные пользователи
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS banned_users (
                user_id INTEGER PRIMARY KEY,
                reason TEXT,
                banned_at TEXT,
                banned_by INTEGER
            )
        ''')
        
        # Логи админов
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS admin_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER,
                action TEXT,
                target_user INTEGER,
                details TEXT,
                created_at TEXT
            )
        ''')
        
        # Промокоды
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS promocodes (
                code TEXT PRIMARY KEY,
                reward_type TEXT,
                reward_amount INTEGER,
                max_uses INTEGER,
                used_count INTEGER DEFAULT 0,
                expires_at TEXT,
                created_by INTEGER,
                created_at TEXT
            )
        ''')
        
        # Использованные промокоды
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS used_promocodes (
                user_id INTEGER,
                code TEXT,
                used_at TEXT,
                PRIMARY KEY (user_id, code)
            )
        ''')
        
        self.conn.commit()
    
    def init_settings(self):
        currency_settings = [
            ('ton_rate', '5.2'), ('usdt_rate', '92'), ('stars_rate', '1.3'), ('usd_to_rub', '92'),
        ]
        economy_settings = [
            ('min_donate_rub', '100'), ('point_percent', '35'), ('withdrawal_fee', '15'),
            ('min_withdrawal_points', '35'), ('vip_donate', '5000'), ('exchange_rate', '1000'),
            ('gold_to_rub', '100'), ('amethyst_price', '10'), ('gold_percent', '50'), ('silver_percent', '50'),
        ]
        for key, value in currency_settings + economy_settings:
            self.cursor.execute('INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)',
                               (key, value, datetime.now().isoformat()))
        self.conn.commit()
    
    def load_goblins(self):
        for gt, goblin in GOBLINS.items():
            self.cursor.execute('INSERT OR IGNORE INTO goblin_settings (goblin_type, price_silver, amethyst_per_hour, updated_at) VALUES (?, ?, ?, ?)',
                               (gt, goblin["price_silver"], goblin["amethyst_per_hour"], datetime.now().isoformat()))
        self.conn.commit()
    
    def get_setting(self, key):
        self.cursor.execute('SELECT value FROM settings WHERE key = ?', (key,))
        result = self.cursor.fetchone()
        return float(result[0]) if result else 0
    
    def update_setting(self, key, value):
        self.cursor.execute('UPDATE settings SET value = ?, updated_at = ? WHERE key = ?',
                           (str(value), datetime.now().isoformat(), key))
        self.conn.commit()
    
    def get_goblin_settings(self, goblin_type):
        self.cursor.execute('SELECT price_silver, amethyst_per_hour FROM goblin_settings WHERE goblin_type = ?', (goblin_type,))
        result = self.cursor.fetchone()
        if result:
            return {"price_silver": result[0], "amethyst_per_hour": result[1]}
        default = GOBLINS.get(goblin_type, {})
        return {"price_silver": default.get("price_silver", 0), "amethyst_per_hour": default.get("amethyst_per_hour", 0)}
    
    def update_goblin_setting(self, goblin_type, price_silver, amethyst_per_hour):
        self.cursor.execute('UPDATE goblin_settings SET price_silver = ?, amethyst_per_hour = ?, updated_at = ? WHERE goblin_type = ?',
                           (price_silver, amethyst_per_hour, datetime.now().isoformat(), goblin_type))
        self.conn.commit()
    
    @contextmanager
    def get_cursor(self):
        cursor = self.conn.cursor()
        try:
            yield cursor
            self.conn.commit()
        finally:
            cursor.close()

db = Database()

# ===================== ЗАГРУЗКА НАСТРОЕК =====================
MIN_DONATE_RUB = int(db.get_setting('min_donate_rub'))
POINT_PERCENT = int(db.get_setting('point_percent'))
WITHDRAWAL_FEE = int(db.get_setting('withdrawal_fee'))
MIN_WITHDRAWAL_POINTS = int(db.get_setting('min_withdrawal_points'))
VIP_DONATE = int(db.get_setting('vip_donate'))
EXCHANGE_RATE = int(db.get_setting('exchange_rate'))
GOLD_TO_RUB = int(db.get_setting('gold_to_rub'))
AMETHYST_PRICE = int(db.get_setting('amethyst_price'))
GOLD_PERCENT = int(db.get_setting('gold_percent'))
SILVER_PERCENT = int(db.get_setting('silver_percent'))

# ===================== СОСТОЯНИЯ =====================
class DonateStates(StatesGroup):
    waiting_crypto_amount = State()
    currency = State()

class WithdrawStates(StatesGroup):
    waiting_amount = State()
    waiting_wallet = State()

class ExchangeStates(StatesGroup):
    waiting_amount = State()

class AdminSettingsStates(StatesGroup):
    waiting_setting_key = State()
    waiting_setting_value = State()

class GoblinStates(StatesGroup):
    waiting_goblin_type = State()
    waiting_price = State()
    waiting_profit = State()

class AdminStates(StatesGroup):
    waiting_user_id_for_balance = State()
    waiting_balance_amount = State()
    waiting_balance_action = State()
    waiting_user_id_for_ban = State()
    waiting_ban_reason = State()
    waiting_mailing_text = State()
    waiting_promo_code = State()
    waiting_promo_reward_type = State()
    waiting_promo_reward_amount = State()
    waiting_promo_max_uses = State()
    waiting_promo_expires = State()

# ===================== ФУНКЦИИ =====================
def get_user(user_id):
    with db.get_cursor() as cursor:
        cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        user = cursor.fetchone()
        if not user:
            cursor.execute('INSERT INTO users (user_id, last_collect, is_vip) VALUES (?, ?, ?)',
                          (user_id, datetime.now().isoformat(), 0))
            cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
            user = cursor.fetchone()
        return user

def is_banned(user_id):
    with db.get_cursor() as cursor:
        cursor.execute('SELECT * FROM banned_users WHERE user_id = ?', (user_id,))
        return cursor.fetchone() is not None

def collect_amethyst(user_id):
    with db.get_cursor() as cursor:
        cursor.execute('SELECT last_collect FROM users WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        if not result:
            return 0
        last_collect = datetime.fromisoformat(result[0])
        hours_passed = (datetime.now() - last_collect).total_seconds() / 3600
        cursor.execute('SELECT goblin_type FROM miners WHERE user_id = ?', (user_id,))
        miners = cursor.fetchall()
        if not miners:
            return 0
        total_per_hour = 0
        for miner in miners:
            settings = db.get_goblin_settings(miner[0])
            total_per_hour += settings["amethyst_per_hour"]
        amethyst_earned = int(total_per_hour * hours_passed)
        if amethyst_earned > 0:
            cursor.execute('UPDATE users SET amethyst_balance = amethyst_balance + ?, last_collect = ? WHERE user_id = ?',
                          (amethyst_earned, datetime.now().isoformat(), user_id))
        return amethyst_earned

def sell_amethyst(user_id, amount=None):
    with db.get_cursor() as cursor:
        cursor.execute('SELECT amethyst_balance FROM users WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        if not result or result[0] == 0:
            return 0, 0
        amethyst_balance = result[0]
        if amount is None or amount > amethyst_balance:
            amount = amethyst_balance
        total_coins = amount * AMETHYST_PRICE
        silver_earned = int(total_coins * SILVER_PERCENT / 100)
        gold_earned = total_coins - silver_earned
        cursor.execute('UPDATE users SET amethyst_balance = amethyst_balance - ?, silver_balance = silver_balance + ?, gold_balance = gold_balance + ? WHERE user_id = ?',
                      (amount, silver_earned, gold_earned, user_id))
        return silver_earned, gold_earned

def main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 ПРОФИЛЬ", callback_data="menu_profile"),
         InlineKeyboardButton(text="⛏ МОИ ГНОМЫ", callback_data="menu_goblins")],
        [InlineKeyboardButton(text="🏪 МАГАЗИН", callback_data="menu_mine"),
         InlineKeyboardButton(text="💎 ПРОДАТЬ", callback_data="menu_sell")],
        [InlineKeyboardButton(text="💰 ПОПОЛНИТЬ", callback_data="menu_donate"),
         InlineKeyboardButton(text="💸 ВЫВЕСТИ", callback_data="menu_withdraw")],
        [InlineKeyboardButton(text="📊 БИРЖА (VIP)", callback_data="menu_exchange")]
    ])

# ===================== ОСНОВНЫЕ КОМАНДЫ ПОЛЬЗОВАТЕЛЯ =====================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    
    if is_banned(user_id):
        await message.answer("🚫 Вы забанены в этом боте.")
        return
    
    get_user(user_id)
    
    text = (
        "✨ **AMETHYST MINES** ✨\n\n"
        "⚙️ **КАК РАБОТАЕТ ВЫВОД:**\n"
        f"1️⃣ 💎 Поинты - это ПРОПУСК на вывод (НЕ ВЫВОДЯТСЯ!)\n"
        f"2️⃣ ❌ Поинты нельзя вывести, только доступ\n"
        f"3️⃣ 💰 Выводится только ЗОЛОТО\n"
        f"4️⃣ 💎 1 поинт = доступ к 10 золоту\n"
        f"5️⃣ 💰 Курс: {GOLD_TO_RUB} золота = 1 рубль\n"
        f"6️⃣ 🎫 Мин. донат {MIN_DONATE_RUB} руб = {int(MIN_DONATE_RUB * POINT_PERCENT / 100)} поинтов\n"
        f"7️⃣ 👑 VIP после {VIP_DONATE}₽: биржа {EXCHANGE_RATE} серебра = 1 поинт\n\n"
        
        "💰 **ПОПОЛНЕНИЕ:**\n"
        "• CryptoBot (TON/USDT)\n"
        "• Telegram Stars\n\n"
        
        "📱 **ИСПОЛЬЗУЙ КНОПКИ** 👇"
    )
    
    await message.answer(text, reply_markup=main_keyboard(), parse_mode="Markdown")

@dp.callback_query(lambda c: c.data == "menu_profile")
async def menu_profile(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    collect_amethyst(user_id)
    
    with db.get_cursor() as cursor:
        cursor.execute('SELECT silver_balance, gold_balance, points_balance, amethyst_balance, total_donated, total_withdrawn, is_vip FROM users WHERE user_id = ?', (user_id,))
        user = cursor.fetchone()
        cursor.execute('SELECT COUNT(*) FROM miners WHERE user_id = ?', (user_id,))
        miners = cursor.fetchone()[0]
    
    total_wealth = user[0] + user[1] + user[3] * AMETHYST_PRICE
    vip_status = "👑 VIP" if user[6] else "Обычный"
    
    text = (
        f"👤 **ТВОЙ ПРОФИЛЬ**\n\n"
        f"💰 **ОБЩИЙ КАПИТАЛ:** {total_wealth:,} монет\n"
        f"⛏ **ГНОМОВ:** {miners}\n\n"
        f"📦 **РЕСУРСЫ:**\n"
        f"🥈 Серебро: {user[0]:,}\n"
        f"🥇 Золото: {user[1]:,}\n"
        f"💎 Аметист: {user[3]:,}\n"
        f"💎 Поинты: {user[2]:,} (ПРОПУСК)\n\n"
        f"📊 Донатов: {user[4]} руб\n"
        f"💸 Выведено: {user[5]} руб\n"
        f"👑 Статус: {vip_status}\n\n"
        f"💎 1 поинт = доступ к 10 золоту\n"
        f"🔐 Мин. поинтов: {MIN_WITHDRAWAL_POINTS}"
    )
    
    await callback.message.edit_text(text, reply_markup=main_keyboard(), parse_mode="Markdown")

@dp.callback_query(lambda c: c.data == "menu_mine")
async def menu_mine(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    
    with db.get_cursor() as cursor:
        cursor.execute('SELECT silver_balance FROM users WHERE user_id = ?', (user_id,))
        silver = cursor.fetchone()[0]
    
    text = f"🥈 **Твоё серебро:** {silver:,}\n\n🏪 **МАГАЗИН ГНОМОВ:**\n\n"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    
    for goblin_id, goblin in GOBLINS.items():
        settings = db.get_goblin_settings(goblin_id)
        can_buy = "✅" if silver >= settings["price_silver"] else "❌"
        text += f"{goblin['emoji']} **{goblin['name']}**\n"
        text += f"   💎 Добыча: {settings['amethyst_per_hour']} аметистов/час\n"
        text += f"   💵 Цена: {settings['price_silver']:,} серебра {can_buy}\n\n"
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text=f"Купить {goblin['name']}", callback_data=f"buy_goblin_{goblin_id}")
        ])
    
    keyboard.inline_keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")])
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")

@dp.callback_query(lambda c: c.data.startswith('buy_goblin_'))
async def process_buy_goblin(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    goblin_id = int(callback.data.split('_')[2])
    goblin = GOBLINS[goblin_id]
    settings = db.get_goblin_settings(goblin_id)
    
    with db.get_cursor() as cursor:
        cursor.execute('SELECT silver_balance FROM users WHERE user_id = ?', (user_id,))
        silver = cursor.fetchone()[0]
        
        if silver < settings["price_silver"]:
            await callback.answer("❌ Недостаточно серебра!", show_alert=True)
            return
        
        cursor.execute('INSERT INTO miners (user_id, goblin_type, amethyst_per_hour, purchased_at) VALUES (?, ?, ?, ?)',
                      (user_id, goblin_id, settings["amethyst_per_hour"], datetime.now().isoformat()))
        cursor.execute('UPDATE users SET silver_balance = silver_balance - ? WHERE user_id = ?',
                      (settings["price_silver"], user_id))
    
    await callback.answer(f"✅ Куплен {goblin['name']}!", show_alert=True)
    await menu_mine(callback)

@dp.callback_query(lambda c: c.data == "menu_goblins")
async def menu_goblins(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    amethyst_earned = collect_amethyst(user_id)
    
    with db.get_cursor() as cursor:
        cursor.execute('SELECT goblin_type FROM miners WHERE user_id = ?', (user_id,))
        miners = cursor.fetchall()
        cursor.execute('SELECT amethyst_balance FROM users WHERE user_id = ?', (user_id,))
        amethyst = cursor.fetchone()[0]
    
    if not miners:
        await callback.message.edit_text("У тебя нет гномов! Купи в магазине.", reply_markup=main_keyboard())
        return
    
    goblin_counts = {}
    total_profit = 0
    for miner in miners:
        gt = miner[0]
        settings = db.get_goblin_settings(gt)
        goblin_counts[gt] = gobl
