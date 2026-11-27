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

load_dotenv()

bot = Bot(token=(os.getenv("BOT_TOKEN") or "").strip())
dp = Dispatcher()

tavily_key = (os.getenv("TAVILY_API_KEY") or "").strip()
tavily_client = TavilyClient(api_key=tavily_key) if tavily_key else None

groq_client = Groq(
    api_key=(os.getenv("GROQ_API_KEY") or "").strip(),
    http_client=httpx.Client(),
)

user_sports = {}

sport_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="⚽ Футбол"), KeyboardButton(text="🏒 Хоккей")],
        [
            KeyboardButton(text="🏐 Волейбол"),
            KeyboardButton(text="🎮 Киберспорт"),
        ],
        [KeyboardButton(text="🏀 Баскетбол"), KeyboardButton(text="🎾 Теннис")],
        [KeyboardButton(text="🥊 ММА")],
    ],
    resize_keyboard=True,
    one_time_keyboard=False,
)


async def fallback_free_search(match_name: str, sport: str) -> str:
    print(f"[INFO] Резервный поиск: {match_name} ({sport})")
    info = ""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    queries = [f"{match_name} {sport} прогноз", f"{match_name} {sport}"]

    async with httpx.AsyncClient(
        headers=headers, follow_redirects=True, timeout=15.0
    ) as client:
        for q in queries:
            if len(info) > 1500:
                break
            try:
                eq = urllib.parse.quote(q)
                url = f"https://news.google.com/rss/search?q={eq}&hl=ru&gl=RU&ceid=RU:ru"
                resp = await client.get(url)
                if resp.status_code == 200:
                    root = ET.fromstring(resp.text)
                    for item in root.findall(".//item")[:6]:
                        title = item.findtext("title", "")
                        pub = item.findtext("pubDate", "")
                        raw_d = html.unescape(item.findtext("description", ""))
                        desc = re.sub(r"<[^>]+>", "", raw_d)
                        info += f"Новость: {title} ({pub})\n{desc}\n\n"
            except Exception as e:
                print(f"[WARN] Google RSS: {e}")

        try:
            eq = urllib.parse.quote(f"{sport} {match_name} прогноз статистика")
            b_url = f"https://www.bing.com/search?q={eq}&format=rss"
            resp = await client.get(b_url)
            if resp.status_code == 200:
                root = ET.fromstring(resp.text)
                for item in root.findall(".//item")[:5]:
                    title = item.findtext("title", "")
                    desc = item.findtext("description", "")
                    info += f"Заголовок: {title}\nОписание: {desc}\n\n"
        except Exception as e:
            print(f"[WARN] Bing RSS: {e}")

    return info


async def search_match_info(match_name: str, sport: str) -> str:
    info = ""
    if tavily_client:
        try:
            q_str = f"{sport} {match_name} прогноз статистика форма"
            resp = tavily_client.search(
                query=q_str, search_depth="basic", max_results=5
            )
            for r in resp.get("results", []):
                info += f"Заголовок: {r.get('title', '')}\n{r.get('content', '')}\n\n"
        except Exception as e:
            print(f"[WARN] Tavily ошибка: {e}")

    if len(info.strip()) < 100:
        info = await fallback_free_search(match_name, sport)

    return info if info.strip() else "Используй общую статистику команд."


def get_working_models() -> list:
    preferred = [
        "openai/gpt-oss-20b",
        "openai/gpt-oss-120b",
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "qwen/qwen3-32b",
    ]
    try:
        models_data = groq_client.models.list()
        server_models = []
        skip_words = [
            "whisper",
            "guard",
            "tts",
            "orpheus",
            "playai",
            "embed",
            "allam",
        ]
        for m in models_data.data:
            m_id = m.id
            if not any(w in m_id.lower() for w in skip_words):
                server_models.append(m_id)

        final_list = [m for m in preferred if m in server_models]
        for m in server_models:
            if m not in final_list:
                final_list.append(m)
        return final_list if final_list else preferred
    except Exception as e:
        print(f"[WARN] Список моделей: {e}")
        return preferred


