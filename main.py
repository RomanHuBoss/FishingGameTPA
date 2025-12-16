import yaml
import logging
import random
import time
import asyncio
import hashlib  # Для генерации ID результата inline
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from aiogram import Bot, Dispatcher, types
from aiogram.types import WebAppInfo, InlineQueryResultPhoto, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, BigInteger, Integer, String, Float, Boolean, DateTime, desc, select, func
from pydantic import BaseModel

# --- КОНФИГУРАЦИЯ ---
def load_config():
    try:
        with open("config.yaml", "r") as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        return {}

config = load_config()

BOT_TOKEN = config.get('bot', {}).get('token', "")
WEBAPP_URL = config.get('bot', {}).get('webapp_url', "")
DATABASE_URL = config.get('database', {}).get('url', "sqlite+aiosqlite:///./fishing.db")
ADSGRAM_ID = config.get('adsgram', {}).get('block_id', "")

Base = declarative_base()

# --- МОДЕЛИ ДАННЫХ ---
class User(Base):
    __tablename__ = "users"
    telegram_id = Column(BigInteger, primary_key=True, index=True)
    username = Column(String, nullable=True)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    balance = Column(Integer, default=0)
    energy = Column(Float, default=100.0)
    
    # Уровни снастей (Equipment)
    rod_level = Column(Integer, default=1) 
    boat_level = Column(Integer, default=0)
    
    # Инвентарь (Consumables)
    bait_common = Column(Integer, default=0) # Обычная наживка
    bait_rare = Column(Integer, default=0)   # Редкая наживка
    
    last_active_at = Column(Integer, default=lambda: int(time.time()))
    # Анти-чит: время последнего клика
    last_click_at = Column(Float, default=0.0) 

class Catch(Base):
    __tablename__ = "catches"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, index=True) 
    fish_id = Column(String)                  
    weight = Column(Float, default=0.0)       
    is_trash = Column(Boolean, default=False)
    reward = Column(Integer, default=0)
    caught_at = Column(DateTime, default=datetime.utcnow)

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# --- БАЛАНС И КОНСТАНТЫ ---

# Цены на удочки (Сглаженная прогрессия)
ROD_PRICES = {
    1: 0, 2: 300, 3: 1000, 4: 3500, 5: 12000, 
    6: 40000, 7: 120000, 8: 400000, 9: 1000000, 10: 3000000
}

# Цены на лодки
BOAT_PRICES = {1: 1500, 2: 8000, 3: 35000, 4: 150000, 5: 800000}

# БАЛАНС ЛОДОК (HARD NERF)
# Доход в секунду (сильно уменьшен, чтобы не убивать активную игру)
BOAT_INCOME = {
    0: 0, 
    1: 0.1,   # ~360 монет/час
    2: 0.5,   # ~1800 монет/час
    3: 2.5,   # ~9000 монет/час
    4: 10.0,  # ~36k монет/час
    5: 40.0   # ~144k монет/час
}

# ВМЕСТИМОСТЬ ТРЮМА (В часах)
# Лодка перестает приносить доход, если игрок не заходил дольше этого времени
BOAT_MAX_HOURS = {
    0: 0, 
    1: 2,   # Нужно заходить каждые 2 часа
    2: 4,   
    3: 8,   # Ночной режим
    4: 12,  
    5: 24   # Сутки
}

# Расходники
CONSUMABLES = {
    "energy_drink": {"price": 400, "energy": 50},  
    "bait_common": {"price": 100, "amount": 10},   # 10 монет/шт
    "bait_rare": {"price": 800, "amount": 5}       # 160 монет/шт
}

ENERGY_REGEN_PER_SEC = 0.6  # Полное восстановление ~2.7 минуты
MAX_ENERGY = 100
CLICK_COOLDOWN = 0.5        # Задержка между кликами (анти-кликер)

