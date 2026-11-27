import asyncio
from datetime import datetime
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from dotenv import load_dotenv
from groq import Groq
import httpx
from tavily import TavilyClient

# Загружаем переменные окружения
load_dotenv()

# Инициализация клиентов
bot = Bot(token=os.getenv("BOT_TOKEN"))
dp = Dispatcher()
tavily_client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))
groq_client = Groq(
    api_key=os.getenv("GROQ_API_KEY"), http_client=httpx.Client()
)

# Хранилище выбранных видов спорта для пользователей
user_sports = {}

# Создаём клавиатуру с видами спорта
sport_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="⚽ Футбол"),
            KeyboardButton(text="🏒 Хоккей"),
        ],
        [
            KeyboardButton(text="🏐 Волейбол"),
            KeyboardButton(text="🎮 Киберспорт"),
        ],
        [
            KeyboardButton(text="🏀 Баскетбол"),
            KeyboardButton(text="🎾 Теннис"),
        ],
        [
            KeyboardButton(text="🥊 ММА"),
        ],
    ],
    resize_keyboard=True,
    one_time_keyboard=False,
)


async def search_match_info(match_name: str, sport: str) -> str:
    """Поиск информации о матче через Tavily"""
    try:
        print(f"[INFO] 🔍 Поиск информации о матче: {match_name} ({sport})")
        print(f"[INFO] 📡 Отправка запроса в Tavily API...")

        # ИСПРАВЛЕНО: убран topic="news" (он не ищет на русском) и оптимизирован запрос
        response = tavily_client.search(
            query=f"{sport} {match_name} прогноз статистика форма обзор матча",
            search_depth="basic",
            max_results=5,
            include_raw_content=False,
        )

        results = response.get("results", [])
        print(f"[INFO] ✅ Получено {len(results)} результатов от Tavily")

        info = ""
        for result in results:
            info += f"Заголовок: {result.get('title', '')}\n"
            info += f"Содержание: {result.get('content', '')}\n"
            info += f"URL: {result.get('url', '')}\n\n"

        print(f"[INFO] 📝 Собрано информации: {len(info)} символов")
        return info if info.strip() else "Информация не найдена"
    except Exception as e:
        print(f"[ERROR] ❌ Ошибка при поиске: {str(e)}")
        return f"ERROR_TAVILY: {str(e)}"


