import asyncio
import logging
import sqlite3
import sys
import re
from urllib.parse import quote

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, CommandObject, Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ContentType,
)
from aiogram.utils.deep_linking import create_start_link, decode_payload
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

# --- НАСТРОЙКИ ---
BOT_TOKEN = "8671256807:AAF9GkMIl0K0qCtfADOUA7FPdVtZvq8RYQ8" 
ADMIN_ID = 1341871418  # Ваш ID для доступа к рассылке

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ---
DB_PATH = "bot.db"
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
BOT_USERNAME = ""
SLUG_REGEX = re.compile(r"^[a-zA-Z0-9_]{3,32}$")

# --- СОСТОЯНИЯ ---
class BotState(StatesGroup):
    waiting_for_question = State()
    waiting_for_reply = State()
    recipient_id = State()
    reply_to_id = State()
    waiting_for_slug = State()
    waiting_for_broadcast = State()  # Состояние для рассылки

# --- БАЗА ДАННЫХ ---
def init_db():
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS blocks (
                recipient_id INTEGER NOT NULL,
                blocked_id INTEGER NOT NULL,
                block_type TEXT NOT NULL,
                PRIMARY KEY (recipient_id, blocked_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS custom_links (
                slug TEXT PRIMARY KEY,
                owner_id INTEGER NOT NULL
            )
        """)
        # Новая таблица для сохранения всех пользователей бота
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY
            )
        """)

# --- Функции БД ---
def db_add_user(user_id: int):
    with conn:
        conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))

def db_get_all_users():
    cur = conn.execute("SELECT user_id FROM users")
    return [row[0] for row in cur.fetchall()]

def db_get_block_type(recipient_id: int, sender_id: int):
    cur = conn.execute(
        "SELECT block_type FROM blocks WHERE recipient_id=? AND blocked_id=?",
        (recipient_id, sender_id)
    )
    row = cur.fetchone()
    return row[0] if row else None

def db_set_block(recipient_id: int, sender_id: int, block_type: str):
    with conn:
        conn.execute(
            "REPLACE INTO blocks(recipient_id, blocked_id, block_type) VALUES (?, ?, ?)",
            (recipient_id, sender_id, block_type)
        )

def db_delete_block(recipient_id: int, sender_id: int):
    with conn:
        conn.execute(
            "DELETE FROM blocks WHERE recipient_id=? AND blocked_id=?",
            (recipient_id, sender_id)
        )

def db_get_blacklist(recipient_id: int):
    cur = conn.execute(
        "SELECT blocked_id, block_type FROM blocks WHERE recipient_id=?",
        (recipient_id,)
    )
    return cur.fetchall()

def db_save_slug(owner_id: int, slug: str) -> bool:
    slug = slug.lower()
    cur = conn.execute("SELECT owner_id FROM custom_links WHERE slug=?", (slug,))
    row = cur.fetchone()
    if row and row[0] != owner_id:
        return False 
    with conn:
        conn.execute("REPLACE INTO custom_links(slug, owner_id) VALUES (?, ?)", (slug, owner_id))
    return True

def db_delete_slug(owner_id: int, slug: str):
    slug = slug.lower()
    with conn:
        conn.execute("DELETE FROM custom_links WHERE slug=? AND owner_id=?", (slug, owner_id))

def db_get_owner(slug: str):
    cur = conn.execute("SELECT owner_id FROM custom_links WHERE slug=?", (slug.lower(),))
    row = cur.fetchone()
    return row[0] if row else None

def db_get_user_slugs(owner_id: int):
    cur = conn.execute("SELECT slug FROM custom_links WHERE owner_id=?", (owner_id,))
    return [row[0] for row in cur.fetchall()]

init_db()

# --- КЛАВИАТУРЫ ---

async def get_main_menu_kb(user_id: int):
    base_link = await create_start_link(bot, str(user_id), encode=True)
    text_to_share = "Задать мне анонимный вопрос можно по этой ссылке!"
    share_url = f"https://t.me/share/url?url={quote(base_link)}&text={quote(text_to_share)}"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Поделиться ссылкой", url=share_url)],
        [
            InlineKeyboardButton(text="🔗 Мои ссылки", callback_data="menu_links"),
            InlineKeyboardButton(text="🚫 Чёрный список", callback_data="menu_blacklist")
        ]
    ])
    return base_link, kb

