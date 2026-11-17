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
from groq import Groq

# Загружаем переменные окружения
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip().strip('"').strip("'")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip().strip('"').strip("'")

# Инициализация клиентов
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
groq_client = Groq(
    api_key=GROQ_API_KEY,
    http_client=httpx.Client()
)

# Хранилище выбранных видов спорта и рабочей модели ИИ
user_sports = {}
ACTIVE_GROQ_MODEL = None

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


async def call_groq_ai(messages: list, max_tokens: int = 1500) -> str:
    """Умный вызов Groq: сам находит рабочую модель на аккаунте пользователя"""
    global ACTIVE_GROQ_MODEL

    # Список приоритетных моделей (от самых умных к базовым)
    preferred_models = [
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
        "meta-llama/llama-4-maverick-17b-128e-instruct",
        "meta-llama/llama-4-scout-17b-16e-instruct",
        "qwen/qwen3-32b",
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
    ]

    # Получаем реальный список моделей, доступных на аккаунте
    try:
        models_list = await asyncio.to_thread(groq_client.models.list)
        available_ids = [
            m.id for m in models_list.data
            if not any(skip in m.id.lower() for skip in ["whisper", "tts", "guard", "embed", "playai", "orpheus"])
        ]
    except Exception:
        available_ids = []

    # Собираем очередь моделей для проверки
    candidates = []
    if ACTIVE_GROQ_MODEL:
        candidates.append(ACTIVE_GROQ_MODEL)
    for pm in preferred_models:
        if pm in available_ids and pm not in candidates:
            candidates.append(pm)
    for pm in preferred_models:
        if pm not in candidates:
            candidates.append(pm)
    for aid in available_ids:
        if aid not in candidates:
            candidates.append(aid)

    last_error = ""
    for model_id in candidates:
        try:
            resp = await asyncio.to_thread(
                groq_client.chat.completions.create,
                model=model_id,
                messages=messages,
                temperature=0.5,
                max_tokens=max_tokens
            )
            text = resp.choices[0].message.content or ""
            # Убираем скрытые теги размышлений <think>...</think>, если модель их выдает
            text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
            if text:
                ACTIVE_GROQ_MODEL = model_id
                print(f"[INFO] ✅ Использована модель Groq: {model_id}")
                return text
        except Exception as e:
            last_error = str(e)
            print(f"[WARN] Модель {model_id} не подошла: {last_error}")
            continue

    raise Exception(f"Не удалось вызвать модель Groq: {last_error}")


async def build_english_query(match_name: str, sport: str) -> str:
    """Переводит название матча на английский для мирового поиска"""
    try:
        res = await call_groq_ai(
            messages=[
                {
                    "role": "system",
                    "content": "Translate the sports match into a short English search query (max 6 words) with team names and sport. Output ONLY the search query, no quotes."
                },
                {"role": "user", "content": f"{sport}: {match_name}"}
            ],
            max_tokens=50
        )
        return res.strip()
    except Exception:
        return f"{match_name} {sport}"


async def google_and_ddg_search(query: str, lang: str = "ru") -> str:
    """Бесплатный и безотказный поиск через Google News RSS и DuckDuckGo"""
    results_text = ""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }

    async with httpx.AsyncClient(headers=headers, timeout=15.0, follow_redirects=True) as client:
        # 1. Поиск свежих новостей через Google News RSS
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
    """Двуязычный поиск информации о матче"""
    ru_info = await google_and_ddg_search(f"{match_name} {sport}", lang="ru")
    en_query = await build_english_query(match_name, sport)
    en_info = await google_and_ddg_search(f"{en_query} prediction stats", lang="en")

    info = ru_info + en_info
    print(f"[INFO] 📝 Собрано информации из сети: {len(info)} символов")
    return info if info.strip() else "Используй базовую спортивную статистику и исторические данные по этим командам/игрокам."


async def generate_analysis(match_name: str, search_info: str, sport: str) -> str:
    """Генерация аналитической сводки через Groq"""
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

        result = await call_groq_ai(
            messages=[
                {
                    "role": "system",
                    "content": "Ты — профессиональный спортивный аналитик и каппер-эксперт. Ты знаешь всё о мировом спорте и киберспорте. Пиши четко, информативно, на русском языке. Не используй символы * или **."
                },
                {"role": "user", "content": prompt}
            ],
            max_tokens=1500
        )
        return result

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
    print("🚀 БОТ УСПЕШНО ЗАПУЩЕН (АВТОВЫБОР МОДЕЛИ GROQ ВКЛЮЧЕН)")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