# Таблица рыб
FISH_TABLE = [
    # Мусор (trash) - теперь дает небольшую награду
    {"id": "weed", "emoji": "🌿", "mult": 0.0, "weight": 20, "color": "#64748b", "is_trash": True, "min_w": 0, "max_w": 0, "rarity": 0},
    {"id": "boot", "emoji": "👢", "mult": 0.0, "weight": 10, "color": "#64748b", "is_trash": True, "min_w": 0, "max_w": 0, "rarity": 0},
    {"id": "tin", "emoji": "🥫", "mult": 0.0, "weight": 10, "color": "#64748b", "is_trash": True, "min_w": 0, "max_w": 0, "rarity": 0},
    {"id": "bone", "emoji": "☠️", "mult": 0.0, "weight": 8, "color": "#64748b", "is_trash": True, "min_w": 0, "max_w": 0, "rarity": 0},
    {"id": "bag", "emoji": "🛍️", "mult": 0.0, "weight": 8, "color": "#64748b", "is_trash": True, "min_w": 0, "max_w": 0, "rarity": 0},
    {"id": "tire", "emoji": "🍩", "mult": 0.0, "weight": 5, "color": "#64748b", "is_trash": True, "min_w": 0, "max_w": 0, "rarity": 0},
    # Обычные (rarity 1)
    {"id": "minnow", "emoji": "🐟", "mult": 1.0, "weight": 45, "color": "#fff", "is_trash": False, "min_w": 0.05, "max_w": 0.15, "rarity": 1},
    {"id": "shrimp", "emoji": "🦐", "mult": 1.2, "weight": 40, "color": "#e2e8f0", "is_trash": False, "min_w": 0.01, "max_w": 0.05, "rarity": 1},
    {"id": "sardine", "emoji": "🐟", "mult": 1.5, "weight": 30, "color": "#cbd5e1", "is_trash": False, "min_w": 0.1, "max_w": 0.3, "rarity": 1},
    {"id": "carp", "emoji": "🎏", "mult": 1.8, "weight": 25, "color": "#fbbf24", "is_trash": False, "min_w": 0.5, "max_w": 2.5, "rarity": 1},
    {"id": "perch", "emoji": "🐠", "mult": 2.0, "weight": 25, "color": "#a5f3fc", "is_trash": False, "min_w": 0.3, "max_w": 1.2, "rarity": 1},
    {"id": "trout", "emoji": "🐟", "mult": 2.5, "weight": 20, "color": "#86efac", "is_trash": False, "min_w": 1.0, "max_w": 4.0, "rarity": 1},
    # Редкие (rarity 2)
    {"id": "clown", "emoji": "🤡", "mult": 3.0, "weight": 18, "color": "#f97316", "is_trash": False, "min_w": 0.1, "max_w": 0.3, "rarity": 2},
    {"id": "crab", "emoji": "🦀", "mult": 3.5, "weight": 15, "color": "#f87171", "is_trash": False, "min_w": 1.0, "max_w": 5.0, "rarity": 2},
    {"id": "jelly", "emoji": "🪼", "mult": 4.0, "weight": 12, "color": "#c084fc", "is_trash": False, "min_w": 0.5, "max_w": 2.0, "rarity": 2},
    {"id": "squid", "emoji": "🦑", "mult": 5.0, "weight": 10, "color": "#f472b6", "is_trash": False, "min_w": 0.5, "max_w": 3.0, "rarity": 2},
    {"id": "seahorse", "emoji": "🐉", "mult": 6.0, "weight": 10, "color": "#fde047", "is_trash": False, "min_w": 0.01, "max_w": 0.05, "rarity": 2},
    {"id": "pike", "emoji": "🐊", "mult": 7.0, "weight": 8, "color": "#4ade80", "is_trash": False, "min_w": 2.0, "max_w": 12.0, "rarity": 2},
    {"id": "eel", "emoji": "🐍", "mult": 8.0, "weight": 7, "color": "#facc15", "is_trash": False, "min_w": 1.0, "max_w": 5.0, "rarity": 2},
    # Эпические (rarity 3)
    {"id": "tuna", "emoji": "🐟", "mult": 12.0, "weight": 6, "color": "#60a5fa", "is_trash": False, "min_w": 20.0, "max_w": 250.0, "rarity": 3},
    {"id": "sword", "emoji": "🗡️", "mult": 15.0, "weight": 5, "color": "#93c5fd", "is_trash": False, "min_w": 30.0, "max_w": 300.0, "rarity": 3},
    {"id": "ray", "emoji": "👿", "mult": 20.0, "weight": 4, "color": "#818cf8", "is_trash": False, "min_w": 5.0, "max_w": 50.0, "rarity": 3},
    {"id": "catfish", "emoji": "🐡", "mult": 25.0, "weight": 4, "color": "#d946ef", "is_trash": False, "min_w": 10.0, "max_w": 100.0, "rarity": 3},
    {"id": "angler", "emoji": "👾", "mult": 35.0, "weight": 3, "color": "#a855f7", "is_trash": False, "min_w": 2.0, "max_w": 10.0, "rarity": 3},
    {"id": "turtle", "emoji": "🐢", "mult": 40.0, "weight": 3, "color": "#22c55e", "is_trash": False, "min_w": 30.0, "max_w": 150.0, "rarity": 3},
    # Легендарные (rarity 4)
    {"id": "shark", "emoji": "🦈", "mult": 60.0, "weight": 2.5, "color": "#eab308", "is_trash": False, "min_w": 300.0, "max_w": 1500.0, "rarity": 4},
    {"id": "whale", "emoji": "🐳", "mult": 120.0, "weight": 1.5, "color": "#3b82f6", "is_trash": False, "min_w": 2000.0, "max_w": 10000.0, "rarity": 4},
    {"id": "chest", "emoji": "👑", "mult": 250.0, "weight": 0.5, "color": "#facc15", "is_trash": True, "min_w": 0, "max_w": 0, "rarity": 4},
    {"id": "mega", "emoji": "🦖", "mult": 500.0, "weight": 0.2, "color": "#ef4444", "is_trash": False, "min_w": 5000.0, "max_w": 20000.0, "rarity": 4},
    {"id": "kraken", "emoji": "🐙", "mult": 1000.0, "weight": 0.1, "color": "#dc2626", "is_trash": False, "min_w": 10000.0, "max_w": 50000.0, "rarity": 4},
]

