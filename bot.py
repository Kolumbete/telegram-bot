import sqlite3
import logging
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from fastapi import FastAPI, Request
import uvicorn
import os

# ====== Конфиг ======
# Токен оставляем в коде (как просил)
TOKEN = "7699699715:AAFAOCQJ4uDDFmFOaKS0XRpCukFKjb5cym8"

# Публичный базовый URL твоего сервиса (можно задать в Render как ENV: WEBHOOK_BASE_URL)
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "https://telegram-bot-4ciw.onrender.com")
WEBHOOK_URL = f"{WEBHOOK_BASE_URL.rstrip('/')}/"

# Опционально: включить внутренний само-пинг (KEEPALIVE_SELF_PING=true)
KEEPALIVE_SELF_PING = os.getenv("KEEPALIVE_SELF_PING", "false").lower() in {"1", "true", "yes"}

# ====== Логирование ======
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ====== Aiogram ======
bot = Bot(token=TOKEN)
dp = Dispatcher()

# ====== FastAPI ======
app = FastAPI()


# ---- health / root ----
@app.get("/")
async def root():
    return {"message": "Bot is running!", "webhook_url": WEBHOOK_URL}

@app.head("/")
async def head_root():
    return {}

@app.get("/health")
async def health():
    return {"status": "ok"}


# ---- входящие апдейты из Telegram (вебхук) ----
@app.post("/")
async def process_update(update: dict, request: Request):
    # минимальное логирование, чтобы видеть входящие POST из Telegram
    try:
        ip = request.client.host if request and request.client else "unknown"
        logger.info(f"POST / from {ip} | keys={list(update.keys())}")
    except Exception:
        pass

    telegram_update = types.Update.model_validate(update)
    await dp.feed_update(bot, telegram_update)
    return {"ok": True}


# ====== SQLite ======
conn = sqlite3.connect("questions.db", check_same_thread=False)
cursor = conn.cursor()
cursor.executescript("""
CREATE TABLE IF NOT EXISTS topics (
    id INTEGER PRIMARY KEY,
    name TEXT
);
CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY,
    topic_id INTEGER,
    question TEXT,
    option_a TEXT,
    option_b TEXT,
    option_c TEXT,
    option_d TEXT,
    correct_answer TEXT,
    explanation TEXT,
    FOREIGN KEY (topic_id) REFERENCES topics(id)
);
""")
conn.commit()

def fetch_all(query, params=()):
    cursor.execute(query, params)
    return cursor.fetchall()


# ====== user_progress в памяти ======
user_progress = {}


# ====== Хэндлеры ======
@dp.message(Command("start", "restart"))
async def start_handler(message: types.Message):
    topics = fetch_all("SELECT id, name FROM topics")
    logger.info(f"/start from {message.from_user.id} | topics={len(topics)}")

    if not topics:
        await message.answer("В базе данных пока нет тем.")
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=name, callback_data=f"topic_{id}")]
            for id, name in topics
        ]
    )
    await message.answer("Выберите тему для тестирования:", reply_markup=keyboard)


@dp.callback_query(lambda c: c.data.startswith("topic_"))
async def topic_handler(callback: types.CallbackQuery):
    # быстрый ответ на callback, чтобы не ловить "query is too old"
    await callback.answer()

    topic_id = int(callback.data.split("_")[1])
    questions = fetch_all(
        """
        SELECT id, question, option_a, option_b, option_c, option_d, correct_answer, explanation
        FROM questions
        WHERE topic_id = ?
        ORDER BY RANDOM()
        """,
        (topic_id,)
    )

    if not questions:
        await callback.message.answer("Нет вопросов по этой теме.")
        return

    user_progress[callback.from_user.id] = {
        "questions": questions,
        "current_index": 0,
        "correct_answers": 0,
        "wrong_answers": [],
        "topic_id": topic_id,
        "username": callback.from_user.full_name,
    }

    await send_question(callback.from_user.id)


