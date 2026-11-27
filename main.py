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
                temperature=0.3,
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


async def quick_rss_search(query_list: list) -> str:
    """Быстрый поиск в Google News и Bing по списку запросов"""
    info = ""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    async with httpx.AsyncClient(
        headers=headers, follow_redirects=True, timeout=12.0
    ) as client:
        for q in query_list:
            if len(info) > 2200:
                break
            try:
                eq = urllib.parse.quote(q)
                g_url = (
                    f"https://news.google.com/rss/search"
                    f"?q={eq}&hl=ru&gl=RU&ceid=RU:ru"
                )
                resp = await client.get(g_url)
                if resp.status_code == 200:
                    root = ET.fromstring(resp.text)
                    for item in root.findall(".//item")[:4]:
                        title = item.findtext("title", "")
                        pub = item.findtext("pubDate", "")
                        raw_d = html.unescape(item.findtext("description", ""))
                        desc = re.sub(r"<[^>]+>", "", raw_d)
                        info += f"Новость: {title} ({pub})\n{desc}\n\n"
            except Exception as e:
                print(f"[WARN] Google RSS: {e}")

            try:
                eq = urllib.parse.quote(q)
                b_url = f"https://www.bing.com/search?q={eq}&format=rss"
                resp = await client.get(b_url)
                if resp.status_code == 200:
                    root = ET.fromstring(resp.text)
                    for item in root.findall(".//item")[:4]:
                        title = item.findtext("title", "")
                        desc = item.findtext("description", "")
                        info += f"Сайт: {title}\nОписание: {desc}\n\n"
            except Exception as e:
                print(f"[WARN] Bing RSS: {e}")

    return info


async def detect_real_sport_and_teams(
    raw_input: str, hinted_sport: str
) -> dict:
    """Автоматически определяет реальный вид спорта, турнир и команды"""
    # Сначала ищем сам матч БЕЗ привязки к старой кнопке спорта
    initial_info = await quick_rss_search(
        [
            f"{raw_input} матч прогноз",
            f"{raw_input} match prediction stats",
        ]
    )

    hint_text = (
        f"Подсказка кнопки от пользователя: {hinted_sport}"
        if hinted_sport and hinted_sport != "авто"
        else "Кнопка спорта не выбрана"
    )

    prompt = (
        f"Пользователь ввел запрос на матч: '{raw_input}'\n"
        f"{hint_text}\n\n"
        f"Результаты поиска в интернете по этому запросу:\n"
        f"{initial_info[:1800]}\n\n"
        "Твоя задача:\n"
        "1. Раздели соперников тире в формате: Команда 1 - Команда 2.\n"
        "2. Определи РЕАЛЬНЫЙ вид спорта этих команд/игроков! "
        "ВАЖНО: Если пользователь раньше нажал 'хоккей', но ввел "
        "'1W Team Eternal Fire', 'Team Spirit', 'NaVi' и т.д. — "
        "игнорируй старую кнопку и укажи реальный спорт: "
        "'киберспорт (CS2)' или 'киберспорт (Dota 2)'!\n"
        "Ответь СТРОГО в две строки:\n"
        "МАТЧ: [Соперник 1 - Соперник 2]\n"
        "СПОРТ: [футбол / хоккей / баскетбол / теннис / волейбол / "
        "киберспорт (CS2) / киберспорт (Dota 2) / ММА]"
    )

    ai_ans = ask_groq(
        "Ты эксперт по всем видам спорта и киберспорту (HLTV, Dota, спорт).",
        prompt,
        max_tok=150,
    )

    match_formatted = raw_input
    detected_sport = (
        hinted_sport if hinted_sport and hinted_sport != "авто" else "спорт"
    )

    for line in ai_ans.splitlines():
        if "МАТЧ:" in line.upper():
            val = line.split(":", 1)[1].strip().strip('"\'[]')
            if len(val) > 2:
                match_formatted = val
        elif "СПОРТ:" in line.upper():
            val = line.split(":", 1)[1].strip().strip('"\'[]')
            if len(val) > 2:
                detected_sport = val

    return {
        "match_name": match_formatted,
        "sport": detected_sport,
        "initial_info": initial_info,
    }


