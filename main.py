import yaml
import logging
import random
import time
import asyncio
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from aiogram import Bot, Dispatcher, types
from aiogram.types import WebAppInfo
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, BigInteger, Integer, String, Float, Boolean, DateTime, desc, select, func
from pydantic import BaseModel

# --- ЗАГРУЗКА КОНФИГА ---
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
    first_name = Column(String, nullable=True) # Имя
    last_name = Column(String, nullable=True)  # Фамилия
    balance = Column(Integer, default=0)
    energy = Column(Float, default=100.0)
    rod_level = Column(Integer, default=1) 
    boat_level = Column(Integer, default=0)
    last_active_at = Column(Integer, default=lambda: int(time.time()))

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

# --- ИГРОВЫЕ КОНСТАНТЫ ---
ROD_PRICES = {1: 0, 2: 500, 3: 1500, 4: 5000, 5: 15000, 6: 50000, 7: 150000, 8: 500000, 9: 1000000, 10: 5000000}
BOAT_PRICES = {1: 2000, 2: 10000, 3: 50000, 4: 200000, 5: 1000000}
BOAT_INCOME = {0: 0, 1: 2, 2: 10, 3: 50, 4: 200, 5: 1000}
ENERGY_REGEN_PER_SEC = 0.5 
MAX_ENERGY = 100

FISH_TABLE = [
    {"id": "weed", "emoji": "🌿", "mult": 0.0, "weight": 20, "color": "#64748b", "is_trash": True, "min_w": 0, "max_w": 0},
    {"id": "boot", "emoji": "👢", "mult": 0.0, "weight": 10, "color": "#64748b", "is_trash": True, "min_w": 0, "max_w": 0},
    {"id": "tin", "emoji": "🥫", "mult": 0.0, "weight": 10, "color": "#64748b", "is_trash": True, "min_w": 0, "max_w": 0},
    {"id": "bone", "emoji": "☠️", "mult": 0.0, "weight": 8, "color": "#64748b", "is_trash": True, "min_w": 0, "max_w": 0},
    {"id": "bag", "emoji": "🛍️", "mult": 0.0, "weight": 8, "color": "#64748b", "is_trash": True, "min_w": 0, "max_w": 0},
    {"id": "tire", "emoji": "🍩", "mult": 0.0, "weight": 5, "color": "#64748b", "is_trash": True, "min_w": 0, "max_w": 0},
    {"id": "minnow", "emoji": "🐟", "mult": 1.0, "weight": 45, "color": "#fff", "is_trash": False, "min_w": 0.05, "max_w": 0.15},
    {"id": "shrimp", "emoji": "🦐", "mult": 1.2, "weight": 40, "color": "#e2e8f0", "is_trash": False, "min_w": 0.01, "max_w": 0.05},
    {"id": "sardine", "emoji": "🐟", "mult": 1.5, "weight": 30, "color": "#cbd5e1", "is_trash": False, "min_w": 0.1, "max_w": 0.3},
    {"id": "carp", "emoji": "🎏", "mult": 1.8, "weight": 25, "color": "#fbbf24", "is_trash": False, "min_w": 0.5, "max_w": 2.5},
    {"id": "perch", "emoji": "🐠", "mult": 2.0, "weight": 25, "color": "#a5f3fc", "is_trash": False, "min_w": 0.3, "max_w": 1.2},
    {"id": "trout", "emoji": "🐟", "mult": 2.5, "weight": 20, "color": "#86efac", "is_trash": False, "min_w": 1.0, "max_w": 4.0},
    {"id": "clown", "emoji": "🤡", "mult": 3.0, "weight": 18, "color": "#f97316", "is_trash": False, "min_w": 0.1, "max_w": 0.3},
    {"id": "crab", "emoji": "🦀", "mult": 3.5, "weight": 15, "color": "#f87171", "is_trash": False, "min_w": 1.0, "max_w": 5.0},
    {"id": "jelly", "emoji": "🪼", "mult": 4.0, "weight": 12, "color": "#c084fc", "is_trash": False, "min_w": 0.5, "max_w": 2.0},
    {"id": "squid", "emoji": "🦑", "mult": 5.0, "weight": 10, "color": "#f472b6", "is_trash": False, "min_w": 0.5, "max_w": 3.0},
    {"id": "seahorse", "emoji": "🐉", "mult": 6.0, "weight": 10, "color": "#fde047", "is_trash": False, "min_w": 0.01, "max_w": 0.05},
    {"id": "pike", "emoji": "🐊", "mult": 7.0, "weight": 8, "color": "#4ade80", "is_trash": False, "min_w": 2.0, "max_w": 12.0},
    {"id": "eel", "emoji": "🐍", "mult": 8.0, "weight": 7, "color": "#facc15", "is_trash": False, "min_w": 1.0, "max_w": 5.0},
    {"id": "tuna", "emoji": "🐟", "mult": 12.0, "weight": 6, "color": "#60a5fa", "is_trash": False, "min_w": 20.0, "max_w": 250.0},
    {"id": "sword", "emoji": "🗡️", "mult": 15.0, "weight": 5, "color": "#93c5fd", "is_trash": False, "min_w": 30.0, "max_w": 300.0},
    {"id": "ray", "emoji": "👿", "mult": 20.0, "weight": 4, "color": "#818cf8", "is_trash": False, "min_w": 5.0, "max_w": 50.0},
    {"id": "catfish", "emoji": "🐡", "mult": 25.0, "weight": 4, "color": "#d946ef", "is_trash": False, "min_w": 10.0, "max_w": 100.0},
    {"id": "angler", "emoji": "👾", "mult": 35.0, "weight": 3, "color": "#a855f7", "is_trash": False, "min_w": 2.0, "max_w": 10.0},
    {"id": "turtle", "emoji": "🐢", "mult": 40.0, "weight": 3, "color": "#22c55e", "is_trash": False, "min_w": 30.0, "max_w": 150.0},
    {"id": "shark", "emoji": "🦈", "mult": 60.0, "weight": 2.5, "color": "#eab308", "is_trash": False, "min_w": 300.0, "max_w": 1500.0},
    {"id": "whale", "emoji": "🐳", "mult": 120.0, "weight": 1.5, "color": "#3b82f6", "is_trash": False, "min_w": 2000.0, "max_w": 10000.0},
    {"id": "chest", "emoji": "👑", "mult": 250.0, "weight": 0.5, "color": "#facc15", "is_trash": True, "min_w": 0, "max_w": 0},
    {"id": "mega", "emoji": "🦖", "mult": 500.0, "weight": 0.2, "color": "#ef4444", "is_trash": False, "min_w": 5000.0, "max_w": 20000.0},
    {"id": "kraken", "emoji": "🐙", "mult": 1000.0, "weight": 0.1, "color": "#dc2626", "is_trash": False, "min_w": 10000.0, "max_w": 50000.0},
]