async def send_question(user_id: int):
    user_data = user_progress.get(user_id)
    if not user_data:
        return

    index = user_data["current_index"]
    if index >= len(user_data["questions"]):
        await finish_quiz(user_id)
        return

    q_id, q_text, a, b, c, d, correct, explanation = user_data["questions"][index]

    # Полный текст вариантов — в сообщении (не обрезается Telegram'ом)
    options_text = "\n".join([
        f"a) {a}",
        f"b) {b}",
        f"c) {c}",
        f"d) {d}",
    ])

    # Кнопки — только короткие метки, чтобы не было обрезаний "…"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="a", callback_data=f"answer_{index}_a")],
            [InlineKeyboardButton(text="b", callback_data=f"answer_{index}_b")],
            [InlineKeyboardButton(text="c", callback_data=f"answer_{index}_c")],
            [InlineKeyboardButton(text="d", callback_data=f"answer_{index}_d")],
        ]
    )

    await bot.send_message(
        user_id,
        f"Вопрос {index + 1} из {len(user_data['questions'])}:\n\n{q_text}\n\n{options_text}",
        reply_markup=keyboard,
    )


@dp.callback_query(lambda c: c.data.startswith("answer_"))
async def answer_handler(callback: types.CallbackQuery):
    # быстрый ответ
    await callback.answer()

    user_id = callback.from_user.id
    user_data = user_progress.get(user_id)

    if not user_data:
        await callback.message.answer("Сессия устарела, начните заново /start")
        return

    _, q_index_str, answer = callback.data.split("_")
    q_index = int(q_index_str)

    question = user_data["questions"][q_index]
    correct = question[6].lower()

    text = f"Вопрос {q_index + 1}:\n\n{question[1]}\nВаш ответ: {answer.upper()}\n"

    if answer == correct:
        user_data["correct_answers"] += 1
        response_text = f"{text}✅ Правильно!\n\nℹ {question[7]}"
    else:
        user_data["wrong_answers"].append((question[1], answer))
        response_text = f"{text}❌ Неправильно. Правильный ответ: {correct.upper()}\n\nℹ {question[7]}"

    await callback.message.answer(response_text)

    user_data["current_index"] += 1
    await send_question(user_id)


async def finish_quiz(user_id: int):
    user_data = user_progress.pop(user_id, None)
    if not user_data:
        return

    total = len(user_data["questions"])
    correct = user_data["correct_answers"]

    result_text = f"🎉 Тест завершен!\n✅ Правильных ответов: {correct}/{total}\n"
    result_text += (
        "🔥 Отличная работа!" if correct == total
        else "😊 Хороший результат!" if correct > total // 2
        else "😕 Попробуйте еще раз!"
    )

    await bot.send_message(user_id, result_text)

    report = (
        f"📊 Результаты пользователя\n"
        f"👤 Имя: {user_data['username']}\n"
        f"📌 Тема: {user_data['topic_id']}\n"
        f"✅ Правильно: {correct}/{total}\n"
        f"❌ Ошибки: {len(user_data['wrong_answers'])}"
    )
    await bot.send_message(838595372, report)


# ====== авто-вебхук + (опционально) само-пинг ======
async def _keepalive_loop():
    import aiohttp  # есть в зависимостях aiogram
    url = WEBHOOK_BASE_URL.rstrip("/") + "/health"
    logger.info(f"Keepalive self-ping is ON. Target: GET {url}")
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(url, timeout=10) as resp:
                    logger.info(f"Keepalive ping -> {resp.status}")
            except Exception as e:
                logger.warning(f"Keepalive ping failed: {e}")
            await asyncio.sleep(300)  # каждые 5 минут

@app.on_event("startup")
async def on_startup():
    logger.info(f"Setting webhook to: {WEBHOOK_URL}")
    # drop_pending_updates=True чтобы не копились старые апдейты после сна
    await bot.set_webhook(WEBHOOK_URL, drop_pending_updates=True)
    info = await bot.get_webhook_info()
    logger.info(f"Webhook set. Telegram says: {info}")

    if KEEPALIVE_SELF_PING and WEBHOOK_BASE_URL.startswith("http"):
        # запускаем фоновой таск само-пинга
        app.state.keepalive_task = asyncio.create_task(_keepalive_loop())


# ВАЖНО: намеренно НЕТ on_shutdown() и удаления вебхука,
# чтобы Render sleep/stop не обнулял вебхук.


# ====== Запуск ======
if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
