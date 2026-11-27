import asyncio
import html
import os
import re
import urllib.parse
import xml.etree.ElementTree as ET
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

tavily_key = (os.getenv("TAVILY_API_KEY") or "").strip()
tavily_client = TavilyClient(api_key=tavily_key) if tavily_key else None

groq_client = Groq(
    api_key=(os.getenv("GROQ_API_KEY") or "").strip(),
    http_client=httpx.Client(),
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


async def fallback_free_search(match_name: str, sport: str) -> str:
    """Резервный бесплатный поиск (Google News + Bing), если Tavily заблокирован (403)"""
    print(f"[INFO] 🔄 Запуск резервного поиска для: {match_name} ({sport})")
    info = ""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
    }

    queries = [f"{match_name} {sport} прогноз обзор", f"{match_name} {sport}"]

    async with httpx.AsyncClient(
        headers=headers, follow_redirects=True, timeout=15.0
    ) as client:
        # 1. Ищем свежие новости через Google News RSS
        for q in queries:
            if len(info) > 1500:
                break
            try:
                encoded_q = urllib.parse.quote(q)
                url = f"https://news.google.com/rss/search?q={encoded_q}&hl=ru&gl=RU&ceid=RU:ru"
                resp = await client.get(url)
                if resp.status_code == 200:
                    root = ET.fromstring(resp.text)
                    for item in root.findall(".//item")[:6]:
                        title = item.findtext("title", "")
                        pub_date = item.findtext("pubDate", "")
                        raw_desc = html.unescape(
                            item.findtext("description", "")
                        )
                        desc = re.sub(r"<[^>]+>", "", raw_desc)
                        info += f"Новость: {title} ({pub_date})\nОписание: {desc}\n\n"
            except Exception as e:
                print(f"[WARNING] Ошибка Google RSS: {e}")

        # 2. Дополняем данными через Bing RSS
        try:
            encoded_q = urllib.parse.quote(
                f"{sport} {match_name} прогноз статистика форма"
            )
            bing_url = f"https://www.bing.com/search?q={encoded_q}&format=rss"
            resp = await client.get(bing_url)
            if resp.status_code == 200:
                root = ET.fromstring(resp.text)
                for item in root.findall(".//item")[:5]:
                    title = item.findtext("title", "")
                    desc = item.findtext("description", "")
                    info += f"Заголовок: {title}\nСодержание: {desc}\n\n"
        except Exception as e:
            print(f"[WARNING] Ошибка Bing RSS: {e}")

    print(f"[INFO] ✅ Резервный поиск собрал: {len(info)} символов")
    return info


async def search_match_info(match_name: str, sport: str) -> str:
    """Поиск информации о матче (Tavily + автоматический резервный поиск)"""
    info = ""

    # Попытка 1: Пробуем Tavily
    if tavily_client:
        try:
            print(f"[INFO] 🔍 Поиск в Tavily: {match_name} ({sport})")
            response = tavily_client.search(
                query=f"{sport} {match_name} прогноз статистика форма обзор",
                search_depth="basic",
                max_results=5,
                include_raw_content=False,
            )
            for result in response.get("results", []):
                info += f"Заголовок: {result.get('title', '')}\n"
                info += f"Содержание: {result.get('content', '')}\n\n"
        except Exception as e:
            print(
                f"[WARNING] ⚠️ Tavily недоступен ({str(e)}). Переключаюсь на резервный поиск..."
            )

    # Попытка 2: Если Tavily выдал ошибку 403 или ничего не нашёл — включаем резервный поиск
    if len(info.strip()) < 100:
        info = await fallback_free_search(match_name, sport)

    return info if info.strip() else "Свежих новостей в ленте мало, используй общую статистику команд."


async def generate_analysis(
    match_name: str, search_info: str, sport: str
) -> str:
    """Генерация аналитической сводки через Groq"""
    if sport == "ММА":
        section_name = "🥊 **Бой**"
        form_section = """📊 **Анализ бойцов**:
- Боец 1: [анализ формы, стиль, сильные стороны]
- Боец 2: [анализ формы, стиль, сильные стороны]"""
        injuries_section = """🤕 **Состояние и весогонка**:
- [состояние бойцов, форма перед боем]"""
    else:
        section_name = "🏆 **Матч**"
        form_section = """📊 **Анализ формы команд**:
- Команда 1: [анализ текущей игры]
- Команда 2: [анализ текущей игры]"""
        injuries_section = """🤕 **Кадровые потери и состав**:
- [информация о составах и травмах]"""

    prompt = f"""
Проанализируй матч/бой "{match_name}" ({sport}) и создай аналитическую сводку для ставок.
Найденная информация из интернета:
{search_info}

Если в найденной информации каких-то деталей не хватает, дополни анализ на основе своих знаний об этих командах/спортсменах и их статистике личных встреч.

Создай структурированный ответ на русском языке:
═══════════════════════════════════════
{section_name}: {match_name}
═══════════════════════════════════════
🏅 Вид спорта: {sport}

{form_section}

{injuries_section}

📰 Сводка и контекст матча:
• [ключевые факты и новости]

🎯 Прогноз экспертов:
[развернутый прогноз и варианты ставок]

⚠️ Важные факторы:
• [на что обратить внимание]

Важно: Пиши кратко, уверенно, без повторов, по существу.
"""

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
                        "content": "Ты — профессиональный спортивный аналитик. Пиши информативно на русском языке, используй эмодзи.",
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
            print(f"[WARNING] ⚠️ Модель {model_name} не сработала: {last_error}")

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
        "⚡ Я проанализирую свежие новости, форму и сделаю прогноз для ставок!",
        reply_markup=sport_keyboard,
    )


@dp.message()
async def handle_message(message: types.Message):
    """Обработчик всех сообщений"""
    user_id = message.from_user.id
    text = message.text

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

    status_message = await message.answer(
        f"🔍 Ищу информацию о матче: {match_name} ({selected_sport})...\n"
        f"⏳ Это может занять 15-30 секунд."
    )

    try:
        await status_message.edit_text("📰 Поиск новостей и статистики...")
        search_info = await search_match_info(match_name, selected_sport)

        await status_message.edit_text("🤖 Генерация аналитической сводки...")
        analysis = await generate_analysis(
            match_name, search_info, selected_sport
        )

        await status_message.edit_text("✅ Готово!")
        await message.answer(analysis)
    except Exception as e:
        print(f"[ERROR] ❌ Ошибка в обработчике: {str(e)}")
        await status_message.edit_text(
            f"❌ Произошла ошибка: {str(e)}\n\nПопробуй позже."
        )


async def main():
    """Запуск бота"""
    print("🚀 ЗАПУСК БОТА ДЛЯ АНАЛИТИКИ СПОРТИВНЫХ МАТЧЕЙ")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
