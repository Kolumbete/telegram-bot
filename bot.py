import sqlite3
import logging
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command, Filter
from aiogram.fsm.context import FSMContext

TOKEN = "7699699715:AAHmPFNIR7LUk2ubAowy6cOBUaG38ekG2fs"

bot = Bot(token=TOKEN)
dp = Dispatcher()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Подключение к БД
conn = sqlite3.connect("questions.db", check_same_thread=False)
cursor = conn.cursor()

# Создание таблиц
cursor.execute(
    """
CREATE TABLE IF NOT EXISTS topics (
    id INTEGER PRIMARY KEY,
    name TEXT
)"""
)

cursor.execute(
    """
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
)"""
)
conn.commit()

# Инициализация тестовых данных
cursor.execute("SELECT COUNT(*) FROM topics")
if cursor.fetchone()[0] == 0:
    cursor.execute(
        "INSERT INTO topics (name) VALUES (?)",
        ["Гигиена и стерилизация в тату-салонах"],
    )
    topic_id = cursor.lastrowid

    cursor.execute(
        """
    INSERT INTO questions 
    (topic_id, question, option_a, option_b, option_c, option_d, correct_answer, explanation)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            topic_id,
            "Какие требования предъявляются к помещению тату-салона?",
            "Наличие ковровых покрытий и мягкой мебели",
            "Отдельный кабинет с легко моющимися поверхностями, хорошей вентиляцией и освещением",
            "Совмещенное помещение для процедур и отдыха",
            "Отсутствие раковины для мытья рук",
            "b",
            "Для соблюдения санитарных норм необходимо, чтобы поверхности легко дезинфицировались, а воздух хорошо циркулировал.",
        ),
    )
    conn.commit()

user_progress = {}


class TopicFilter(Filter):
    async def __call__(self, callback: types.CallbackQuery) -> bool:
        return callback.data.startswith("topic_")


class AnswerFilter(Filter):
    async def __call__(self, callback: types.CallbackQuery) -> bool:
        return callback.data.startswith("answer_")


def get_all_questions(topic_id: int):
    cursor.execute(
        """
    SELECT id, question, option_a, option_b, option_c, option_d, correct_answer, explanation
    FROM questions 
    WHERE topic_id = ? 
    ORDER BY RANDOM()
    """,
        (topic_id,),
    )
    return cursor.fetchall()


@dp.message(Command("start", "restart"))
async def start_handler(message: types.Message):
    cursor.execute("SELECT id, name FROM topics")
    topics = cursor.fetchall()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    for topic_id, topic_name in topics:
        keyboard.inline_keyboard.append(
            [InlineKeyboardButton(text=topic_name, callback_data=f"topic_{topic_id}")]
        )

    await message.answer("Выберите тему для тестирования:", reply_markup=keyboard)


@dp.callback_query(TopicFilter())
async def topic_handler(callback: types.CallbackQuery):
    topic_id = int(callback.data.split("_")[1])
    questions = get_all_questions(topic_id)

    if not questions:
        await callback.message.answer("Нет вопросов по этой теме.")
        return

    user_progress[callback.from_user.id] = {
        "questions": questions,
        "current_index": 0,
        "correct_answers": 0,
        "wrong_answers": [],
        "topic_id": topic_id,
        "username": callback.from_user.full_name,  # Сохраняем имя пользователя
    }

    await callback.answer()
    await send_question(callback.from_user.id)


async def send_question(user_id: int):
    user_data = user_progress.get(user_id)
    if not user_data:
        return

    index = user_data["current_index"]
    if index >= len(user_data["questions"]):
        await finish_quiz(user_id)
        return

    question = user_data["questions"][index]
    (q_id, q_text, a, b, c, d, correct, explanation) = question

    # Формируем текст с номерами вариантов
    options_text = "\n".join([f"a) {a}", f"b) {b}", f"c) {c}", f"d) {d}"])

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"a) {a}", callback_data=f"answer_{index}_a")],
            [InlineKeyboardButton(text=f"b) {b}", callback_data=f"answer_{index}_b")],
            [InlineKeyboardButton(text=f"c) {c}", callback_data=f"answer_{index}_c")],
            [InlineKeyboardButton(text=f"d) {d}", callback_data=f"answer_{index}_d")],
        ]
    )

    await bot.send_message(
        user_id,
        f"Вопрос {index + 1} из {len(user_data['questions'])}:\n\n{q_text}\n\n{options_text}",
        reply_markup=keyboard,
    )


@dp.callback_query(AnswerFilter())
async def answer_handler(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    user_data = user_progress.get(user_id)

    if not user_data:
        return await callback.answer("Сессия устарела, начните заново /start")

    parts = callback.data.split("_")
    q_index = int(parts[1])
    answer = parts[2]

    question = user_data["questions"][q_index]
    correct = question[6].lower()

    # Сохраняем текст вопроса перед показом результата
    question_text = (
        f"Вопрос {q_index + 1}:\n\n{question[1]}\n\n" f"Ваш ответ: {answer.upper()}\n"
    )

    if answer == correct:
        user_data["correct_answers"] += 1
        text = f"{question_text}✅ Правильно!\n\nℹ {question[7]}"
    else:
        user_data["wrong_answers"].append((question[1], answer))
        text = f"{question_text}❌ Неправильно. Правильный ответ: {correct.upper()}\n\nℹ {question[7]}"

    # Отправляем результат как новое сообщение
    await callback.message.answer(text)

    # Переходим к следующему вопросу
    user_data["current_index"] += 1
    await send_question(user_id)
    await callback.answer()


async def finish_quiz(user_id: int):
    user_data = user_progress.pop(user_id, None)
    if not user_data:
        return

    total = len(user_data["questions"])
    correct = user_data["correct_answers"]

    text = f"🎉 Тест завершен!\n" f"✅ Правильных ответов: {correct}/{total}\n"

    if correct == total:
        text += "🔥 Отличная работа!"
    elif correct > total // 2:
        text += "😊 Хороший результат!"
    else:
        text += "😕 Попробуйте еще раз!"

    await bot.send_message(user_id, text)

    # Отправка отчета администратору
    report = (
        f"📊 Результаты пользователя\n"
        f"👤 Имя: {user_data['username']}\n"
        f"📌 Тема: {user_data['topic_id']}\n"
        f"✅ Правильно: {correct}/{total}\n"
        f"❌ Ошибки: {len(user_data['wrong_answers'])}"
    )
    await bot.send_message(440745793, report)


if __name__ == "__main__":
    asyncio.run(dp.start_polling(bot))
