import os
import asyncio
import httpx
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from tavily import TavilyClient
from groq import Groq

# Загружаем переменные окружения и очищаем от случайных пробелов/кавычек
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip().strip('"').strip("'")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "").strip().strip('"').strip("'")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip().strip('"').strip("'")

# Инициализация клиентов (с фиксом httpx для Bothost)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
tavily_client = TavilyClient(api_key=TAVILY_API_KEY)
groq_client = Groq(
    api_key=GROQ_API_KEY,
    http_client=httpx.Client()
)

# Хранилище выбранных видов спорта для пользователей
user_sports = {}

# Клавиатура с видами спорта
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
    one_time_keyboard=False
)


async def build_english_query(match_name: str, sport: str) -> str:
    """Переводит название матча на английский для поиска по мировым базам статистики"""
    try:
        resp = await asyncio.to_thread(
            groq_client.chat.completions.create,
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": "Translate the sports match query into a short English search query (max 6 words) to find match stats, H2H, and predictions. Output ONLY the search query, no quotes."
                },
                {"role": "user", "content": f"{sport}: {match_name}"}
            ],
            temperature=0.2,
            max_tokens=30
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return f"{match_name} {sport} match stats prediction"


async def search_match_info(match_name: str, sport: str) -> str:
    """Умный двуязычный поиск информации о матче через Tavily"""
    try:
        print(f"[INFO] 🔍 Поиск информации о матче: {match_name} ({sport})")
        info = ""

        # 1. Поиск на русском языке (без ограничения topic="news")
        ru_query = f"{match_name} {sport} прогноз статистика форма"
        response_ru = await asyncio.wait_for(
            asyncio.to_thread(
                tavily_client.search,
                query=ru_query,
                search_depth="basic",
                max_results=5,
                include_raw_content=False
            ),
            timeout=25.0
        )

        for result in response_ru.get("results", []):
            info += f"Заголовок: {result.get('title', '')}\n"
            info += f"Содержание: {result.get('content', '')}\n\n"

        # 2. Дополнительный поиск на английском (особенно важен для Киберспорта, ММА и сборных)
        en_query = await build_english_query(match_name, sport)
        print(f"[INFO] 🌍 Доп. англоязычный запрос: {en_query}")
        response_en = await asyncio.wait_for(
            asyncio.to_thread(
                tavily_client.search,
                query=en_query,
                search_depth="basic",
                max_results=5,
                include_raw_content=False
            ),
            timeout=25.0
        )

        for result in response_en.get("results", []):
            info += f"Title: {result.get('title', '')}\n"
            info += f"Content: {result.get('content', '')}\n\n"

        print(f"[INFO] 📝 Собрано информации: {len(info)} символов")
        return info if info.strip() else "Информация не найдена"

    except Exception as e:
        print(f"[ERROR] ❌ Ошибка Tavily: {str(e)}")
        return f"TAVILY_ERROR: {str(e)}"


