import asyncio
from datetime import datetime, timedelta, timezone
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


def ask_groq(system_msg: str, user_msg: str, max_tok: int = 1800) -> str:
    models_to_try = get_working_models()
    last_error = ""
    for model_name in models_to_try:
        try:
            response = groq_client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.4,
                max_tokens=max_tok,
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
    return f"Ошибка генерации: {last_error}"


async def parse_bookmaker_link(url: str) -> dict:
    """Извлекает название матча, вид спорта и данные со страницы БК по ссылке"""
    print(f"[INFO] 🔗 Разбор ссылки: {url}")
    page_data = ""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        ),
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    }

    parsed_url = urllib.parse.urlparse(url)
    url_words = urllib.parse.unquote(parsed_url.path).replace("-", " ").replace(
        "_", " "
    ).replace("/", " ")

    async with httpx.AsyncClient(
        headers=headers, follow_redirects=True, timeout=15.0
    ) as client:
        # 1. Прямой запрос к странице (читаем заголовки и мета-теги)
        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                text_html = resp.text
                titles = re.findall(
                    r"<title[^>]*>(.*?)</title>",
                    text_html,
                    re.IGNORECASE | re.DOTALL,
                )
                metas = re.findall(
                    r'<meta[^>]+content=["\'](.*?)["\'][^>]*>',
                    text_html,
                    re.IGNORECASE,
                )
                h1s = re.findall(
                    r"<h1[^>]*>(.*?)</h1>", text_html, re.IGNORECASE | re.DOTALL
                )
                extracted = " | ".join(titles[:2] + h1s[:2] + metas[:8])
                extracted = re.sub(r"<[^>]+>", " ", html.unescape(extracted))
                page_data += f"Мета-данные страницы: {extracted[:1500]}\n"
        except Exception as e:
            print(f"[WARN] Прямое чтение ссылки: {e}")

        # 2. Чтение через облачный рендер Jina (пробивает JS-сайты букмекеров)
        try:
            jina_url = f"https://r.jina.ai/{url}"
            j_resp = await client.get(jina_url, timeout=12.0)
            if j_resp.status_code == 200 and len(j_resp.text) > 50:
                clean_jina = j_resp.text[:2500]
                page_data += f"Текст со страницы БК:\n{clean_jina}\n"
        except Exception as e:
            print(f"[WARN] Jina рендер: {e}")

    prompt = (
        f"Пользователь прислал ссылку на матч с сайта букмекера:\n"
        f"Ссылка: {url}\n"
        f"Слова из адреса ссылки: {url_words}\n"
        f"{page_data}\n\n"
        "Определи, какой это матч/бой и какой вид спорта.\n"
        "Переведи названия команд или фамилии спортсменов на русский язык.\n"
        "Ответь СТРОГО в две строки (без лишнего текста):\n"
        "МАТЧ: [Команда 1 - Команда 2] (или UNKNOWN, если данных совсем нет)\n"
        "СПОРТ: [футбол / хоккей / баскетбол / теннис / волейбол / киберспорт / ММА]"
    )

    ai_resp = ask_groq(
        "Ты парсер спортивных ссылок. Отвечай строго по шаблону.",
        prompt,
        max_tok=200,
    )

    match_name = "UNKNOWN"
    sport = "спорт"

    for line in ai_resp.splitlines():
        if "МАТЧ:" in line.upper():
            match_name = line.split(":", 1)[1].strip().strip('"\'[]')
        elif "СПОРТ:" in line.upper():
            sport = line.split(":", 1)[1].strip().strip('"\'[]').lower()

    return {
        "match_name": match_name,
        "sport": sport,
        "page_data": page_data[:1500],
    }


async def fallback_free_search(match_name: str, sport: str) -> str:
    print(f"[INFO] Резервный поиск: {match_name} ({sport})")
    info = ""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    queries = [
        f"{match_name} {sport} дата время начала матча прогноз",
        f"{match_name} {sport} статистика форма обзор",
    ]

    async with httpx.AsyncClient(
        headers=headers, follow_redirects=True, timeout=15.0
    ) as client:
        for q in queries:
            if len(info) > 2000:
                break
            try:
                eq = urllib.parse.quote(q)
                url = f"https://news.google.com/rss/search?q={eq}&hl=ru&gl=RU&ceid=RU:ru"
                resp = await client.get(url)
                if resp.status_code == 200:
                    root = ET.fromstring(resp.text)
                    for item in root.findall(".//item")[:5]:
                        title = item.findtext("title", "")
                        pub = item.findtext("pubDate", "")
                        raw_d = html.unescape(item.findtext("description", ""))
                        desc = re.sub(r"<[^>]+>", "", raw_d)
                        info += f"Новость: {title} (Опубликовано: {pub})\n{desc}\n\n"
            except Exception as e:
                print(f"[WARN] Google RSS: {e}")

        try:
            eq = urllib.parse.quote(
                f"{sport} {match_name} дата время прогноз коэффициенты"
            )
            b_url = f"https://www.bing.com/search?q={eq}&format=rss"
            resp = await client.get(b_url)
            if resp.status_code == 200:
                root = ET.fromstring(resp.text)
                for item in root.findall(".//item")[:5]:
                    title = item.findtext("title", "")
                    desc = item.findtext("description", "")
                    info += f"Источник: {title}\nОписание: {desc}\n\n"
        except Exception as e:
            print(f"[WARN] Bing RSS: {e}")

    return info


