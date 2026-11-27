import os
import asyncio
from datetime import datetime, timedelta
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from tavily import TavilyClient
from groq import Groq

# Загружаем переменные окружения
load_dotenv()

# Инициализация клиентов
bot = Bot(token=os.getenv("BOT_TOKEN"))
dp = Dispatcher()
tavily_client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

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
    one_time_keyboard=False
)


async def search_match_info(match_name: str, sport: str) -> str:
    """Поиск информации о матче через Tavily за последние 3 дня"""
    try:
        print(f"[INFO] 🔍 Поиск информации о матче: {match_name} ({sport})")

        # Вычисляем даты для поиска (последние 3 дня)
        end_date = datetime.now()
        start_date = end_date - timedelta(days=3)

        print(f"[INFO] 📡 Отправка запроса в Tavily API...")

        # Добавляем таймаут с asyncio.wait_for
        response = await asyncio.wait_for(
            asyncio.to_thread(
                tavily_client.search,
                query=f"{sport} {match_name} новости травмы форма команды прогнозы экспертов ставки",
                search_depth="basic",  # Поменяли на basic для скорости
                max_results=5,  # Уменьшили с 10 до 5
                include_raw_content=False,  # Убрали raw content для скорости
                topic="news"
            ),
            timeout=30.0  # Таймаут 30 секунд
        )

        results_count = len(response.get("results", []))
        print(f"[INFO] ✅ Получено {results_count} результатов от Tavily")

        # Собираем найденную информацию
        info = ""
        for result in response.get("results", []):
            info += f"Заголовок: {result.get('title', '')}\n"
            info += f"Содержание: {result.get('content', '')}\n"
            info += f"URL: {result.get('url', '')}\n\n"

        print(f"[INFO] 📝 Собрано информации: {len(info)} символов")
        return info if info else "Информация не найдена"
    except asyncio.TimeoutError:
        print(f"[ERROR] ❌ Таймаут Tavily API (более 30 секунд)")
        return "Ошибка при поиске: Превышено время ожидания ответа от поисковика. Попробуйте позже."
    except Exception as e:
        print(f"[ERROR] ❌ Ошибка при поиске: {str(e)}")
        return f"Ошибка при поиске: {str(e)}"


async def generate_analysis(match_name: str, search_info: str, sport: str) -> str:
    """Генерация аналитической сводки через Groq"""
    try:
        print(f"[INFO] 🤖 Генерация анализа через Groq API...")

        # Адаптивный промпт для ММА (бойцы) и командных видов спорта
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

🎯 Прогноз:
[твой прогноз на основе данных]

⚠️ Важные факторы:
• [ключевые факторы]

Важно: Не повторяй один и тот же текст. Пиши кратко и по делу.
"""

        print(f"[INFO] 📤 Отправка запроса в Groq (модель: openai/gpt-oss-20b)...")

        response = groq_client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[
                {"role": "system", "content": "Ты - спортивный аналитик. Пиши кратко, без повторов, на русском языке. Не зацикливайся на одних и тех же фразах."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.5,  # Снизил чтобы уменьшить зацикливание
            max_tokens=1000  # Уменьшил для краткости
        )

        result = response.choices[0].message.content
        print(f"[INFO] ✅ Анализ сгенерирован: {len(result)} символов")

        # Проверка на зацикливание
        if len(result) > 2000:
            print(f"[WARNING] ⚠️ Ответ слишком длинный, возможно зацикливание")
            result = result[:2000] + "\n\n[ответ обрезан из-за длины]"

        return result
    except Exception as e:
        print(f"[ERROR] ❌ Ошибка при генерации анализа: {str(e)}")
        return f"Ошибка при генерации анализа: {str(e)}"


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Обработчик команды /start"""
    user_id = message.from_user.id
    user_sports[user_id] = None  # Сбрасываем выбор вида спорта

    await message.answer(
        "👋 Привет! Я бот для аналитики спортивных матчей и боёв.\n\n"
        "📌 Сначала выбери вид спорта с помощью кнопок ниже, "
        "затем отправь название матча или боя (например: \"Арсенал - Челси\" или \"Конор Макгрегор - Хабиб Нурмагомедов\").\n\n"
        "⚡ Я проанализирую свежие новости, травмы, форму и сделаю прогноз для ставок!",
        reply_markup=sport_keyboard
    )

    print(f"[INFO] 👤 Пользователь {user_id} запустил бота")