class ClickRequest(BaseModel):
    telegram_id: int
class InitRequest(BaseModel):
    telegram_id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
class BuyRequest(BaseModel):
    telegram_id: int
    item_id: str
class AdRewardRequest(BaseModel):
    telegram_id: int

# --- ЛОГИКА ОФФЛАЙН ПРОГРЕССА С ЛИМИТАМИ ---
def calculate_offline_progress(user, current_time, is_active=False):
    time_diff = current_time - user.last_active_at
    if time_diff < 0: time_diff = 0
    
    # 1. Лимит по времени работы лодки (вместимость трюма)
    max_hours = BOAT_MAX_HOURS.get(user.boat_level, 0)
    max_seconds = max_hours * 3600
    effective_time = min(time_diff, max_seconds)
    
    # 2. Начисление денег за эффективное время
    income = BOAT_INCOME.get(user.boat_level, 0)
    earned = int(effective_time * income)
    user.balance += earned
    
    # 3. Восстановление энергии (за ВСЁ время отсутствия, тут лимит лодки не влияет)
    if not is_active or time_diff > 5:
        restored_energy = time_diff * ENERGY_REGEN_PER_SEC
        user.energy = min(MAX_ENERGY, user.energy + restored_energy)
    
    user.last_active_at = current_time
    return earned