# --- API REQUEST MODELS ---
class ClickRequest(BaseModel):
    telegram_id: int
class InitRequest(BaseModel):
    telegram_id: int
    username: str | None = None
    first_name: str | None = None # Принимаем имя
    last_name: str | None = None  # Принимаем фамилию
class BuyRequest(BaseModel):
    telegram_id: int
    item_id: str
class AdRewardRequest(BaseModel):
    telegram_id: int

def calculate_offline_progress(user, current_time, is_active=False):
    time_diff = current_time - user.last_active_at
    if time_diff < 0: time_diff = 0
    income = BOAT_INCOME.get(user.boat_level, 0)
    earned = int(time_diff * income)
    user.balance += earned
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
            user = User(
                telegram_id=data.telegram_id, 
                username=data.username,
                first_name=data.first_name, # Сохраняем имя
                last_name=data.last_name,   # Сохраняем фамилию
                last_active_at=current_time
            )
            session.add(user)
        else:
            # Обновляем данные, если они изменились в телеграме
            if data.username: user.username = data.username
            if data.first_name: user.first_name = data.first_name
            if data.last_name: user.last_name = data.last_name
            earned = calculate_offline_progress(user, current_time)
        
        await session.commit()
        return {
            "balance": user.balance, "energy": int(user.energy),
            "rod_level": user.rod_level, "boat_level": user.boat_level,
            "rod_price": ROD_PRICES.get(user.rod_level + 1), "boat_price": BOAT_PRICES.get(user.boat_level + 1),
            "offline_earned": earned, "adsgram_id": ADSGRAM_ID
        }