async def search_match_info(
    match_name: str, sport: str, extra_info: str = ""
) -> str:
    info = extra_info + "\n\n" if extra_info else ""
    if tavily_client:
        try:
            q_str = (
                f"{sport} {match_name} дата время матча прогноз статистика форма"
            )
            resp = tavily_client.search(
                query=q_str, search_depth="basic", max_results=5
            )
            for r in resp.get("results", []):
                info += f"Заголовок: {r.get('title', '')}\n{r.get('content', '')}\n\n"
        except Exception as e:
            print(f"[WARN] Tavily ошибка: {e}")

    if len(info.strip()) < 200:
        info += await fallback_free_search(match_name, sport)

    return info if info.strip() else "Используй общую статистику команд."


async def generate_analysis(
    match_name: str, search_info: str, sport: str
) -> str:
    msk_tz = timezone(timedelta(hours=3))
    now_msk = datetime.now(msk_tz).strftime("%d.%m.%Y (%H:%M МСК)")

    if "мма" in sport.lower() or "бокс" in sport.lower():
        header_icon = "🥊 БОЙ"
        form_block = (
            "📊 АНАЛИЗ ФОРМЫ БОЙЦОВ:\n"
            "🔹 Боец 1: [актуальная форма, рекорд, сильные стороны]\n"
            "🔸 Боец 2: [актуальная форма, рекорд, сильные стороны]"
        )
        roster_block = "🤕 СОСТОЯНИЕ И ЛАГЕРЬ:\n• [весогонка, травмы, готовность]"
    else:
        header_icon = "🏆 МАТЧ"
        form_block = (
            "📊 АНАЛИЗ ФОРМЫ КОМАНД:\n"
            "🔹 Команда 1: [результаты последних игр, стиль, форма]\n"
            "🔸 Команда 2: [результаты последних игр, стиль, форма]"
        )
        roster_block = "🤕 СОСТАВЫ И ПОТЕРИ:\n• [травмы, дисквалификации, лидеры]"

    prompt = (
        f"Сегодняшняя дата: {now_msk}.\n"
        f"Проанализируй событие '{match_name}' ({sport}) и создай профессиональную сводку.\n\n"
        f"Найденная информация:\n{search_info}\n\n"
        "Обязательно укажи дату и время начала матча/боя (по МСК) на основе данных "
        "(если точное время неизвестно, укажи ориентировочную дату проведения).\n"
        "Оформи ответ СТРОГО по этой красивой структуре на русском языке:\n\n"
        "══════════════════════════\n"
        f"{header_icon}: {match_name}\n"
        "══════════════════════════\n"
        "📅 Дата и время: [Дата и время начала по МСК]\n"
        f"🏅 Вид спорта: {sport.capitalize()} (укажи турнир/лигу)\n\n"
        f"{form_block}\n\n"
        "⚔️ ЛИЧНЫЕ ВСТРЕЧИ (H2H):\n"
        "• [статистика очных игр или сравнение стилей]\n\n"
        f"{roster_block}\n\n"
        "📰 СВЕЖИЕ НОВОСТИ И КОНТЕКСТ:\n"
        "• [мотивация, турнирное положение, важные новости]\n\n"
        "⚠️ ВАЖНЫЕ ФАКТОРЫ:\n"
        "• [ключевой фактор 1]\n"
        "• [ключевой фактор 2]\n\n"
        "══════════════════════════\n"
        "🎯 ПРОГНОЗ ЭКСПЕРТА:\n"
        "✅ Основная ставка: [конкретный исход]\n"
        "🔥 Альтернатива / Тотал: [дополнительный вариант]\n"
        "💡 Обоснование: [краткий вывод в 2-3 предложениях]\n"
        "══════════════════════════"
    )

    sys_msg = (
        "Ты профессиональный спортивный аналитик и каппер. "
        "Пиши чётко, структурно, с иконками, без воды и без звездочек (**)."
    )
    result = ask_groq(sys_msg, prompt, max_tok=1800)
    return result.replace("**", "")


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    user_sports[user_id] = None
    text_msg = (
        "👋 Привет! Я ИИ-бот для спортивной аналитики и прогнозов.\n\n"
        "📌 Как мной пользоваться (2 способа):\n"
        "1️⃣ Выбери вид спорта кнопкой ниже и напиши матч (например: Северсталь - Сибирь).\n"
        "2️⃣ Или просто вставь прямую ССЫЛКУ на матч с сайта любого букмекера "
        "(Fonbet, BetBoom, Winline, Pari, 1xBet, Flashscore и др.) — я сам распознаю игру и сделаю разбор!"
    )
    await message.answer(text_msg, reply_markup=sport_keyboard)