logging.basicConfig(level=logging.INFO)
app = FastAPI()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.post("/api/init")
async def init_user(data: InitRequest):
    current_time = int(time.time())
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == data.telegram_id))
        user = result.scalars().first()
        earned = 0
        
        if not user:
            # SOFT LAUNCH: Даем ресурсы новичку
            user = User(
                telegram_id=data.telegram_id, 
                username=data.username,
                first_name=data.first_name, 
                last_name=data.last_name,   
                last_active_at=current_time,
                balance=200,    # Стартовый бонус
                bait_common=5   # 5 бесплатных червей
            )
            session.add(user)
        else:
            if data.username: user.username = data.username
            if data.first_name: user.first_name = data.first_name
            if data.last_name: user.last_name = data.last_name
            earned = calculate_offline_progress(user, current_time)
        
        await session.commit()
        
        return {
            "balance": user.balance, 
            "energy": int(user.energy),
            "rod_level": user.rod_level, 
            "boat_level": user.boat_level,
            "rod_price": ROD_PRICES.get(user.rod_level + 1), 
            "boat_price": BOAT_PRICES.get(user.boat_level + 1),
            "bait_common": user.bait_common,
            "bait_rare": user.bait_rare,
            "offline_earned": earned, 
            "adsgram_id": ADSGRAM_ID
        }

@app.post("/api/fish")
async def fish_action(data: ClickRequest):
    current_time = time.time()
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == data.telegram_id))
        user = result.scalars().first()
        
        # Считаем пассивный доход перед действием
        afk_earned = calculate_offline_progress(user, int(current_time), is_active=True)
        
        # --- ANTI-CLICKER ---
        if current_time - user.last_click_at < CLICK_COOLDOWN:
             return {"status": "cooldown", "balance": user.balance, "energy": int(user.energy), "afk_earned": afk_earned}
        user.last_click_at = current_time

        # Фиксированная цена клика (убрали наказание за усталость)
        energy_cost = 2.0 
        
        if user.energy < energy_cost:
            await session.commit()
            return {"status": "no_energy", "balance": user.balance, "energy": int(user.energy), "afk_earned": afk_earned}

        # --- ЛОГИКА НАЖИВКИ ---
        used_bait = None
        luck_boost = 0.0
        
        if user.bait_rare > 0:
            user.bait_rare -= 1
            used_bait = "rare"
            luck_boost = 0.35 # +35% шанса
        elif user.bait_common > 0:
            user.bait_common -= 1
            used_bait = "common"
            luck_boost = 0.15 # +15% шанса

        # --- БАЛАНС: ШАНСЫ ---
        # База 30% + бонус за уровень удочки (макс 95%)
        catch_chance = 0.30 + (user.rod_level * 0.04) + luck_boost
        catch_chance = min(catch_chance, 0.95)
        
        user.energy = max(0.0, user.energy - energy_cost)
        
        # Промах
        if random.random() > catch_chance:
            await session.commit()
            return {
                "status": "miss", 
                "balance": user.balance, 
                "energy": int(user.energy), 
                "afk_earned": afk_earned,
                "bait_common": user.bait_common,
                "bait_rare": user.bait_rare
            }

        # ВЫБОР РЫБЫ
        weights = [f['weight'] for f in FISH_TABLE]
        
        # Если редкая наживка: убираем мусор, НО оставляем Сундук (Chest)
        if used_bait == "rare":
            weights = [w if (not f['is_trash'] or f['id'] == 'chest') else 0 for f, w in zip(FISH_TABLE, weights)]
        
        try:
            fish = random.choices(FISH_TABLE, weights=weights, k=1)[0]
        except ValueError:
             fish = FISH_TABLE[0]

        weight = 0.0
        if not fish['is_trash']:
            weight = round(random.uniform(fish['min_w'], fish['max_w']), 2)
        
        # --- БАЛАНС: НАГРАДА ---
        # Нелинейный рост силы удочки (x^1.15), чтобы поспевать за ценами
        rod_multiplier = user.rod_level ** 1.15
        base_power = 15 * rod_multiplier
        
        reward = 0
        if fish['is_trash'] and fish['id'] != 'chest':
            # "Эко-сбор": символическая плата за мусор
            reward = int(5 * rod_multiplier)
        else:
            reward = int(base_power * fish['mult'])

        user.balance += reward
        
        new_catch = Catch(
            user_id=user.telegram_id,
            fish_id=fish['id'],
            weight=weight,
            is_trash=fish['is_trash'],
            reward=reward
        )
        session.add(new_catch)

        await session.commit()
        return {
            "status": "caught", 
            "fish_id": fish['id'], "fish_emoji": fish['emoji'], "fish_color": fish['color'],
            "reward": reward, "weight": weight, "is_trash": fish['is_trash'],
            "rarity": fish.get('rarity', 1),
            "balance": user.balance, "energy": int(user.energy), 
            "afk_earned": afk_earned,
            "bait_common": user.bait_common,
            "bait_rare": user.bait_rare
        }

