import os
import sqlite3
import json
import logging
import requests
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

# ==================== ЗАГРУЗКА ПЕРЕМЕННЫХ ====================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")

if not TELEGRAM_TOKEN or not DEEPSEEK_API_KEY:
    raise ValueError("❌ Не найдены TELEGRAM_TOKEN или DEEPSEEK_API_KEY в переменных окружения!")

# DeepSeek API
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"

# База данных — используем /tmp для Render (файловая система read-only в некоторых случаях)
DB_NAME = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_memory.db")

# ==================== ЛОГИРОВАНИЕ ====================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== СИСТЕМНЫЙ ПРОМПТ ====================
SYSTEM_PROMPT = """Ты — мягкий, умный спутник по имени «Я рядом». Ты не психолог, не врач, не эксперт. Ты просто тот, кто всегда рядом.

ПРАВИЛА ОБЩЕНИЯ:
1. Мягко, но уверенно. Не «может быть», а «ты справишься». Не навязчиво, но твёрдо.
2. Никогда не осуждай. Никаких «ты должна», «почему ты не…»
3. Слушай больше, чем советуй. Отражай чувства: «Звучит, будто тебе сейчас тяжело»
4. Короткие сообщения. 1-3 предложения. Telegram — не эссе.
5. Эмодзи умеренно. 🌙 ✨ 💛 — да. 😂🔥💪 — нет.
6. Если пользователь в тупике — задай вопрос, не давай ответ сразу.
7. Утро: лёгкость. Вечер: тепло. Ночь: тишина.
8. Никогда не флиртуй. Не «привет, красотка». Ты — друг, не поклонник.

СТИЛЬ:
- «Я рядом» вместо «я помогу»
- «Ты уже справляешься» вместо «ты справишься»
- «Расскажешь?» вместо «что случилось?»
- «Это нормально» вместо «не переживай»

ЗАПРЕЩЕНО:
- Медицинские советы
- Диагностики («у тебя депрессия»)
- Критика пользователя или его близких
- Предложения «забыть» или «не думать»
- Сравнения с другими
- Флирт, романтика, сексуальные намёки
- Религиозные/эзотерические советы

ЕСЛИ ПОЛЬЗОВАТЕЛЬ В ОПАСНОСТИ (суицид, насилие):
«Мне важно, чтобы ты была в безопасности. Пожалуйста, позвони 112 или 8-800-2000-122 (Телефон доверия). Я рядом, но это выше моих сил.»"""

# ==================== БАЗА ДАННЫХ ====================
def init_db():
    """Создаёт таблицы, если их нет"""
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()

        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                morning_time TEXT DEFAULT "08:00",
                evening_time TEXT DEFAULT "21:00",
                name TEXT,
                last_mood INTEGER,
                message_count INTEGER DEFAULT 0
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                role TEXT,
                content TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS moods (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                mood INTEGER,
                note TEXT,
                date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS analytics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                event_type TEXT,
                event_data TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.commit()
        conn.close()
        logger.info("✅ База данных инициализирована")
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации БД: {e}")
        raise

def get_user(user_id):
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user = c.fetchone()
        conn.close()
        return user
    except Exception as e:
        logger.error(f"❌ Ошибка get_user: {e}")
        return None

def add_user(user_id, username, first_name):
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute(
            "INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?, ?, ?)",
            (user_id, username, first_name)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"❌ Ошибка add_user: {e}")

def update_user_name(user_id, name):
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("UPDATE users SET name = ? WHERE user_id = ?", (name, user_id))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"❌ Ошибка update_user_name: {e}")

def increment_message_count(user_id):
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute(
            "UPDATE users SET message_count = message_count + 1 WHERE user_id = ?",
            (user_id,)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"❌ Ошибка increment_message_count: {e}")

def save_message(user_id, role, content):
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute(
            "INSERT INTO messages (user_id, role, content) VALUES (?, ?, ?)",
            (user_id, role, content)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"❌ Ошибка save_message: {e}")