@dp.message()
async def handle_message(message: types.Message):
    """Обработчик всех сообщений"""
    user_id = message.from_user.id
    text = message.text

    print(f"[DEBUG] 📨 Получено сообщение от {user_id}: '{text}' (длина: {len(text)})")
    print(f"[DEBUG] Текущий выбранный спорт для пользователя: {user_sports.get(user_id)}")

    # Список видов спорта из кнопок с примерами
    sports = {
        "⚽ Футбол": ("футбол", "Арсенал - Челси"),
        "🏒 Хоккей": ("хоккей", "ЦСКА - Ак Барс"),
        "🏐 Волейбол": ("волейбол", "Зенит - Динамо"),
        "🎮 Киберспорт": ("киберспорт", "Team Spirit - Team Liquid"),
        "🏀 Баскетбол": ("баскетбол", "Лейкерс - Уорриорз"),
        "🎾 Теннис": ("теннис", "Джокович - Алькарас"),
        "🥊 ММА": ("ММА", "Конор Макгрегор - Хабиб Нурмагомедов"),
    }

    # Если пользователь выбрал вид спорта
    if text in sports:
        selected_sport, example = sports[text]
        user_sports[user_id] = selected_sport
        print(f"[INFO] 🎯 Пользователь {user_id} выбрал вид спорта: {selected_sport}")
        await message.answer(
            f"✅ Выбран вид спорта: {text}\n\n"
            f"📌 Теперь отправь название матча/боя (например: \"{example}\")"
        )
        return

    # Если вид спорта не выбран
    if user_sports.get(user_id) is None:
        print(f"[INFO] ⚠️ Вид спорта не выбран, просим выбрать")
        await message.answer(
            "⚠️ Сначала выбери вид спорта с помощью кнопок ниже!",
            reply_markup=sport_keyboard
        )
        return

    # Если вид спорта выбран, обрабатываем название матча
    match_name = text
    selected_sport = user_sports[user_id]

    print(f"[INFO] 📨 Получен запрос: {selected_sport} - {match_name}")

    # Отправляем сообщение о начале поиска
    status_message = await message.answer(
        f"🔍 Ищу информацию о матче: {match_name} ({selected_sport})...\n"
        f"⏳ Это может занять 15-30 секунд."
    )

    try:
        # Шаг 1: Поиск информации через Tavily
        print(f"[INFO] 📰 Шаг 1/3: Поиск новостей и информации...")
        await status_message.edit_text("📰 Поиск новостей и информации...")
        search_info = await search_match_info(match_name, selected_sport)

        if "Информация не найдена" in search_info or len(search_info) < 100:
            print(f"[INFO] ⚠️ Информация не найдена")
            await status_message.edit_text(
                "😔 К сожалению, не удалось найти достаточно информации об этом матче.\n"
                "Попробуй:\n"
                "• Проверить правильность названия команды\n"
                "• Указать более точное название матча\n"
                "• Попробовать другой матч"
            )
            return

        # Шаг 2: Генерация анализа через Groq
        print(f"[INFO] 🤖 Шаг 2/3: Генерация аналитической сводки...")
        await status_message.edit_text("🤖 Генерация аналитической сводки...")
        analysis = await generate_analysis(match_name, search_info, selected_sport)

        # Проверяем, что анализ не пустой
        if not analysis or len(analysis.strip()) == 0:
            print(f"[INFO] ❌ Анализ пустой")
            await status_message.edit_text("❌ Не удалось сгенерировать анализ. Попробуйте другой запрос.")
            return

        # Шаг 3: Отправка результата
        print(f"[INFO] 📤 Шаг 3/3: Отправка результата пользователю...")
        await status_message.edit_text("✅ Готово!")
        await message.answer(analysis)

        print(f"[INFO] ✅ Успешно! Ответ отправлен пользователю {user_id}")

    except Exception as e:
        print(f"[ERROR] ❌ Ошибка в обработчике: {str(e)}")
        await status_message.edit_text(f"❌ Произошла ошибка: {str(e)}\n\nПопробуй позже.")


async def main():
    """Запуск бота"""
    print("=" * 60)
    print("🚀 ЗАПУСК БОТА ДЛЯ АНАЛИТИКИ СПОРТИВНЫХ МАТЧЕЙ")
    print("=" * 60)

    # Показываем список моделей Groq
    print("\n📋 Получение списка моделей Groq...")
    try:
        models = groq_client.models.list()
        print(f"\n✅ Доступные модели в Groq (всего {len(models.data)}):")
        print("-" * 60)
        for model in models.data:
            print(f"  • {model.id}")
        print("-" * 60)
    except Exception as e:
        print(f"\n❌ Не удалось получить список моделей: {e}")

    print("\n🤖 Бот готов к работе! Ожидаю сообщения...\n")
    print("=" * 60)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