@app.post("/api/upgrade")
async def buy_upgrade(data: BuyRequest):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == data.telegram_id))
        user = result.scalars().first()
        success = False
        
        # --- ОБРАБОТКА ПОКУПОК ---
        if data.item_id == "rod":
            price = ROD_PRICES.get(user.rod_level + 1)
            if price and user.balance >= price:
                user.balance -= price; user.rod_level += 1; success = True
                
        elif data.item_id == "boat":
            price = BOAT_PRICES.get(user.boat_level + 1)
            if price and user.balance >= price:
                user.balance -= price; user.boat_level += 1; success = True
        
        elif data.item_id in CONSUMABLES:
            item = CONSUMABLES[data.item_id]
            if user.balance >= item['price']:
                user.balance -= item['price']
                success = True
                
                if data.item_id == "energy_drink":
                    user.energy = min(MAX_ENERGY, user.energy + item['energy'])
                elif data.item_id == "bait_common":
                    user.bait_common += item['amount']
                elif data.item_id == "bait_rare":
                    user.bait_rare += item['amount']

        await session.commit()
        
        return {
            "success": success, 
            "balance": user.balance, 
            "energy": int(user.energy),
            "rod_level": user.rod_level, 
            "boat_level": user.boat_level, 
            "rod_price": ROD_PRICES.get(user.rod_level + 1), 
            "boat_price": BOAT_PRICES.get(user.boat_level + 1),
            "bait_common": user.bait_common,
            "bait_rare": user.bait_rare
        }

@app.post("/api/ad_reward")
async def ad_reward(data: AdRewardRequest):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == data.telegram_id))
        user = result.scalars().first()
        if not user: return {"success": False}
        
        # ДИНАМИЧЕСКАЯ НАГРАДА
        # 500 база + (уровень * 250). На 10 уровне ~3000 монет.
        base_reward = 500
        scaling = user.rod_level * 250
        total_reward = base_reward + scaling
        
        user.balance += total_reward
        user.energy = 100
        await session.commit()
        return {"success": True, "balance": user.balance, "energy": int(user.energy), "reward": total_reward}