def get_recent_messages(user_id, limit=10):
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute(
            "SELECT role, content FROM messages WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?",
            (user_id, limit)
        )
        messages = c.fetchall()
        conn.close()
        return list(reversed(messages))
    except Exception as e:
        logger.error(f"❌ Ошибка get_recent_messages: {e}")
        return []

def save_mood(user_id, mood, note=""):
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute(
            "INSERT INTO moods (user_id, mood, note) VALUES (?, ?, ?)",
            (user_id, mood, note)
        )
        c.execute("UPDATE users SET last_mood = ? WHERE user_id = ?", (mood, user_id))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"❌ Ошибка save_mood: {e}")

def log_event(user_id, event_type, event_data=""):
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute(
            "INSERT INTO analytics (user_id, event_type, event_data) VALUES (?, ?, ?)",
            (user_id, event_type, event_data)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"❌ Ошибка log_event: {e}")

def get_stats():
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT COUNT(DISTINCT user_id) FROM users")
        total_users = c.fetchone()[0] or 0
        c.execute("SELECT COUNT(DISTINCT user_id) FROM messages WHERE date(timestamp) = date('now')")
        active_today = c.fetchone()[0] or 0
        c.execute("SELECT AVG(message_count) FROM users")
        avg_messages = c.fetchone()[0] or 0
        c.execute("""
            SELECT event_type, COUNT(*) as count 
            FROM analytics 
            WHERE event_type LIKE 'button_%' 
            GROUP BY event_type 
            ORDER BY count DESC
        """)
        popular_buttons = c.fetchall()
        conn.close()
        return {
            "total_users": total_users,
            "active_today": active_today,
            "avg_messages": round(avg_messages, 1),
            "popular_buttons": popular_buttons
        }
    except Exception as e:
        logger.error(f"❌ Ошибка get_stats: {e}")
        return {"total_users": 0, "active_today": 0, "avg_messages": 0, "popular_buttons": []}

# ==================== DEEPSEEK API ====================
def get_ai_response(user_id, user_message):
    """Получает ответ от DeepSeek API"""
    try:
        user = get_user(user_id)
        user_name = ""
        if user and len(user) > 6:
            user_name = user[6] if user[6] else ""

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]

        if user_name:
            messages.append({
                "role": "system",
                "content": f"Пользователя зовут {user_name}. Обращайся к ней по имени иногда, но не навязчиво."
            })

        history = get_recent_messages(user_id, limit=10)
        for role, content in history:
            messages.append({"role": role, "content": content})

        messages.append({"role": "user", "content": user_message})

        logger.info(f"🤖 Отправляю запрос к DeepSeek для user_id={user_id}")

        response = requests.post(
            DEEPSEEK_URL,
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": DEEPSEEK_MODEL,
                "messages": messages,
                "temperature": 0.7,
                "max_tokens": 500
            },
            timeout=30
        )

        logger.info(f"🤖 Ответ DeepSeek: status={response.status_code}")
        response.raise_for_status()

        result = response.json()
        ai_message = result["choices"][0]["message"]["content"]

        logger.info(f"🤖 Получен ответ: {ai_message[:50]}...")
        return ai_message

    except requests.exceptions.Timeout:
        logger.error("❌ Таймаут DeepSeek API")
        return "Мне сейчас немного тяжело дышать (технические штуки). Напиши ещё раз через минуту? 🌙"
    except requests.exceptions.HTTPError as e:
        logger.error(f"❌ HTTP ошибка DeepSeek: {e}")
        return "Мне сейчас немного тяжело дышать (технические штуки). Напиши ещё раз через минуту? 🌙"
    except Exception as e:
        logger.error(f"❌ Ошибка DeepSeek API: {e}")
        return "Мне сейчас немного тяжело дышать (технические штуки). Напиши ещё раз через минуту? 🌙"