@app.post("/api/fish")
async def fish_action(data: ClickRequest):
    current_time = int(time.time())
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == data.telegram_id))
        user = result.scalars().first()
        afk_earned = calculate_offline_progress(user, current_time, is_active=True)
        
        if int(user.energy) < 1:
            await session.commit()
            return {"status": "no_energy", "balance": user.balance, "energy": int(user.energy), "afk_earned": afk_earned}

        catch_chance = 0.15 + (user.rod_level * 0.03)
        catch_chance = min(catch_chance, 0.60)
        
        energy_cost = 1
        if int(user.energy) < 70: energy_cost = 2
        if int(user.energy) < 30: energy_cost = 4
        user.energy = max(0.0, user.energy - energy_cost)
        
        if random.random() > catch_chance:
            await session.commit()
            return {"status": "miss", "balance": user.balance, "energy": int(user.energy), "afk_earned": afk_earned}

        fish = random.choices(FISH_TABLE, weights=[f['weight'] for f in FISH_TABLE], k=1)[0]
        weight = 0.0
        if not fish['is_trash']:
            weight = round(random.uniform(fish['min_w'], fish['max_w']), 2)
        
        base_power = 10 * user.rod_level
        reward = int(base_power * fish['mult'])
        if fish['id'] == 'boot': reward = 0

        user.balance += reward
        
        # Запись улова в историю
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
            "status": "caught", "fish_id": fish['id'], "fish_emoji": fish['emoji'], "fish_color": fish['color'],
            "reward": reward, "weight": weight, "is_trash": fish['is_trash'],
            "balance": user.balance, "energy": int(user.energy), "afk_earned": afk_earned
        }

@app.post("/api/upgrade")
async def buy_upgrade(data: BuyRequest):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == data.telegram_id))
        user = result.scalars().first()
        success = False
        if data.item_id == "rod":
            price = ROD_PRICES.get(user.rod_level + 1)
            if price and user.balance >= price:
                user.balance -= price; user.rod_level += 1; success = True
        elif data.item_id == "boat":
            price = BOAT_PRICES.get(user.boat_level + 1)
            if price and user.balance >= price:
                user.balance -= price; user.boat_level += 1; success = True
        await session.commit()
        return {"success": success, "balance": user.balance, "rod_level": user.rod_level, "boat_level": user.boat_level, "rod_price": ROD_PRICES.get(user.rod_level + 1), "boat_price": BOAT_PRICES.get(user.boat_level + 1)}

@app.post("/api/ad_reward")
async def ad_reward(data: AdRewardRequest):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == data.telegram_id))
        user = result.scalars().first()
        if not user: return {"success": False}
        user.balance += 2000
        user.energy = 100
        await session.commit()
        return {"success": True, "balance": user.balance, "energy": int(user.energy), "reward": 2000}

# --- ИСПРАВЛЕННЫЙ ЛИДЕРБОРД ---
@app.get("/api/leaderboard")
async def get_leaderboard(type: str = "balance", period: str = "all"):
    async with AsyncSessionLocal() as session:
        date_filter = None
        now = datetime.utcnow()
        if period == "week": date_filter = now - timedelta(days=7)
        elif period == "month": date_filter = now - timedelta(days=30)
        elif period == "year": date_filter = now - timedelta(days=365)
        
        stmt = None
        
        # Мы группируем по ID, но выбираем также имена для отображения
        if type == "balance":
            # Считаем ДОХОД (reward) за период
            stmt = select(User.first_name, User.last_name, User.username, func.sum(Catch.reward).label("score")) \
                   .join(Catch, User.telegram_id == Catch.user_id) \
                   .group_by(User.telegram_id, User.first_name, User.last_name, User.username).order_by(desc("score"))
        elif type == "weight":
            # Считаем ВЕС (weight)
            stmt = select(User.first_name, User.last_name, User.username, func.sum(Catch.weight).label("score")) \
                   .join(Catch, User.telegram_id == Catch.user_id) \
                   .where(Catch.is_trash == False).group_by(User.telegram_id, User.first_name, User.last_name, User.username).order_by(desc("score"))
        elif type == "trash":
            # Считаем ШТУКИ мусора (count)
            stmt = select(User.first_name, User.last_name, User.username, func.count(Catch.id).label("score")) \
                   .join(Catch, User.telegram_id == Catch.user_id) \
                   .where(Catch.is_trash == True).group_by(User.telegram_id, User.first_name, User.last_name, User.username).order_by(desc("score"))
        
        if date_filter: stmt = stmt.where(Catch.caught_at >= date_filter)
        stmt = stmt.limit(10)
        
        # Считаем общее кол-во игроков
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
            # Логика склейки имени: Имя + Фамилия, или Username, или "Fisher"
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

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
    webhook = await bot.get_webhook_info()
    if webhook.url: await bot.delete_webhook()
    asyncio.create_task(dp.start_polling(bot))
    yield
    await bot.session.close()

app.router.lifespan_context = lifespan