@app.get("/api/leaderboard")
async def get_leaderboard(type: str = "balance", period: str = "all"):
    async with AsyncSessionLocal() as session:
        date_filter = None
        now = datetime.utcnow()
        if period == "week": date_filter = now - timedelta(days=7)
        elif period == "month": date_filter = now - timedelta(days=30)
        elif period == "year": date_filter = now - timedelta(days=365)
        
        stmt = None
        
        if type == "balance":
            stmt = select(User.first_name, User.last_name, User.username, func.sum(Catch.reward).label("score")) \
                   .join(Catch, User.telegram_id == Catch.user_id) \
                   .group_by(User.telegram_id, User.first_name, User.last_name, User.username).order_by(desc("score"))
        elif type == "weight":
            stmt = select(User.first_name, User.last_name, User.username, func.sum(Catch.weight).label("score")) \
                   .join(Catch, User.telegram_id == Catch.user_id) \
                   .where(Catch.is_trash == False).group_by(User.telegram_id, User.first_name, User.last_name, User.username).order_by(desc("score"))
        elif type == "trash":
            stmt = select(User.first_name, User.last_name, User.username, func.count(Catch.id).label("score")) \
                   .join(Catch, User.telegram_id == Catch.user_id) \
                   .where(Catch.is_trash == True).group_by(User.telegram_id, User.first_name, User.last_name, User.username).order_by(desc("score"))
        
        if date_filter: stmt = stmt.where(Catch.caught_at >= date_filter)
        stmt = stmt.limit(10)
        
        total_stmt = select(func.count(User.telegram_id))
        
        try:
            result = await session.execute(stmt)
            data = result.all()
            total_result = await session.execute(total_stmt)
            total_count = total_result.scalar() or 0
        except Exception as e:
            logging.error(f"Error LB: {e}")
            return {"leaderboard": [], "total": 0}

        leaderboard_data = []
        for row in data:
            d_name = row.username
            if row.first_name:
                d_name = row.first_name
                if row.last_name:
                    d_name += f" {row.last_name}"
            if not d_name: d_name = "Fisher"
            leaderboard_data.append({"username": d_name, "value": row.score or 0})
        
        return {
            "leaderboard": leaderboard_data,
            "total": total_count
        }

@dp.message()
async def start_command(message: types.Message):
    markup = types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="🎣 Play", web_app=WebAppInfo(url=f"{WEBAPP_URL}/static/index.html"))]])
    await message.answer("Let's go fishing!", reply_markup=markup)

# --- INLINE MODE (ПОДЕЛИТЬСЯ УЛОВОМ) ---
@dp.inline_query()
async def inline_share_catch(query: types.InlineQuery):
    text = query.query.strip()
    
    # Ожидаем формат запроса: "fish_id|weight|rarity"
    # Если придет мусор, просто игнорируем
    if not text or "|" not in text:
        return

    try:
        # Разбиваем текст по разделителю
        parts = text.split("|")
        
        # Берем данные, только если есть хотя бы 2 части (id и weight)
        if len(parts) < 2:
            return
            
        fish_id = parts[0]
        weight = parts[1]
        # rarity = parts[2] # Пока не используем, но в строке оно есть
        
        # Ссылка на картинку (ВАЖНО: Должна быть HTTPS и доступна из интернета)
        # Убедитесь, что в config.yaml WEBAPP_URL ведет на реальный домен
        thumb_url = f"{WEBAPP_URL}/static/images/{fish_id}.png"
        
        # Формируем текст сообщения
        caption = f"🎣 <b>Look at this catch!</b>\n\n" \
                  f"🐠 <b>Fish:</b> {fish_id.capitalize()}\n" \
                  f"⚖️ <b>Weight:</b> {weight} kg\n" \
                  f"🔥 <b>Can you do better?</b>"

        # Кнопка под картинкой
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🎣 Try to catch better!", web_app=WebAppInfo(url=f"{WEBAPP_URL}/static/index.html"))
        ]])

        # Создаем результат
        # id должен быть уникальным для каждого запроса, используем хэш
        result_id = hashlib.md5(text.encode()).hexdigest()

        result = InlineQueryResultPhoto(
            id=result_id,
            photo_url=thumb_url,
            thumbnail_url=thumb_url,
            title="Share Catch",
            caption=caption,
            parse_mode="HTML",
            reply_markup=keyboard
        )

        # cache_time=0 чтобы при отладке изменения применялись сразу
        await query.answer([result], cache_time=0, is_personal=True)
        
    except Exception as e:
        # Логируем ошибку, чтобы видеть её в консоли
        logging.error(f"Inline error: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
    webhook = await bot.get_webhook_info()
    if webhook.url: await bot.delete_webhook()
    asyncio.create_task(dp.start_polling(bot))
    yield
    await bot.session.close()

app.router.lifespan_context = lifespan