async def generate_analysis(match_name: str, search_info: str, sport: str) -> str:
    """Генерация аналитической сводки через Groq (Llama 3.3 70B)"""
    try:
        if sport == "ММА":
            section_name = "🥊 Бой"
            form_section = """📊 АНАЛИЗ БОЙЦОВ:
• Боец 1: [анализ формы, стиль, рекорды]
• Боец 2: [анализ формы, стиль, рекорды]"""
            injuries_section = """🤕 СОСТОЯНИЕ И ФАКТОРЫ:
• [состояние бойцов, габариты, кардио, возможные травмы]"""
        else:
            section_name = "🏆 Матч"
            form_section = """📊 АНАЛИЗ ФОРМЫ КОМАНД:
• Команда/Игрок 1: [текущая форма и последние результаты]
• Команда/Игрок 2: [текущая форма и последние результаты]"""
            injuries_section = """🤕 СОСТАВЫ И ПОТЕРИ:
• [ключевые игроки, замены или травмы]"""

        prompt = f"""
Проанализируй информацию о событии "{match_name}" ({sport}) и создай подробную аналитическую сводку для ставок.
Даже если прямого анонса сегодняшнего матча мало, используй найденную статистику команд/игроков, их последние игры и общую силу.
НЕ используй символы звездочек (**) для жирного текста.

Найденная информация из интернета:
{search_info}

Сделай ответ строго по этому шаблону:

══════════════════════════
{section_name}: {match_name}
🏅 Вид спорта: {sport}
══════════════════════════

{form_section}

{injuries_section}

📰 СВЕЖИЕ ДАННЫЕ И СТАТИСТИКА:
• [факт или статистика 1]
• [факт или статистика 2]
• [факт или статистика 3]

⚠️ ВАЖНЫЕ ТРЕНДЫ:
• [тренд 1]
• [тренд 2]

🎯 ВЕРДИКТ ИИ-АНАЛИТИКА:
💭 Оценка шансов: [кто фаворит и почему]
📈 Варианты для ставки: [1-2 наиболее логичных исхода/тотала/форы с кратким обоснованием]
══════════════════════════
"""

        response = await asyncio.to_thread(
            groq_client.chat.completions.create,
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": "Ты — профессиональный спортивный аналитик. Пиши четко, по делу, на русском языке. Не используй символы * или **."
                },
                {"role": "user", "content": prompt}
            ],
            temperature=0.5,
            max_tokens=1500
        )

        return response.choices[0].message.content

    except Exception as e:
        print(f"[ERROR] ❌ Ошибка Groq: {str(e)}")
        return f"Ошибка при генерации анализа: {str(e)}"


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    user_sports[user_id] = None

    await message.answer(
        "👋 Привет! Я ИИ-бот для аналитики спортивных матчей и боёв.\n\n"
        "📌 Сначала выбери вид спорта с помощью кнопок ниже, "
        "затем отправь название матча или боя (например: \"Арсенал - Челси\" или \"Spirit - Liquid\").\n\n"
        "⚡ Я соберу статистику, проверю форму команд и выделю лучшие тренды для ставок!",
        reply_markup=sport_keyboard
    )


@dp.message()
async def handle_message(message: types.Message):
    if not message.text:
        return

    user_id = message.from_user.id
    text = message.text.strip()

    sports = {
        "⚽ Футбол": ("футбол", "Арсенал - Челси"),
        "🏒 Хоккей": ("хоккей", "ЦСКА - Ак Барс"),
        "🏐 Волейбол": ("волейбол", "Зенит - Динамо"),
        "🎮 Киберспорт": ("киберспорт", "Team Spirit - Team Liquid"),
        "🏀 Баскетбол": ("баскетбол", "Лейкерс - Уорриорз"),
        "🎾 Теннис": ("теннис", "Джокович - Алькарас"),
        "🥊 ММА": ("ММА", "Махачев - Царукян"),
    }

    if text in sports:
        selected_sport, example = sports[text]
        user_sports[user_id] = selected_sport
        await message.answer(
            f"✅ Выбран вид спорта: {text}\n\n"
            f"📌 Теперь отправь название матча/боя (например: \"{example}\")"
        )
        return

    if user_sports.get(user_id) is None:
        await message.answer(
            "⚠️ Сначала выбери вид спорта с помощью кнопок ниже!",
            reply_markup=sport_keyboard
        )
        return

    match_name = text
    selected_sport = user_sports[user_id]

    status_message = await message.answer(
        f"🔍 Анализирую: {match_name} ({selected_sport})...\n"
        f"⏳ Собираю статистику по русским и мировым базам (10-15 сек)."
    )

    try:
        search_info = await search_match_info(match_name, selected_sport)

        # Если проблема в ключе Tavily — сразу покажем точную причину
        if search_info.startswith("TAVILY_ERROR:"):
            await status_message.edit_text(
                f"⚠️ Ошибка поисковика Tavily:\n{search_info}\n\n"
                f"Проверь ключ TAVILY_API_KEY во вкладке 'Переменные' на Bothost."
            )
            return

        if search_info == "Информация не найдена" or len(search_info) < 30:
            await status_message.edit_text(
                "😔 По этому запросу совсем нет данных в сети. Проверь названия команд."
            )
            return

        await status_message.edit_text("🤖 Данные собраны! ИИ формирует аналитический отчет...")
        analysis = await generate_analysis(match_name, search_info, selected_sport)

        await status_message.delete()
        await message.answer(analysis)

    except Exception as e:
        await status_message.edit_text(f"❌ Произошла ошибка: {str(e)}")


async def main():
    print("🚀 БОТ УСПЕШНО ЗАПУЩЕН (УМНЫЙ ПОИСК ВКЛЮЧЕН)")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
