import os
import re
import html
import asyncio
import urllib.parse
import xml.etree.ElementTree as ET
import httpx
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from tavily import TavilyClient
from groq import Groq

# Загружаем переменные окружения
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip().strip('"').strip("'")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "").strip().strip('"').strip("'")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip().strip('"').strip("'")

# Инициализация клиентов
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
tavily_client = TavilyClient(api_key=TAVILY_API_KEY) if TAVILY_API_KEY else None
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


def clean_html(raw_html: str) -> str:
    """Очищает текст от HTML-тегов"""
    clean = re.sub(r"<[^>]+>", " ", raw_html)
    return html.unescape(re.sub(r"\s+", " ", clean)).strip()


async def build_english_query(match_name: str, sport: str) -> str:
    """Переводит название матча на английский для мирового поиска"""
    try:
        resp = await asyncio.to_thread(
            groq_client.chat.completions.create,
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": "Translate the sports match into a short English search query (max 6 words) with team names and sport. Output ONLY the search query, no quotes."
                },
                {"role": "user", "content": f"{sport}: {match_name}"}
            ],
            temperature=0.2,
            max_tokens=30
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return f"{match_name} {sport}"


async def google_and_ddg_search(query: str, lang: str = "ru") -> str:
    """Бесплатный и безотказный поиск через Google News RSS и DuckDuckGo (без ключей и ошибок 403)"""
    results_text = ""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }

    async with httpx.AsyncClient(headers=headers, timeout=15.0, follow_redirects=True) as client:
        # 1. Поиск свежих новостей и превью через Google News RSS
        try:
            encoded_q = urllib.parse.quote(query)
            if lang == "ru":
                rss_url = f"https://news.google.com/rss/search?q={encoded_q}&hl=ru&gl=RU&ceid=RU:ru"
            else:
                rss_url = f"https://news.google.com/rss/search?q={encoded_q}&hl=en-US&gl=US&ceid=US:en"

            resp = await client.get(rss_url)
            if resp.status_code == 200:
                root = ET.fromstring(resp.text)
                items = root.findall(".//item")[:6]
                for item in items:
                    title = item.findtext("title", "")
                    desc = clean_html(item.findtext("description", ""))
                    pub_date = item.findtext("pubDate", "")
                    results_text += f"Новость ({pub_date}): {title}\nДетали: {desc}\n\n"
        except Exception as e:
            print(f"[WARN] Ошибка Google RSS ({lang}): {e}")

        # 2. Поиск аналитики и статистики через DuckDuckGo HTML
        try:
            ddg_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query + ' прогноз статистика')}"
            resp = await client.get(ddg_url)
            if resp.status_code == 200:
                snippets = re.findall(r'<a class="result__snippet"[^>]*>(.*?)</a>', resp.text, re.DOTALL)
                for snippet in snippets[:5]:
                     results_text += f"Сводка: {clean_html(snippet)}\n\n"
        except Exception as e:
            print(f"[WARN] Ошибка DuckDuckGo: {e}")

    return results_text


async def search_match_info(match_name: str, sport: str) -> str:
    """Комбинированный поиск: пробует Tavily, а при ошибке 403 автоматически включает Google + DuckDuckGo"""
    info = ""

    # Попытка 1: Tavily (если не заблокирован)
    if tavily_client:
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    tavily_client.search,
                    query=f"{match_name} {sport} прогноз статистика форма",
                    search_depth="basic",
                    max_results=5
                ),
                timeout=10.0
            )
            for result in response.get("results", []):
                info += f"Заголовок: {result.get('title', '')}\nСодержание: {result.get('content', '')}\n\n"
        except Exception as e:
            print(f"[INFO] ⚠️ Tavily недоступен ({e}), переключаюсь на прямой поиск Google/DuckDuckGo...")

    # Попытка 2: Прямой поиск Google + DuckDuckGo (на русском и английском)
    ru_info = await google_and_ddg_search(f"{match_name} {sport}", lang="ru")
    en_query = await build_english_query(match_name, sport)
    en_info = await google_and_ddg_search(f"{en_query} prediction stats", lang="en")

    info += ru_info + en_info
    print(f"[INFO] 📝 Собрано информации из сети: {len(info)} символов")
    return info if info.strip() else "Используй базовую спортивную статистику и исторические данные по этим командам/игрокам."


async def generate_analysis(match_name: str, search_info: str, sport: str) -> str:
    """Генерация аналитической сводки через Groq (Llama 3.3 70B)"""
    try:
        if sport == "ММА":
            section_name = "🥊 Бой"
            form_section = """📊 АНАЛИЗ БОЙЦОВ:
• Боец 1: [анализ формы, стиль, сильные стороны и рекорды]
• Боец 2: [анализ формы, стиль, сильные стороны и рекорды]"""
            injuries_section = """🤕 СОСТОЯНИЕ И ФАКТОРЫ:
• [габариты, выносливость, борьба/ударка, состояние перед боем]"""
        else:
            section_name = "🏆 Матч"
            form_section = """📊 АНАЛИЗ ФОРМЫ И СИЛЫ:
• Команда/Игрок 1: [текущая форма, стиль игры и результаты]
• Команда/Игрок 2: [текущая форма, стиль игры и результаты]"""
            injuries_section = """🤕 СОСТАВЫ И КАДРОВЫЕ ФАКТОРЫ:
• [ключевые игроки, лидеры, возможные замены или потери]"""

        prompt = f"""
Проанализируй спортивное событие "{match_name}" ({sport}) и создай подробную аналитическую сводку для ставок.
Опирайся на найденную информацию из интернета ниже, а если в ней мало деталей — обязательно дополни разбор своими глубокими знаниями об этих командах/игроках, их стиле игры, истории личных встреч и статистических трендах. Никогда не отказывайся от анализа!
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

📰 КЛЮЧЕВЫЕ ФАКТЫ И НОВОСТИ:
• [факт или статистика 1]
• [факт или статистика 2]
• [факт или статистика 3]

⚠️ ВАЖНЫЕ ТРЕНДЫ ДЛЯ СТАВОК:
• [статистический тренд 1]
• [статистический тренд 2]

🎯 ВЕРДИКТ ИИ-АНАЛИТИКА:
💭 Оценка шансов: [кто фаворит и за счет чего]
📈 Лучшие варианты для ставки: [1-2 наиболее логичных варианта: исход, тотал или фора с четким обоснованием]
══════════════════════════
"""

        response = await asyncio.to_thread(
            groq_client.chat.completions.create,
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": "Ты — профессиональный спортивный аналитик и каппер-эксперт. Ты знаешь всё о мировом спорте и киберспорте. Пиши четко, информативно, на русском языке. Не используй символы * или **."
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
        f"🔍 Анализирую событие: {match_name} ({selected_sport})...\n"
        f"⏳ Собираю статистику и новости (10-15 сек)."
    )

    try:
        search_info = await search_match_info(match_name, selected_sport)

        await status_message.edit_text("🤖 Данные собраны! ИИ формирует аналитический отчет...")
        analysis = await generate_analysis(match_name, search_info, selected_sport)

        await status_message.delete()
        await message.answer(analysis)

    except Exception as e:
        await status_message.edit_text(f"❌ Произошла ошибка: {str(e)}")


async def main():
    print("🚀 БОТ УСПЕШНО ЗАПУЩЕН (GOOGLE + DUCKDUCKGO ПОИСК АКТИВЕН)")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