async def generate_analysis(
    match_name: str, search_info: str, sport: str
) -> str:
    """Генерация аналитической сводки через Groq"""
    if sport == "ММА":
        section_name = "🥊 **Бой**"
        form_section = """📊 **Анализ бойцов**:
- Боец 1: [анализ формы, стиль, победы/поражения]
- Боец 2: [анализ формы, стиль, победы/поражения]"""
        injuries_section = """🤕 **Травмы и состояние**:
- [состояние бойцов, возможные травмы]"""
    else:
        section_name = "🏆 **Матч**"
        form_section = """📊 **Анализ формы команд**:
- Команда 1: [анализ]
- Команда 2: [анализ]"""
        injuries_section = """🤕 **Травмы и дисквалификации**:
- [список травмированных игроков]"""

    prompt = f"""
Проанализируй информацию о матче/бое "{match_name}" ({sport}) и создай краткую аналитическую сводку.
Найденная информация:
{search_info}
Создай структурированный ответ на русском языке:
═══════════════════════════════════════
{section_name}: {match_name}
═══════════════════════════════════════
📅 Дата: [укажи дату если есть]
🏅 Вид спорта: {sport}
📊 Анализ формы:
{form_section}
🤕 Травмы и состояние:
{injuries_section}
📰 Свежие новости:
• [краткие новости]
🎯 Прогноз экспертов:
[твой прогноз на основе данных]
⚠️ Важные факторы:
• [ключевые факторы]
Важно: Пиши кратко, без повторов, по существу.
"""

    # Список моделей: если первая недоступна (ошибка 404), бот сам возьмет следующую
    models_to_try = [
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "llama3-70b-8192",
        "gemma2-9b-it",
    ]

    last_error = ""
    for model_name in models_to_try:
        try:
            print(
                f"[INFO] 📤 Отправка запроса в Groq (модель: {model_name})..."
            )
            response = groq_client.chat.completions.create(
                model=model_name,
                messages=[
                    {
                        "role": "system",
                        "content": "Ты - спортивный аналитик. Пиши кратко на русском языке, без повторов. Используй эмодзи.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.5,
                max_tokens=1000,
            )
            result = response.choices[0].message.content
            print(
                f"[INFO] ✅ Анализ сгенерирован ({model_name}): {len(result)} символов"
            )
            return result
        except Exception as e:
            last_error = str(e)
            print(
                f"[WARNING] ⚠️ Модель {model_name} не сработала: {last_error}. Пробую следующую..."
            )

    return f"Ошибка при генерации анализа: {last_error}"


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Обработчик команды /start"""
    user_id = message.from_user.id
    user_sports[user_id] = None
    await message.answer(
        "👋 Привет! Я бот для аналитики спортивных матчей и боёв.\n\n"
        "📌 Сначала выбери вид спорта с помощью кнопок ниже, "
        'затем отправь название матча или боя (например: "Арсенал - Челси" или "Конор Макгрегор - Хабиб Нурмагомедов").\n\n'
        "⚡ Я проанализирую свежие новости, травмы, форму и сделаю прогноз для ставок!",
        reply_markup=sport_keyboard,
    )
    print(f"[INFO] 👤 Пользователь {user_id} запустил бота")


@dp.message()
async def handle_message(message: types.Message):
    """Обработчик всех сообщений"""
    user_id = message.from_user.id
    text = message.text
    print(f"[DEBUG] 📨 Получено сообщение от {user_id}: '{text}'")

    sports = {
        "⚽ Футбол": ("футбол", "Арсенал - Челси"),
        "🏒 Хоккей": ("хоккей", "ЦСКА - Ак Барс"),
        "🏐 Волейбол": ("волейбол", "Зенит - Динамо"),
        "🎮 Киберспорт": ("киберспорт", "Team Spirit - Team Liquid"),
        "🏀 Баскетбол": ("баскетбол", "Лейкерс - Уорриорз"),
        "🎾 Теннис": ("теннис", "Джокович - Алькарас"),
        "🥊 ММА": ("ММА", "Конор Макгрегор - Хабиб Нурмагомедов"),
    }

    if text in sports:
        selected_sport, example = sports[text]
        user_sports[user_id] = selected_sport
        print(
            f"[INFO] 🎯 Пользователь {user_id} выбрал вид спорта: {selected_sport}"
        )
        await message.answer(
            f"✅ Выбран вид спорта: {text}\n\n"
            f'📌 Теперь отправь название матча/боя (например: "{example}")'
        )
        return

    if user_sports.get(user_id) is None:
        await message.answer(
            "⚠️ Сначала выбери вид спорта с помощью кнопок ниже!",
            reply_markup=sport_keyboard,
        )
        return

    match_name = text
    selected_sport = user_sports[user_id]
    print(f"[INFO] 📨 Получен запрос: {selected_sport} - {match_name}")

    status_message = await message.answer(
        f"🔍 Ищу информацию о матче: {match_name} ({selected_sport})...\n"
        f"⏳ Это может занять 15-30 секунд."
    )

    try:
        await status_message.edit_text("📰 Поиск новостей и информации...")
        search_info = await search_match_info(match_name, selected_sport)

        # Если проблема с ключом Tavily — покажем реальную ошибку
        if search_info.startswith("ERROR_TAVILY:"):
            await status_message.edit_text(
                f"❌ Ошибка поисковика Tavily (проверьте TAVILY_API_KEY):\n{search_info}"
            )
            return

        if "Информация не найдена" in search_info or len(search_info) < 50:
            await status_message.edit_text(
                "😔 К сожалению, не удалось найти достаточно информации об этом матче.\n"
                "Попробуй:\n"
                "• Проверить правильность названия команды\n"
                "• Указать более точное название матча\n"
                "• Попробовать другой матч"
            )
            return

        await status_message.edit_text("🤖 Генерация аналитической сводки...")
        analysis = await generate_analysis(
            match_name, search_info, selected_sport
        )

        if not analysis or len(analysis.strip()) == 0:
            await status_message.edit_text(
                "❌ Не удалось сгенерировать анализ. Попробуйте другой запрос."
            )
            return

        await status_message.edit_text("✅ Готово!")
        await message.answer(analysis)
        print(f"[INFO] ✅ Успешно! Ответ отправлен пользователю {user_id}")
    except Exception as e:
        print(f"[ERROR] ❌ Ошибка в обработчике: {str(e)}")
        await status_message.edit_text(
            f"❌ Произошла ошибка: {str(e)}\n\nПопробуй позже."
        )


async def main():
    """Запуск бота"""
    print("=" * 60)
    print("🚀 ЗАПУСК БОТА ДЛЯ АНАЛИТИКИ СПОРТИВНЫХ МАТЧЕЙ")
    print("=" * 60)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