async def parse_bookmaker_link(url: str) -> dict:
    print(f"[INFO] 🔗 Разбор ссылки: {url}")
    page_data = ""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        ),
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    }

    parsed_url = urllib.parse.urlparse(url)
    full_slug = f"{parsed_url.path} {parsed_url.query} {parsed_url.fragment}"
    url_words = urllib.parse.unquote(full_slug)
    url_words = url_words.replace("-", " ").replace("_", " ").replace("/", " ")

    async with httpx.AsyncClient(
        headers=headers, follow_redirects=True, timeout=14.0
    ) as client:
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
                page_data += f"Мета-данные: {extracted[:1200]}\n"
        except Exception as e:
            print(f"[WARN] Прямое чтение ссылки: {e}")

        try:
            jina_url = f"https://r.jina.ai/{url}"
            j_resp = await client.get(jina_url, timeout=12.0)
            if j_resp.status_code == 200 and len(j_resp.text) > 50:
                page_data += f"Текст страницы:\n{j_resp.text[:2000]}\n"
        except Exception as e:
            print(f"[WARN] Jina рендер: {e}")

    prompt = (
        f"Ссылка на матч: {url}\n"
        f"Слова из URL: {url_words}\n"
        f"{page_data}\n\n"
        "Найди названия двух команд или игроков в этой ссылке или тексте.\n"
        "ВАЖНО: Если в ссылке только цифры (например 12345/67890) и в тексте "
        "нет конкретных названий команд, напиши МАТЧ: UNKNOWN.\n"
        "Ответь СТРОГО в две строки:\n"
        "МАТЧ: [Команда 1 - Команда 2] (или UNKNOWN)\n"
        "СПОРТ: [вид спорта]"
    )

    ai_resp = ask_groq(
        "Ты парсер спортивных ссылок. Не выдумывай команды, если они скрыты.",
        prompt,
        max_tok=150,
    )

    match_name = "UNKNOWN"
    sport = "авто"

    for line in ai_resp.splitlines():
        if "МАТЧ:" in line.upper():
            match_name = line.split(":", 1)[1].strip().strip('"\'[]')
        elif "СПОРТ:" in line.upper():
            sport = line.split(":", 1)[1].strip().strip('"\'[]').lower()

    return {
        "match_name": match_name,
        "sport": sport,
        "page_data": page_data[:1200],
    }


async def search_match_info(
    match_name: str, sport: str, extra_info: str = ""
) -> str:
    info = extra_info + "\n\n" if extra_info else ""
    if tavily_client:
        try:
            q_str = f"{match_name} {sport} прогноз статистика форма"
            resp = tavily_client.search(
                query=q_str, search_depth="basic", max_results=5
            )
            for r in resp.get("results", []):
                info += (
                    f"Заголовок: {r.get('title', '')}\n"
                    f"{r.get('content', '')}\n\n"
                )
        except Exception as e:
            print(f"[WARN] Tavily: {e}")

    more_info = await quick_rss_search(
        [
            f"{match_name} {sport} прогноз статистика",
            f"{match_name} h2h расписание дата",
        ]
    )
    info += more_info
    return info if info.strip() else "Используй актуальную статистику команд."


