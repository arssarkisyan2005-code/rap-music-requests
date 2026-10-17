
import asyncio
import os
import re
import sqlite3
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
)
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

if not TOKEN or not ADMIN_ID:
    raise RuntimeError("Проверь BOT_TOKEN и ADMIN_ID в настройках")

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())
DB_NAME = "requests.db"


def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                original_text TEXT NOT NULL,
                artist TEXT NOT NULL,
                track TEXT,
                uncensored INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'new',
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()


class RequestForm(StatesGroup):
    waiting_for_request = State()


keyboard = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="🎵 Отправить заявку")]],
    resize_keyboard=True,
)


def parse_request(text):
    uncensored = bool(re.search(
        r"без\s+цензуры|uncensored|explicit|18\+",
        text,
        re.IGNORECASE,
    ))
    cleaned = re.sub(
        r"\s*\(?\s*(без\s+цензуры|uncensored|explicit|18\+)\s*\)?",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()

    parts = re.split(r"\s+[—–-]\s+", cleaned, maxsplit=1)
    artist = parts[0].strip()
    track = parts[1].strip() if len(parts) > 1 else ""
    return artist, track, uncensored


@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🎧 Привет! Предложи музыку для нашего канала.\n\n"
        "Можно отправить:\n"
        "• только исполнителя: Баста\n"
        "• исполнителя и трек: Баста — Моя игра\n"
        "• трек с пометкой «без цензуры».\n\n"
        "Администратор сам решает, что публиковать.",
        reply_markup=keyboard,
    )


@dp.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Отправка отменена.", reply_markup=keyboard)


@dp.message(F.text == "🎵 Отправить заявку")
async def begin_request(message: Message, state: FSMContext):
    await state.set_state(RequestForm.waiting_for_request)
    await message.answer(
        "Напиши исполнителя или исполнителя и название трека.\n\n"
        "Примеры:\n"
        "Баста\n"
        "Баста — Моя игра\n"
        "Баста — Моя игра (без цензуры)\n\n"
        "Для отмены отправь /cancel.",
        reply_markup=ReplyKeyboardRemove(),
    )


@dp.message(RequestForm.waiting_for_request, F.text)
async def receive_request(message: Message, state: FSMContext):
    original = message.text.strip()
    if not original or len(original) > 1000:
        await message.answer("Напиши заявку длиной до 1000 символов.")
        return

    artist, track, uncensored = parse_request(original)
    user = message.from_user

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.execute(
            """INSERT INTO requests
            (user_id, username, original_text, artist, track,
             uncensored, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'new', ?)""",
            (
                user.id,
                user.username,
                original,
                artist,
                track,
                int(uncensored),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        request_id = cursor.lastrowid
        conn.commit()

    username = f"@{user.username}" if user.username else "не указан"
    admin_text = (
        f"🎵 Новая заявка №{request_id}\n\n"
        f"👤 Исполнитель: {artist}\n"
        f"💿 Трек: {track or 'Не указан — выбери сам'}\n"
        f"🔞 Без цензуры: {'Да' if uncensored else 'Не указано'}\n\n"
        f"📝 Исходное сообщение: {original}\n"
        f"👥 Username: {username}\n"
        f"🆔 ID отправителя: {user.id}"
    )

    admin_keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="✅ Взял в работу",
            callback_data=f"take:{request_id}",
        ),
        InlineKeyboardButton(
            text="❌ Отклонить",
            callback_data=f"reject:{request_id}",
        ),
    ]])

    try:
        await bot.send_message(
            ADMIN_ID, admin_text, reply_markup=admin_keyboard
        )
    except Exception:
        await state.clear()
        await message.answer(
            "Заявка сохранена, но уведомление не доставлено. "
            "Сообщи администратору.",
            reply_markup=keyboard,
        )
        return

    await state.clear()
    await message.answer(
        f"✅ Заявка №{request_id} отправлена администратору!",
        reply_markup=keyboard,
    )


@dp.message(RequestForm.waiting_for_request)
async def non_text_request(message: Message):
    await message.answer("Пожалуйста, отправь заявку обычным текстом.")


@dp.callback_query(F.data.startswith("take:"))
async def take_request(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа.", show_alert=True)
        return

    request_id = int(callback.data.split(":")[1])
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.execute(
            "UPDATE requests SET status='in_progress' "
            "WHERE id=? AND status='new'",
            (request_id,),
        )
        conn.commit()

    if cursor.rowcount:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer("Заявка взята в работу.")
    else:
        await callback.answer("Заявка уже обработана.", show_alert=True)


@dp.callback_query(F.data.startswith("reject:"))
async def reject_request(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа.", show_alert=True)
        return

    request_id = int(callback.data.split(":")[1])
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.execute(
            "UPDATE requests SET status='rejected' "
            "WHERE id=? AND status='new'",
            (request_id,),
        )
        conn.commit()

    if cursor.rowcount:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer("Заявка отклонена.")
    else:
        await callback.answer("Заявка уже обработана.", show_alert=True)


async def main():
    init_db()
    print("Бот запущен.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