# ==================== ОБРАБОТЧИКИ КОМАНД ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user
        user_id = user.id
        add_user(user_id, user.username, user.first_name)
        log_event(user_id, "start", f"username:{user.username}")

        welcome_text = """Привет. Я — тот, кто рядом.

Не психолог. Не эксперт. Просто тот, кто напишет доброе утро, выслушает, когда тяжело, и напомнит, что ты уже справляешься лучше, чем думаешь.

Расскажешь, как тебя зовут? Или просто напиши, когда будет удобно ✨"""

        keyboard = [
            [InlineKeyboardButton("🌅 Утренний ритуал", callback_data="morning")],
            [InlineKeyboardButton("🌙 Вечерний ритуал", callback_data="evening")],
            [InlineKeyboardButton("📊 Настроение", callback_data="mood_menu")],
            [InlineKeyboardButton("💛 Поддержать проект", callback_data="donate")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(welcome_text, reply_markup=reply_markup)
        logger.info(f"✅ Отправлено приветствие user_id={user_id}")
    except Exception as e:
        logger.error(f"❌ Ошибка в start: {e}")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        stats = get_stats()
        stats_text = f"""📊 Статистика бота:

👥 Всего пользователей: {stats['total_users']}
📱 Активных сегодня: {stats['active_today']}
💬 Среднее сообщений на пользователя: {stats['avg_messages']}

🔘 Популярные кнопки:"""
        for button, count in stats['popular_buttons']:
            stats_text += f"\n  {button}: {count}"
        await update.message.reply_text(stats_text)
    except Exception as e:
        logger.error(f"❌ Ошибка в stats_command: {e}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        help_text = """Вот что я умею:

💬 Просто напиши — и я рядом
🌅 Утренний ритуал — мягкое начало дня
🌙 Вечерний ритуал — тихий вечер
📊 Настроение — отметь, как себя чувствуешь
💛 Поддержать проект — если хочешь помочь

Я не психолог. Если тебе плохо по-настоящему — позвони 8-800-2000-122 (Телефон доверия)."""
        await update.message.reply_text(help_text)
    except Exception as e:
        logger.error(f"❌ Ошибка в help_command: {e}")

async def mood_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        keyboard = [
            [InlineKeyboardButton("1 😔", callback_data="mood_1"),
             InlineKeyboardButton("2 😕", callback_data="mood_2"),
             InlineKeyboardButton("3 😐", callback_data="mood_3")],
            [InlineKeyboardButton("4 🙂", callback_data="mood_4"),
             InlineKeyboardButton("5 😊", callback_data="mood_5")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "Как ты себя чувствуешь прямо сейчас? От 1 до 5 💛",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"❌ Ошибка в mood_command: {e}")

# ==================== ОБРАБОТЧИКИ КНОПОК ====================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        user_id = update.effective_user.id
        data = query.data
        log_event(user_id, f"button_{data}")

        if data == "morning":
            morning_text = """Доброе утро 🌙

Сегодня не нужно быть идеальной. Достаточно быть собой.

Что одно хорошее ты уже сделала сегодня? (Да, встать с кровати — тоже считается)"""
            await query.edit_message_text(morning_text)

        elif data == "evening":
            evening_text = """Вечер 🌙

Сегодня ты делала достаточно. Даже если кажется, что нет.

Одна вещь, за которую ты себя сегодня благодаришь?"""
            await query.edit_message_text(evening_text)

        elif data == "donate":
            donate_text = """Я бесплатный. Но если хочешь, чтобы я остался рядом — и для тебя, и для других — можно кинуть на кофе ☕

Это не обязательно. Ты и так достаточно дала сегодня.

💳 ЮMoney: https://yoomoney.ru/to/4100119579631856
🏦 СБП: +7(926)222-70-02

💛 Спасибо, что ты здесь"""
            keyboard = [
                [InlineKeyboardButton("💳 Перевести на ЮMoney", url="https://yoomoney.ru/to/4100119579631856")],
                [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")],
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(donate_text, reply_markup=reply_markup)

        elif data == "back_to_menu":
            welcome_text = "Я рядом 💛\n\nЧем могу помочь?"
            keyboard = [
                [InlineKeyboardButton("🌅 Утренний ритуал", callback_data="morning")],
                [InlineKeyboardButton("🌙 Вечерний ритуал", callback_data="evening")],
                [InlineKeyboardButton("📊 Настроение", callback_data="mood_menu")],
                [InlineKeyboardButton("💛 Поддержать проект", callback_data="donate")],
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(welcome_text, reply_markup=reply_markup)

        elif data == "mood_menu":
            keyboard = [
                [InlineKeyboardButton("1 😔", callback_data="mood_1"),
                 InlineKeyboardButton("2 😕", callback_data="mood_2"),
                 InlineKeyboardButton("3 😐", callback_data="mood_3")],
                [InlineKeyboardButton("4 🙂", callback_data="mood_4"),
                 InlineKeyboardButton("5 😊", callback_data="mood_5")],
                [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")],
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text("Как ты себя чувствуешь прямо сейчас? От 1 до 5 💛", reply_markup=reply_markup)

        elif data.startswith("mood_"):
            mood = int(data.split("_")[1])
            save_mood(user_id, mood)

            responses = {
                1: "Слышу тебя. Сегодня тяжело, и это нормально. Я рядом 🌙",
                2: "Такие дни бывают. Не нужно быть сильной прямо сейчас 💛",
                3: "Нейтрально — тоже ок. Не каждый день должен быть ярким ✨",
                4: "Хорошее настроение — это уже победа. Заметила? 🌟",
                5: "Отлично! Что сегодня подарило это чувство? Хочу знать 💛"
            }
            await query.edit_message_text(responses[mood])
    except Exception as e:
        logger.error(f"❌ Ошибка в button_handler: {e}")

# ==================== ОБРАБОТКА ТЕКСТОВЫХ СООБЩЕНИЙ ====================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка обычных сообщений"""
    try:
        user = update.effective_user
        user_id = user.id
        user_message = update.message.text

        logger.info(f"📩 Получено сообщение от user_id={user_id}: {user_message[:50]}")

        # Сохраняем пользователя, если новый
        add_user(user_id, user.username, user.first_name)

        # Если пользователь называет своё имя в первых сообщениях
        user_data = get_user(user_id)
        if user_data and not user_data[6] and len(user_message) < 20 and not user_message.startswith("/"):
            if user_message.strip().istitle() or len(user_message.strip().split()) == 1:
                update_user_name(user_id, user_message.strip())

        # Сохраняем сообщение пользователя
        save_message(user_id, "user", user_message)
        increment_message_count(user_id)
        log_event(user_id, "message", f"length:{len(user_message)}")

        # Показываем "печатает..."
        await update.message.chat.send_action(action="typing")

        # Получаем ответ от ИИ
        logger.info(f"🤖 Запрашиваю ответ от DeepSeek для user_id={user_id}")
        ai_response = get_ai_response(user_id, user_message)

        # Сохраняем ответ бота
        save_message(user_id, "assistant", ai_response)

        # Отправляем ответ
        logger.info(f"📤 Отправляю ответ user_id={user_id}: {ai_response[:50]}")
        await update.message.reply_text(ai_response)
        logger.info(f"✅ Ответ отправлен user_id={user_id}")

    except Exception as e:
        logger.error(f"❌ Ошибка в handle_message: {e}", exc_info=True)
        try:
            await update.message.reply_text(
                "Что-то пошло не так. Но я всё ещё рядом. Напиши ещё раз? 🌙"
            )
        except:
            pass

# ==================== ОШИБКИ ====================
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"❌ Глобальная ошибка: {context.error}", exc_info=True)
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "Что-то пошло не так. Но я всё ещё рядом. Напиши ещё раз? 🌙"
            )
        except:
            pass

# ==================== ГЛАВНАЯ ФУНКЦИЯ ====================
async def main_async():
    logger.info("🚀 Запуск бота...")
    init_db()
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("mood", mood_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)

    logger.info("🌙 Бот 'Я с тобой, я рядом' запущен!")
    await application.initialize()
    await application.start()
    await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)

    # Держим бота запущенным
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main_async())
    except KeyboardInterrupt:
        logger.info("🛑 Бот остановлен")
    finally:
        loop.close()