def get_links_menu_kb(slugs):
    buttons = []

    # Список ссылок с кнопкой удаления
    for slug in slugs:
        btn_text = f"🔗 /{slug}"
        # Кнопка удаления (передает слаг в callback)
        buttons.append([
            InlineKeyboardButton(text=btn_text, callback_data="ignore"), # Просто текст (неактивная)
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del_slug_{slug}")
        ])

    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_blacklist_menu_kb(rows):
    buttons = []
    if rows:
        for blocked_id, b_type in rows:
            icon = "⛔" if b_type == 'full' else "🔕"
            btn_text = f"🔓 Разбанить {blocked_id} ({icon})"
            buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"list_unblock_{blocked_id}")])

    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_question_kb(recipient_id, sender_id):
    if db_get_block_type(recipient_id, sender_id):
         return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Разблокировать", callback_data=f"unblock_{sender_id}")]
        ])

    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⛔ Бан", callback_data=f"block_{sender_id}_full"),
            InlineKeyboardButton(text="🔕 Тихий бан", callback_data=f"block_{sender_id}_silent")
        ]
    ])

def get_cancel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")]])

# --- ЛОГИКА ОТПРАВКИ ---
async def send_smart_message(chat_id, message, header, reply_markup=None, add_swipe=False):
    text_content = message.text or message.caption or ""
    
    # Если включен режим вопроса (add_swipe=True), оборачиваем в цитату и добавляем подсказку
    if add_swipe:
        if text_content:
            final_text = f"{header}\n\n<blockquote>{text_content}</blockquote>\n\n⬅️ Свайпни чтобы ответить"
        else:
            final_text = f"{header}\n\n⬅️ Свайпни чтобы ответить"
    else:
        # Для обычных ответов отправляем стандартно
        final_text = f"{header}\n\n{text_content}" if text_content else header

    if message.text:
        await bot.send_message(chat_id, final_text, reply_markup=reply_markup, parse_mode="HTML")
    elif message.content_type in [ContentType.PHOTO, ContentType.VIDEO, ContentType.VOICE, ContentType.DOCUMENT, ContentType.AUDIO]:
        await message.copy_to(chat_id, caption=final_text, reply_markup=reply_markup, parse_mode="HTML")
    else:
        await bot.send_message(chat_id, final_text, parse_mode="HTML")
        await message.copy_to(chat_id, reply_markup=reply_markup)

# --- ЕДИНЫЙ ХЕНДЛЕР START ---
@dp.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext):
    # Очищаем состояние при любом старте
    await state.clear()
    
    # Сохраняем пользователя в базу для будущих рассылок
    db_add_user(message.from_user.id)

    args = command.args

    # 1. ЕСТЬ АРГУМЕНТЫ -> ЭТО DEEP LINK (ВОПРОС)
    if args:
        recipient_id = None

        # Пытаемся понять, это ID или кастомный слаг
        try:
            decoded = decode_payload(args)
            recipient_id = int(decoded)
        except: pass

        # Если не ID, ищем в базе слагов
        if not recipient_id:
            recipient_id = db_get_owner(args)

        if not recipient_id:
            await message.answer("❌ Ссылка недействительна или владелец удалил её.")
            return

        if str(recipient_id) == str(message.from_user.id):
            await message.answer("Вы перешли по собственной ссылке. Введите /start без аргументов, чтобы открыть меню.")
            return

        await state.update_data(recipient_id=recipient_id)
        await state.set_state(BotState.waiting_for_question)
        await message.answer("✍️ <b>Напишите ваш анонимный вопрос:</b>", reply_markup=get_cancel_kb(), parse_mode="HTML")
        return

    # 2. НЕТ АРГУМЕНТОВ -> ГЛАВНОЕ МЕНЮ
    link, kb = await get_main_menu_kb(message.from_user.id)
    await message.answer(
        f"👋 Привет! Это твой бот анонимных вопросов.\n\n"
        f"🔗 Твоя ссылка:\n`{link}`\n\n"
        f"👇 Настрой бота через меню:",
        reply_markup=kb,
        parse_mode="Markdown"
    )

# --- CALLBACKS МЕНЮ ---