async def generate_analysis(
    match_name: str, search_info: str, sport: str
) -> str:
    msk_tz = timezone(timedelta(hours=3))
    now_msk = datetime.now(msk_tz).strftime("%d.%m.%Y (%H:%M МСК)")
    sp_low = sport.lower()

    if any(k in sp_low for k in ["кибер", "cs", "dota", "valorant", "esport"]):
        header_icon = "🎮 КИБЕРСПОРТ"
        form_block = (
            "📊 ИГРОВАЯ ФОРМА И ВИНРЕЙТ:\n"
            "🔹 Команда 1: [результаты последних матчей, винрейт, стрельба]\n"
            "🔸 Команда 2: [результаты последних матчей, винрейт, стрельба]"
        )
        roster_block = (
            "🗺 МАППУЛ / ДРАФТ И СОСТАВЫ:\n"
            "• [лучшие карты/герои, замены в составе (stand-in), "
            "никаких вратарей и физических травм!]"
        )
    elif any(k in sp_low for k in ["мма", "бокс", "ufc"]):
        header_icon = "🥊 БОЙ"
        form_block = (
            "📊 АНАЛИЗ ФОРМЫ БОЙЦОВ:\n"
            "🔹 Боец 1: [рекорд, серия, сильные стороны: ударка/борьба]\n"
            "🔸 Боец 2: [рекорд, серия, сильные стороны: ударка/борьба]"
        )
        roster_block = (
            "⚖️ ГАБАРИТЫ И КОНДИЦИИ:\n"
            "• [весогонка, выносливость (кардио), размах рук]"
        )
    elif "теннис" in sp_low:
        header_icon = "🎾 ТЕННИС"
        form_block = (
            "📊 ФОРМА ИГРОКОВ И ПОКРЫТИЕ:\n"
            "🔹 Игрок 1: [форма, статистика на покрытии, подача/приём]\n"
            "🔸 Игрок 2: [форма, статистика на покрытии, подача/приём]"
        )
        roster_block = (
            "💪 ФИЗИЧЕСКАЯ ГОТОВНОСТЬ:\n"
            "• [тонус, усталость после прошлых кругов]"
        )
    else:
        header_icon = "🏆 МАТЧ"
        form_block = (
            "📊 АНАЛИЗ ФОРМЫ КОМАНД:\n"
            "🔹 Команда 1: [результаты последних игр, атака/оборона]\n"
            "🔸 Команда 2: [результаты последних игр, атака/оборона]"
        )
        roster_block = (
            "🤕 СОСТАВЫ И КАДРОВЫЕ ПОТЕРИ:\n"
            "• [ключевые игроки, травмы или ротация состава]"
        )

    prompt = (
        f"Текущая дата и время: {now_msk}.\n"
        f"Событие: '{match_name}' | Вид спорта: {sport}.\n\n"
        f"Найденная информация:\n{search_info}\n\n"
        "ВАЖНЫЕ ПРАВИЛА:\n"
        "1. Строго соблюдай вид спорта! Если это Киберспорт — пиши про карты, "
        "раунды и стрельбу, НЕ пиши про вратарей и травмы.\n"
        "2. Дата и время: укажи реальную дату из новостей. Если точное время "
        f"в новостях не указано, напиши: 'Ближайший тур ({now_msk[:10]}) — "
        "время по линии БК'. НЕ ВЫДУМЫВАЙ случайные даты в будущем!\n\n"
        "Оформи ответ СТРОГО по шаблону:\n"
        "══════════════════════════\n"
        f"{header_icon}: {match_name}\n"
        "══════════════════════════\n"
        "📅 Дата и время: [Дата и время по МСК]\n"
        f"🏅 Дисциплина и турнир: {sport.capitalize()} [укажи турнир]\n\n"
        f"{form_block}\n\n"
        "⚔️ ЛИЧНЫЕ ВСТРЕЧИ (H2H):\n"
        "• [история очных встреч или сравнение стилей]\n\n"
        f"{roster_block}\n\n"
        "📰 КОНТЕКСТ И МОТИВАЦИЯ:\n"
        "• [турнирное положение и важные факты]\n\n"
        "⚠️ КЛЮЧЕВЫЕ ФАКТОРЫ:\n"
        "• [фактор 1]\n"
        "• [фактор 2]\n\n"
        "══════════════════════════\n"
        "🎯 ПРОГНОЗ ЭКСПЕРТА:\n"
        "✅ Основная ставка: [конкретный исход]\n"
        "🔥 Альтернатива / Тотал / Фора: [дополнительный вариант]\n"
        "💡 Обоснование: [четкий вывод в 2-3 предложениях]\n"
        "══════════════════════════"
    )

    sys_msg = (
        "Ты профессиональный спортивный и киберспортивный аналитик. "
        "Пиши чётко, с иконками, без воды и без звездочек (**)."
    )
    result = ask_groq(sys_msg, prompt, max_tok=1800)
    return result.replace("**", "")


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    user_sports[user_id] = "авто"
    welcome_text = (
        "👋 Привет! Я ИИ-бот для спортивной аналитики и прогнозов.\n"
        "══════════════════════════\n"
        "🚀 КАК МНОЙ ПОЛЬЗОВАТЬСЯ (2 СПОСОБА):\n\n"
        "1️⃣ ПО НАЗВАНИЮ МАТЧА:\n"
        "• Напиши команды или бойцов (например: 1W Team - Eternal Fire "
        "или Северсталь - Сибирь).\n"
        "• Я сам распознаю вид спорта или ты можешь выбрать его кнопкой 👇\n\n"
        "2️⃣ ПО ССЫЛКЕ С САЙТА БУКМЕКЕРА 🔗:\n"
        "• Скопируй и отправь мне прямую ссылку на матч с сайта БК "
        "(Fonbet, BetBoom, Winline, Pari, 1xBet, HLTV, Flashscore и др.)!\n"
        "• Я перейду по ссылке, определю игру и выдам полный разбор.\n"
        "══════════════════════════\n"
        "📊 Что входит в мой анализ:\n"
        "✅ Дата и время матча (по МСК)\n"
        "✅ Разбор формы, маппула/составов и очных встреч (H2H)\n"
        "✅ Конкретный прогноз: основная ставка + тотал/фора\n\n"
        "👇 Напиши матч, выбери кнопку спорта или пришли ссылку!"
    )
    await message.answer(welcome_text, reply_markup=sport_keyboard)