@dp.message()
async def handle_message(message: types.Message):
    user_id = message.from_user.id
    text = (message.text or "").strip()

    sports = {
        "⚽ Футбол": ("футбол", "Арсенал - Челси"),
        "🏒 Хоккей": ("хоккей", "Северсталь - Сибирь"),
        "🏐 Волейбол": ("волейбол", "Зенит - Динамо"),
        "🎮 Киберспорт": ("киберспорт", "Team Spirit - Team Liquid"),
        "🏀 Баскетбол": ("баскетбол", "Лейкерс - Уорриорз"),
        "🎾 Теннис": ("теннис", "Джокович - Алькарас"),
        "🥊 ММА": ("ММА", "Махачев - Царукян"),
    }

    # 1. Если нажата кнопка вида спорта
    if text in sports:
        selected_sport, example = sports[text]
        user_sports[user_id] = selected_sport
        await message.answer(
            f"✅ Выбран вид спорта: {text}\n\n"
            f"✍️ Напиши название матча (например: {example})\n"
            f"🔗 Либо отправь ссылку на матч с сайта БК!"
        )
        return

    # 2. Если пользователь отправил ССЫЛКУ на матч с сайта БК
    url_match = re.search(r"https?://\S+", text)
    if url_match:
        url = url_match.group(0)
        status_msg = await message.answer(
            "🔗 Перехожу по ссылке букмекера и определяю матч..."
        )
        try:
            parsed = await parse_bookmaker_link(url)
            match_name = parsed["match_name"]
            detected_sport = parsed["sport"]
            extra_page_info = parsed["page_data"]

            if match_name == "UNKNOWN" or len(match_name) < 3:
                await status_msg.edit_text(
                    "⚠️ Сайт букмекера скрыл название команд за внутренней защитой.\n"
                    "Пожалуйста, напиши названия команд текстом (например: Команда 1 - Команда 2)!"
                )
                return

            await status_msg.edit_text(
                f"✅ Матч распознан: {match_name} ({detected_sport})\n"
                f"📰 Собираю статистику, дату и новости..."
            )
            search_info = await search_match_info(
                match_name, detected_sport, extra_page_info
            )
            await status_msg.edit_text("🤖 Формирую аналитическую сводку...")
            analysis = await generate_analysis(
                match_name, search_info, detected_sport
            )
            await status_msg.edit_text("✅ Готово!")
            await message.answer(analysis)
        except Exception as e:
            await status_msg.edit_text(f"❌ Ошибка при разборе ссылки: {str(e)}")
        return

    # 3. Если обычный текст, но вид спорта ещё не выбран
    if user_sports.get(user_id) is None:
        await message.answer(
            "⚠️ Сначала выбери вид спорта кнопками ниже (или просто пришли ссылку на матч с сайта БК)!",
            reply_markup=sport_keyboard,
        )
        return

    # 4. Стандартный поиск по названию матча
    match_name = text
    selected_sport = user_sports[user_id]

    status_msg = await message.answer(
        f"🔍 Ищу расписание и данные: {match_name} ({selected_sport})..."
    )

    try:
        search_info = await search_match_info(match_name, selected_sport)
        await status_msg.edit_text("🤖 Формирую аналитическую сводку...")
        analysis = await generate_analysis(
            match_name, search_info, selected_sport
        )
        await status_msg.edit_text("✅ Готово!")
        await message.answer(analysis)
    except Exception as e:
        await status_msg.edit_text(f"❌ Произошла ошибка: {str(e)}")


async def main():
    print("🚀 БОТ ЗАПУЩЕН И ГОТОВ К РАБОТЕ")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