@dp.callback_query(F.data == "cancel_action")
async def cb_cancel(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("🚫 Действие отменено.")

@dp.callback_query(F.data == "menu_main")
async def cb_menu_main(call: CallbackQuery, state: FSMContext):
    await state.clear()
    link, kb = await get_main_menu_kb(call.from_user.id)
    await call.message.edit_text(
        f"👋 Привет! Это твой бот анонимных вопросов.\n\n"
        f"🔗 Твоя ссылка:\n`{link}`\n\n"
        f"👇 Настрой бота через меню:",
        reply_markup=kb,
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "menu_links")
async def cb_menu_links(call: CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    slugs = db_get_user_slugs(user_id)

    text = (
        "✨ Тут вы можете управлять своими уникальными ссылками!\n\n"
        "Чтобы <b>удалить ссылку</b>, нажмите на корзину.\n"
        "Чтобы <b>создать новую</b>, просто пришлите желаемое слово в чат."
        "<b>\n\n🔗 Ваши кастомные ссылки:</b>"
    )

    if slugs:
        links_str = "\n\n".join([f"https://t.me/{BOT_USERNAME}?start={slug}" for slug in slugs])
        text += f"\n\n{links_str}"

    await state.set_state(BotState.waiting_for_slug)
    await call.message.edit_text(text, reply_markup=get_links_menu_kb(slugs), parse_mode="HTML")

# УДАЛЕНИЕ ССЫЛКИ
@dp.callback_query(F.data.startswith("del_slug_"))
async def cb_delete_slug(call: CallbackQuery):
    slug = call.data.split("_", 2)[2] # del_slug_word -> word
    db_delete_slug(call.from_user.id, slug)

    # Обновляем меню
    slugs = db_get_user_slugs(call.from_user.id)
    
    text = (
        "✨ Тут вы можете управлять своими уникальными ссылками!\n\n"
        "Чтобы <b>удалить ссылку</b>, нажмите на корзину.\n"
        "Чтобы <b>создать новую</b>, просто пришлите желаемое слово в чат."
        "<b>\n\n🔗 Ваши кастомные ссылки:</b>"
    )
    
    if slugs:
        links_str = "\n\n".join([f"https://t.me/{BOT_USERNAME}?start={s}" for s in slugs])
        text += f"\n\n{links_str}"

    await call.message.edit_text(text, reply_markup=get_links_menu_kb(slugs), parse_mode="HTML")
    await call.answer("Ссылка удалена")

# СОЗДАНИЕ ССЫЛКИ (ТЕКСТ)
@dp.message(BotState.waiting_for_slug)
async def process_new_slug(message: Message, state: FSMContext):
    slug = message.text.strip()

    if not SLUG_REGEX.match(slug):
        await message.answer("⚠️ Ошибка: используйте только латинские буквы, цифры и _, от 3 до 32 символов.")
        return

    if db_save_slug(message.from_user.id, slug):
        url = f"https://t.me/{BOT_USERNAME}?start={slug.lower()}"
        await message.answer(f"✅ <b>Ссылка создана!</b>\n{url}", parse_mode="HTML")
        await state.clear()

        # Возвращаем главное меню
        link, kb = await get_main_menu_kb(message.from_user.id)
        await message.answer("Главное меню:", reply_markup=kb)
    else:
        await message.answer("❌ Этот алиас уже занят. Попробуй другой.")

# ЧЕРНЫЙ СПИСОК
@dp.callback_query(F.data == "menu_blacklist")
async def cb_menu_blacklist(call: CallbackQuery):
    rows = db_get_blacklist(call.from_user.id)
    text = "🚫 <b>Чёрный список:</b>" if rows else "🎉 <b>Чёрный список пуст.</b>"
    await call.message.edit_text(text, reply_markup=get_blacklist_menu_kb(rows), parse_mode="HTML")

@dp.callback_query(F.data.startswith("list_unblock_"))
async def cb_list_unblock(call: CallbackQuery):
    blocked_id = int(call.data.split("_")[2])
    db_delete_block(call.from_user.id, blocked_id)
    rows = db_get_blacklist(call.from_user.id)
    text = "🚫 <b>Чёрный список:</b>" if rows else "🎉 <b>Чёрный список пуст.</b>"
    await call.message.edit_text(text, reply_markup=get_blacklist_menu_kb(rows), parse_mode="HTML")
    await call.answer("Разблокировано!")

# --- ОБРАБОТКА ВОПРОСА (АНОНИМНОГО) ---
@dp.message(BotState.waiting_for_question)
async def process_question(message: Message, state: FSMContext):
    data = await state.get_data()
    recipient_id = data.get("recipient_id")
    sender_id = message.from_user.id

    # ЛОКАЛЬНАЯ ПРОВЕРКА БАНА
    b_type = db_get_block_type(recipient_id, sender_id)
    if b_type == 'full':
        await message.answer("⛔ Вы заблокированы этим пользователем.")
        await state.clear()
        return
    elif b_type == 'silent':
        await message.answer("✅ Сообщение отправлено!")
        await state.clear()
        return

    try:
        kb = get_question_kb(recipient_id, sender_id)
        # Просто передаем флаг add_swipe=True, всё форматирование возьмет на себя send_smart_message
        await send_smart_message(recipient_id, message, "📬 <b>Новый вопрос!</b>", reply_markup=kb, add_swipe=True)
        await message.answer("✅ Сообщение отправлено!")
    except Exception:
        await message.answer("Не удалось доставить (пользователь недоступен).")

    await state.clear()

# --- ОТВЕТЫ ЧЕРЕЗ REPLY (СВАЙП) ---
@dp.message(F.reply_to_message)
async def process_reply_swipe(message: Message):
    reply = message.reply_to_message
    
    # Ищем ID отправителя в клавиатуре сообщения, на которое отвечают
    if not reply.reply_markup:
        return
    
    target_id = None
    for row in reply.reply_markup.inline_keyboard:
        for btn in row:
            if btn.callback_data and ("block_" in btn.callback_data or "unblock_" in btn.callback_data):
                target_id = int(btn.callback_data.split("_")[1])
                break
    
    if target_id:
        try:
            await send_smart_message(target_id, message, "🔔 <b>Вам пришёл ответ:</b>")
            await message.answer("✅ Ответ отправлен!")
        except:
            await message.answer("Не удалось отправить ответ.")

# --- БАНЫ ВНУТРИ СООБЩЕНИЙ ---
@dp.callback_query(F.data.startswith("block_"))
async def cb_block_msg(call: CallbackQuery):
    parts = call.data.split("_")
    blocked_id = int(parts[1])
    b_type = parts[2]
    db_set_block(call.from_user.id, blocked_id, b_type)
    await call.answer("Заблокирован")
    await call.message.edit_reply_markup(reply_markup=get_question_kb(call.from_user.id, blocked_id))

@dp.callback_query(F.data.startswith("unblock_"))
async def cb_unblock_msg(call: CallbackQuery):
    blocked_id = int(call.data.split("_")[1])
    db_delete_block(call.from_user.id, blocked_id)
    await call.answer("Разблокировано")
    await call.message.edit_reply_markup(reply_markup=get_question_kb(call.from_user.id, blocked_id))

# --- РАССЫЛКА (АДМИН) ---
@dp.message(Command("broadcast"), F.from_user.id == ADMIN_ID)
async def cmd_broadcast(message: Message, state: FSMContext):
    await state.set_state(BotState.waiting_for_broadcast)
    await message.answer(
        "📢 <b>Режим рассылки</b>\n\nОтправьте мне сообщение, которое нужно разослать всем пользователям бота (можно с фото, видео, голосовым и т.д.):",
        reply_markup=get_cancel_kb(),
        parse_mode="HTML"
    )

@dp.message(BotState.waiting_for_broadcast, F.from_user.id == ADMIN_ID)
async def process_broadcast(message: Message, state: FSMContext):
    await state.clear()
    users = db_get_all_users()
    
    if not users:
        await message.answer("В базе данных пока нет пользователей.")
        return

    await message.answer(f"⏳ Начинаю рассылку для {len(users)} пользователей. Это может занять некоторое время...")
    
    success = 0
    failed = 0
    
    for user_id in users:
        try:
            # Копируем сообщение админа (текст, фото, видео и т.д.) пользователю
            await message.copy_to(user_id)
            success += 1
            await asyncio.sleep(0.05)  # Защита от спам-лимитов Telegram
        except Exception:
            failed += 1
            
    await message.answer(
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"Успешно доставлено: {success}\n"
        f"Не доставлено (бот заблокирован/удален): {failed}",
        parse_mode="HTML"
    )

async def main():
    global BOT_USERNAME
    if BOT_TOKEN == "ТВОЙ_ТОКЕН_ЗДЕСЬ":
        print("ОШИБКА: Вставь токен!")
        return
    me = await bot.get_me()
    BOT_USERNAME = me.username
    print(f"Бот @{BOT_USERNAME} запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Стоп.")