@dp.message()
async def handle_message(message: types.Message):
    user_id = message.from_user.id
    text = (message.text or "").strip()

    sports = {
        "⚽ Футбол": ("футбол", "Арсенал - Челси"),
        "🏒 Хоккей": ("хоккей", "Северсталь - Сибирь"),
        "🏐 Волейбол": ("волейбол", "Зенит - Динамо"),
        "🎮 Киберспорт": ("киберспорт", "1W Team - Eternal Fire"),
        "🏀 Баскетбол": ("баскетбол", "Лейкерс - Уорриорз"),
        "🎾 Теннис": ("теннис", "Джокович - Алькарас"),
        "🥊 ММА": ("ММА", "Махачев - Царукян"),
    }

    if text in sports:
        selected_sport, example = sports[text]
        user_sports[user_id] = selected_sport
        await message.answer(
            f"✅ Выбран вид спорта: {text}\n\n"
            f"✍️ Напиши название матча (например: {example})\n"
            "🔗 Либо просто отправь ссылку на матч с сайта БК!"
        )
        return

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
                # Сбрасываем старый спорт, чтобы следующее сообщение
                # определилось автоматически!
                user_sports[user_id] = "авто"
                await status_msg.edit_text(
                    "🔒 Этот букмекер шифрует названия команд внутри ссылки.\n\n"
                    "✍️ Просто напиши названия команд или игроков в ответ "
                    "(например: 1W Team - Eternal Fire) — я сам определю "
                    "вид спорта и сделаю полный разбор!"
                )
                return

            await status_msg.edit_text(
                f"✅ Матч распознан: {match_name} ({detected_sport})\n"
                "📰 Собираю статистику и новости..."
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
            await status_msg.edit_text(
                f"❌ Ошибка при разборе ссылки: {str(e)}"
            )
        return

    # Умная обработка текстового названия матча (авто-проверка вида спорта)
    hinted_sport = user_sports.get(user_id, "авто")
    status_msg = await message.answer(
        f"🔍 Определяю дисциплину и ищу данные по матчу: {text}..."
    )

    try:
        detected = await detect_real_sport_and_teams(text, hinted_sport)
        real_match = detected["match_name"]
        real_sport = detected["sport"]
        init_info = detected["initial_info"]

        await status_msg.edit_text(
            f"✅ Матч: {real_match}\n"
            f"🏅 Дисциплина: {real_sport.capitalize()}\n"
            "📰 Собираю свежую статистику и форму..."
        )

        search_info = await search_match_info(real_match, real_sport, init_info)
        await status_msg.edit_text("🤖 Формирую аналитическую сводку...")
        analysis = await generate_analysis(real_match, search_info, real_sport)

        await status_msg.edit_text("✅ Готово!")
        await message.answer(analysis)
    except Exception as e:
        await status_msg.edit_text(f"❌ Произошла ошибка: {str(e)}")


async def main():
    print("🚀 БОТ ЗАПУЩЕН И ГОТОВ К РАБОТЕ")
    try:
        await bot.set_my_description(
            "🤖 ИИ-бот для аналитики спортивных и киберспортивных матчей!\n\n"
            "✅ Умное определение вида спорта по названию команд\n"
            "🔗 Анализ по прямой ССЫЛКЕ с сайта букмекерской конторы\n"
            "📅 Дата, время, форма, маппул/составы и точный прогноз!\n\n"
            "👇 Нажми «Начать», чтобы получить прогноз!"
        )
    except Exception as e:
        print(f"[WARN] Описание профиля: {e}")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