async def generate_analysis(
    match_name: str, search_info: str, sport: str
) -> str:
    prompt = (
        f"Проанализируй матч/бой '{match_name}' ({sport}) для ставок.\n"
        f"Информация из интернета:\n{search_info}\n\n"
        "Дополни анализ своими знаниями об этих командах/игроках.\n"
        "Напиши структурированный ответ на русском языке:\n"
        f"🏆 Матч/Бой: {match_name} ({sport})\n\n"
        "📊 Анализ формы соперников:\n"
        "- Соперник 1: [анализ]\n"
        "- Соперник 2: [анализ]\n\n"
        "🤕 Составы и потери:\n"
        "- [ключевая информация]\n\n"
        "📰 Важные факты:\n"
        "- [факты и новости]\n\n"
        "🎯 Прогноз и варианты ставок:\n"
        "- [прогноз эксперта]\n"
    )

    models_to_try = get_working_models()
    last_error = ""

    for model_name in models_to_try:
        try:
            print(f"[INFO] Запрос в Groq: {model_name}")
            sys_msg = "Ты спортивный аналитик. Пиши кратко, по делу, на русском языке, с эмодзи."
            response = groq_client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": sys_msg},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.5,
                max_tokens=1800,
            )
            result = response.choices[0].message.content or ""
            result = re.sub(
                r"<think>.*?</think>", "", result, flags=re.DOTALL
            ).strip()
            if result:
                return result
        except Exception as e:
            last_error = str(e)
            print(f"[WARN] Модель {model_name} не сработала: {last_error}")

    return f"Ошибка при генерации анализа: {last_error}"


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    user_sports[user_id] = None
    text_msg = (
        "👋 Привет! Я бот для аналитики спортивных матчей и боёв.\n\n"
        "📌 Сначала выбери вид спорта кнопкой ниже, "
        "затем отправь название матча (например: Арсенал - Челси)."
    )
    await message.answer(text_msg, reply_markup=sport_keyboard)


@dp.message()
async def handle_message(message: types.Message):
    user_id = message.from_user.id
    text = message.text or ""

    sports = {
        "⚽ Футбол": ("футбол", "Арсенал - Челси"),
        "🏒 Хоккей": ("хоккей", "ЦСКА - Ак Барс"),
        "🏐 Волейбол": ("волейбол", "Зенит - Динамо"),
        "🎮 Киберспорт": ("киберспорт", "Spirit - Liquid"),
        "🏀 Баскетбол": ("баскетбол", "Лейкерс - Уорриорз"),
        "🎾 Теннис": ("теннис", "Джокович - Алькарас"),
        "🥊 ММА": ("ММА", "Макгрегор - Чендлер"),
    }

    if text in sports:
        selected_sport, example = sports[text]
        user_sports[user_id] = selected_sport
        await message.answer(
            f"✅ Выбран вид спорта: {text}\n"
            f"📌 Отправь название матча (например: {example})"
        )
        return

    if user_sports.get(user_id) is None:
        await message.answer(
            "⚠️ Сначала выбери вид спорта кнопками ниже!",
            reply_markup=sport_keyboard,
        )
        return

    match_name = text
    selected_sport = user_sports[user_id]

    status_msg = await message.answer(
        f"🔍 Ищу данные: {match_name} ({selected_sport})..."
    )

    try:
        search_info = await search_match_info(match_name, selected_sport)
        await status_msg.edit_text("🤖 Генерация аналитической сводки...")
        analysis = await generate_analysis(
            match_name, search_info, selected_sport
        )
        await status_msg.edit_text("✅ Готово!")
        await message.answer(analysis)
    except Exception as e:
        await status_msg.edit_text(f"❌ Произошла ошибка: {str(e)}")


async def main():
    print("🚀 БОТ ЗАПУЩЕН")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
