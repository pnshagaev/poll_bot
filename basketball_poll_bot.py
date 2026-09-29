from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import find_dotenv, load_dotenv
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from game_store import GamesStore, GamesStoreError, parse_utc_datetime
from commands import BOT_COMMANDS, help_text


logger = logging.getLogger(__name__)
UTC = timezone.utc
POLL_OPTIONS = ["Буду", "Не буду", "50/50"]
VENUES = ["Площадка №1", "Площадка №2", "Площадка №3"]
PAGE_SIZE = 6

(
    ADD_TOUR,
    ADD_MATCH_AT,
    ADD_POLL_AT,
    ADD_CUSTOM_POLL_AT,
    ADD_VENUE,
    ADD_CUSTOM_VENUE,
    ADD_FIRST_TEAM,
    ADD_CUSTOM_FIRST_TEAM,
    ADD_SECOND_TEAM,
    ADD_CUSTOM_SECOND_TEAM,
    ADD_CONFIRM,
    DELETE_SELECT,
    DELETE_CONFIRM,
) = range(12)

load_dotenv(find_dotenv())
POLL_BOT_TOKEN = os.getenv("POLL_BOT_TOKEN")
GAMES_STORE = GamesStore(Path(__file__).with_name("games.json"))


def load_chat_ids() -> list[int]:
    raw_chat_ids = os.getenv("TG_CHAT_IDS")
    if not raw_chat_ids:
        raise RuntimeError("TG_CHAT_IDS is not configured")

    try:
        chat_ids = json.loads(raw_chat_ids)
    except json.JSONDecodeError as error:
        raise RuntimeError("TG_CHAT_IDS must be a JSON array") from error

    if not isinstance(chat_ids, list) or not chat_ids:
        raise RuntimeError("TG_CHAT_IDS must be a non-empty JSON array")

    normalized_chat_ids = []
    for chat_id in chat_ids:
        if isinstance(chat_id, bool):
            raise RuntimeError("TG_CHAT_IDS must contain chat IDs")
        try:
            normalized_chat_ids.append(int(chat_id))
        except (TypeError, ValueError) as error:
            raise RuntimeError("TG_CHAT_IDS must contain chat IDs") from error

    return normalized_chat_ids


def load_owner_user_id() -> int:
    raw_owner_id = os.getenv("OWNER_USER_ID")
    if not raw_owner_id:
        raise RuntimeError("OWNER_USER_ID is not configured")
    try:
        return int(raw_owner_id)
    except ValueError as error:
        raise RuntimeError("OWNER_USER_ID must be an integer") from error


def is_owner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    return bool(
        update.effective_user
        and update.effective_user.id == context.application.bot_data["owner_user_id"]
    )


def game_job_name(game_id: str, chat_id: int) -> str:
    return f"game:{game_id}:{chat_id}"


def should_cancel_game_job(game: dict[str, Any], now: datetime) -> bool:
    return (
        game["status"] == "scheduled"
        and parse_utc_datetime(game["poll_at"]) > now.astimezone(UTC)
    )


def parse_user_datetime(value: str) -> datetime:
    try:
        return datetime.strptime(value.strip(), "%d.%m.%Y %H:%M").replace(tzinfo=UTC)
    except ValueError as error:
        raise ValueError("Используйте формат ДД.ММ.ГГГГ ЧЧ:ММ в UTC") from error


def suggested_poll_at(match_at: datetime) -> datetime:
    return (match_at - timedelta(days=2)).replace(hour=8, minute=0, second=0, microsecond=0)


def format_game_question(
    tour: str,
    match_at: datetime,
    venue: str,
    first_team: str,
    second_team: str,
) -> str:
    weekdays = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
    return (
        f"{tour} ({weekdays[match_at.weekday()]}) "
        f"{match_at:%d.%m.%Y}\t{match_at:%H:%M}\n"
        f"{venue}\n{first_team}\t-\t{second_team}"
    )


def game_label(game: dict[str, Any]) -> str:
    match_at = parse_utc_datetime(game["match_at"])
    title = game["question"].splitlines()[0].replace("\t", " ")
    return f"{match_at:%d.%m.%Y} — {title}"[:60]


def game_details(game: dict[str, Any]) -> str:
    sent_count = len(game["sent_chat_ids"])
    return (
        f"{game['question']}\n\n"
        f"Матч: {parse_utc_datetime(game['match_at']):%d.%m.%Y %H:%M} UTC\n"
        f"Опрос: {parse_utc_datetime(game['poll_at']):%d.%m.%Y %H:%M} UTC\n"
        f"Статус: {game['status']}\n"
        f"Опрос отправлен в чатов: {sent_count}"
    )


def delete_keyboard(games: list[dict[str, Any]], page: int) -> InlineKeyboardMarkup:
    page_count = max(1, (len(games) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, page_count - 1))
    rows = [
        [
            InlineKeyboardButton(
                game_label(game),
                callback_data=f"delete-select:{game['id']}:{page}",
            )
        ]
        for game in games[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    ]
    navigation = []
    if page > 0:
        navigation.append(InlineKeyboardButton("◀ Назад", callback_data=f"delete-page:{page - 1}"))
    if page < page_count - 1:
        navigation.append(InlineKeyboardButton("Вперёд ▶", callback_data=f"delete-page:{page + 1}"))
    if navigation:
        rows.append(navigation)
    rows.append([InlineKeyboardButton("Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(rows)


async def notify_chat_about_error(bot: Bot, chat_id: int) -> None:
    try:
        await bot.send_message(
            chat_id=chat_id,
            text="Не удалось отправить или закрепить опрос. Попробуйте позже.",
        )
    except TelegramError:
        logger.exception("Could not send an error notification to chat %s", chat_id)


async def notify_owner(bot: Bot, owner_user_id: int, message: str) -> None:
    try:
        await bot.send_message(chat_id=owner_user_id, text=message)
    except TelegramError:
        logger.exception("Could not notify the bot owner")


async def send_poll_and_pin(bot: Bot, chat_id: int, question: str) -> bool:
    try:
        message = await bot.send_poll(
            chat_id=chat_id,
            question=question,
            options=POLL_OPTIONS,
            is_anonymous=False,
        )
    except TelegramError:
        logger.exception("Could not send a poll in chat %s", chat_id)
        await notify_chat_about_error(bot, chat_id)
        return False

    try:
        await bot.pin_chat_message(chat_id=chat_id, message_id=message.message_id)
    except TelegramError:
        logger.exception("Could not pin a poll in chat %s", chat_id)
        await notify_chat_about_error(bot, chat_id)
    return True


async def send_daily_poll(context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_poll_and_pin(
        context.bot,
        context.job.chat_id,
        context.job.data["question"],
    )


async def send_game_poll(context: ContextTypes.DEFAULT_TYPE) -> None:
    game_id = context.job.data["game_id"]
    chat_id = context.job.chat_id
    store: GamesStore = context.application.bot_data["games_store"]
    chat_ids: list[int] = context.application.bot_data["chat_ids"]

    try:
        game = store.get(game_id)
        if game is None or game["status"] != "scheduled" or chat_id in game["sent_chat_ids"]:
            return
        if await send_poll_and_pin(context.bot, chat_id, game["question"]):
            store.mark_delivery(game_id, chat_id, chat_ids)
    except GamesStoreError:
        logger.exception("Could not update game %s after sending its poll", game_id)
        await notify_owner(
            context.bot,
            context.application.bot_data["owner_user_id"],
            "Опрос был отправлен, но games.json не удалось обновить. Проверьте лог бота.",
        )


def set_daily_polls(application: Application, chat_id: int) -> None:
    application.job_queue.run_daily(
        callback=send_daily_poll,
        time=time(9, tzinfo=UTC),
        # Sunday: the day before the Monday training.
        days=(0,),
        data={"question": "Тренировка завтра"},
        chat_id=chat_id,
        name="Опрос по треням в ПН",
    )
    application.job_queue.run_daily(
        callback=send_daily_poll,
        time=time(9, tzinfo=UTC),
        # Tuesday: the day before the Wednesday game training.
        days=(2,),
        data={"question": "Игровая завтра"},
        chat_id=chat_id,
        name="Опрос по игровым в СР",
    )


def schedule_game(application: Application, game: dict[str, Any], chat_ids: list[int]) -> None:
    if game["status"] != "scheduled":
        return
    poll_at = parse_utc_datetime(game["poll_at"])
    if poll_at <= datetime.now(UTC):
        return

    for chat_id in chat_ids:
        if chat_id in game["sent_chat_ids"]:
            continue
        application.job_queue.run_once(
            callback=send_game_poll,
            when=poll_at,
            data={"game_id": game["id"]},
            chat_id=chat_id,
            name=game_job_name(game["id"], chat_id),
        )


def cancel_game_jobs(application: Application, game_id: str, chat_ids: list[int]) -> None:
    for chat_id in chat_ids:
        for job in application.job_queue.get_jobs_by_name(game_job_name(game_id, chat_id)):
            job.schedule_removal()


def restore_games(application: Application, store: GamesStore, chat_ids: list[int]) -> None:
    store.mark_expired(datetime.now(UTC))
    for game in store.load():
        schedule_game(application, game, chat_ids)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text("Привет! Это бот опросник. Список команд: /help")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(help_text())


async def test(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update, context):
        return
    chat_id = update.effective_message.chat_id
    await send_poll_and_pin(context.bot, chat_id, f"current chat id: {chat_id}")


async def schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    job_names = [job.name for job in context.job_queue.jobs()]
    await update.effective_message.reply_text("\n\n".join(job_names) or "Нет запланированных опросов")


async def games(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    store: GamesStore = context.application.bot_data["games_store"]
    try:
        stored_games = store.load()
    except GamesStoreError:
        logger.exception("Could not load games for /games")
        await update.effective_message.reply_text("Не удалось прочитать games.json. Проверьте лог бота.")
        return

    if stored_games:
        text = "\n\n".join(game_details(game) for game in stored_games)
    else:
        text = "В games.json пока нет игр."
    markup = None
    if is_owner(update, context):
        markup = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Добавить игру", callback_data="menu-add")],
                [InlineKeyboardButton("Удалить игру", callback_data="menu-delete")],
            ]
        )
    await update.effective_message.reply_text(text[:4096], reply_markup=markup)


async def add_game_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_owner(update, context):
        return ConversationHandler.END
    if update.callback_query:
        await update.callback_query.answer()
    context.user_data["game_draft"] = {}
    await update.effective_message.reply_text(
        "Добавление игры. Введите название тура, например: Тур 6\n/cancel — отмена"
    )
    return ADD_TOUR


async def add_tour(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    draft = context.user_data["game_draft"]
    draft["tour"] = update.effective_message.text.strip()
    await update.effective_message.reply_text(
        "Введите дату и время матча в UTC: ДД.ММ.ГГГГ ЧЧ:ММ\nНапример: 18.10.2026 21:00"
    )
    return ADD_MATCH_AT


async def add_match_at(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        match_at = parse_user_datetime(update.effective_message.text)
        if match_at <= datetime.now(UTC):
            raise ValueError("Дата матча должна быть в будущем")
    except ValueError as error:
        await update.effective_message.reply_text(f"{error}. Попробуйте ещё раз.")
        return ADD_MATCH_AT

    draft = context.user_data["game_draft"]
    draft["match_at"] = match_at
    draft["poll_at"] = suggested_poll_at(match_at)
    if draft["poll_at"] <= datetime.now(UTC):
        await update.effective_message.reply_text(
            "Расчётное время опроса уже прошло. Введите будущее время опроса в UTC: "
            "ДД.ММ.ГГГГ ЧЧ:ММ"
        )
        return ADD_CUSTOM_POLL_AT

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Подтвердить", callback_data="add-use-suggested-poll")],
            [InlineKeyboardButton("Изменить", callback_data="add-custom-poll")],
            [InlineKeyboardButton("Отмена", callback_data="cancel")],
        ]
    )
    await update.effective_message.reply_text(
        f"Опрос будет отправлен {draft['poll_at']:%d.%m.%Y %H:%M} UTC. Подтвердить?",
        reply_markup=keyboard,
    )
    return ADD_POLL_AT


async def add_use_suggested_poll(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Выберите площадку:", reply_markup=venue_keyboard())
    return ADD_VENUE


async def add_custom_poll_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Введите дату и время опроса в UTC: ДД.ММ.ГГГГ ЧЧ:ММ")
    return ADD_CUSTOM_POLL_AT


async def add_custom_poll_at(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    draft = context.user_data["game_draft"]
    try:
        poll_at = parse_user_datetime(update.effective_message.text)
        if poll_at <= datetime.now(UTC):
            raise ValueError("Дата опроса должна быть в будущем")
        if poll_at >= draft["match_at"]:
            raise ValueError("Опрос должен быть раньше матча")
    except ValueError as error:
        await update.effective_message.reply_text(f"{error}. Попробуйте ещё раз.")
        return ADD_CUSTOM_POLL_AT

    draft["poll_at"] = poll_at
    await update.effective_message.reply_text("Выберите площадку:", reply_markup=venue_keyboard())
    return ADD_VENUE


def venue_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(venue, callback_data=f"add-venue:{index}")]
        for index, venue in enumerate(VENUES)
    ]
    rows.append([InlineKeyboardButton("Другая", callback_data="add-venue:other")])
    rows.append([InlineKeyboardButton("Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(rows)


async def add_venue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    venue_value = query.data.split(":", 1)[1]
    if venue_value == "other":
        await query.edit_message_text("Введите название площадки:")
        return ADD_CUSTOM_VENUE

    context.user_data["game_draft"]["venue"] = VENUES[int(venue_value)]
    await query.edit_message_text(
        "Выберите первую команду. Порядок команд влияет на цвет формы:",
        reply_markup=first_team_keyboard(),
    )
    return ADD_FIRST_TEAM


async def add_custom_venue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["game_draft"]["venue"] = update.effective_message.text.strip()
    await update.effective_message.reply_text(
        "Выберите первую команду. Порядок команд влияет на цвет формы:",
        reply_markup=first_team_keyboard(),
    )
    return ADD_FIRST_TEAM


def first_team_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Авито", callback_data="add-first-team:avito")],
            [InlineKeyboardButton("Другая команда", callback_data="add-first-team:other")],
            [InlineKeyboardButton("Отмена", callback_data="cancel")],
        ]
    )


def second_team_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Авито", callback_data="add-second-team:avito")],
            [InlineKeyboardButton("Другая команда", callback_data="add-second-team:other")],
            [InlineKeyboardButton("Отмена", callback_data="cancel")],
        ]
    )


def normalize_team_name(value: str) -> str:
    team_name = value.strip()
    if not team_name:
        raise ValueError("Название команды не может быть пустым")
    if team_name.casefold() == "авито":
        return "Авито"
    return team_name


async def add_first_team(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    team_value = query.data.split(":", 1)[1]
    if team_value == "other":
        await query.edit_message_text("Введите первую команду:")
        return ADD_CUSTOM_FIRST_TEAM

    context.user_data["game_draft"]["first_team"] = "Авито"
    await query.edit_message_text(
        "Выберите вторую команду. Порядок команд влияет на цвет формы:",
        reply_markup=second_team_keyboard(),
    )
    return ADD_SECOND_TEAM


async def add_custom_first_team(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        first_team = normalize_team_name(update.effective_message.text)
    except ValueError as error:
        await update.effective_message.reply_text(f"{error}. Попробуйте ещё раз.")
        return ADD_CUSTOM_FIRST_TEAM

    context.user_data["game_draft"]["first_team"] = first_team
    await update.effective_message.reply_text(
        "Выберите вторую команду. Порядок команд влияет на цвет формы:",
        reply_markup=second_team_keyboard(),
    )
    return ADD_SECOND_TEAM


async def add_second_team(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    team_value = query.data.split(":", 1)[1]
    if team_value == "other":
        await query.answer()
        await query.edit_message_text("Введите вторую команду:")
        return ADD_CUSTOM_SECOND_TEAM

    if context.user_data["game_draft"]["first_team"] == "Авито":
        await query.answer("Команды должны различаться", show_alert=True)
        return ADD_SECOND_TEAM

    await query.answer()
    context.user_data["game_draft"]["second_team"] = "Авито"
    return await show_game_preview(update, context)


async def add_custom_second_team(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    draft = context.user_data["game_draft"]
    try:
        second_team = normalize_team_name(update.effective_message.text)
    except ValueError as error:
        await update.effective_message.reply_text(f"{error}. Попробуйте ещё раз.")
        return ADD_CUSTOM_SECOND_TEAM

    if second_team == draft["first_team"]:
        await update.effective_message.reply_text("Первая и вторая команды должны различаться. Попробуйте ещё раз.")
        return ADD_CUSTOM_SECOND_TEAM
    if "Авито" not in (draft["first_team"], second_team):
        await update.effective_message.reply_text(
            "Одна из команд должна быть Авито. Выберите вторую команду:",
            reply_markup=second_team_keyboard(),
        )
        return ADD_SECOND_TEAM

    draft["second_team"] = second_team
    return await show_game_preview(update, context)


async def show_game_preview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    draft = context.user_data["game_draft"]
    question = format_game_question(
        draft["tour"],
        draft["match_at"],
        draft["venue"],
        draft["first_team"],
        draft["second_team"],
    )
    if len(question) > 300:
        await update.effective_message.reply_text("Текст опроса длиннее 300 символов. Начните заново: /addgame")
        context.user_data.pop("game_draft", None)
        return ConversationHandler.END

    draft["question"] = question
    preview_text = f"Предпросмотр:\n\n{question}\n\nОпрос: {draft['poll_at']:%d.%m.%Y %H:%M} UTC"
    preview_keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Добавить", callback_data="add-confirm")],
            [InlineKeyboardButton("Изменить", callback_data="add-restart")],
            [InlineKeyboardButton("Отмена", callback_data="cancel")],
        ]
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(preview_text, reply_markup=preview_keyboard)
    else:
        await update.effective_message.reply_text(preview_text, reply_markup=preview_keyboard)
    return ADD_CONFIRM


async def add_game_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    draft = context.user_data["game_draft"]
    game = {
        "id": uuid.uuid4().hex[:12],
        "match_at": draft["match_at"].isoformat().replace("+00:00", "Z"),
        "poll_at": draft["poll_at"].isoformat().replace("+00:00", "Z"),
        "question": draft["question"],
        "first_team": draft["first_team"],
        "second_team": draft["second_team"],
        "status": "scheduled",
        "sent_chat_ids": [],
    }
    store: GamesStore = context.application.bot_data["games_store"]
    try:
        store.add(game)
        schedule_game(context.application, game, context.application.bot_data["chat_ids"])
    except GamesStoreError:
        logger.exception("Could not add a game")
        await query.edit_message_text("Не удалось сохранить игру. Проверьте лог бота.")
        return ConversationHandler.END

    context.user_data.pop("game_draft", None)
    await query.edit_message_text(
        f"Игра добавлена. Опрос назначен на {draft['poll_at']:%d.%m.%Y %H:%M} UTC."
    )
    return ConversationHandler.END


async def delete_game_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_owner(update, context):
        return ConversationHandler.END
    if update.callback_query:
        await update.callback_query.answer()
    store: GamesStore = context.application.bot_data["games_store"]
    try:
        stored_games = store.load()
    except GamesStoreError:
        logger.exception("Could not load games for deletion")
        await update.effective_message.reply_text("Не удалось прочитать games.json. Проверьте лог бота.")
        return ConversationHandler.END

    if not stored_games:
        await update.effective_message.reply_text("В games.json нет игр для удаления.")
        return ConversationHandler.END
    await update.effective_message.reply_text(
        "Выберите игру для удаления:", reply_markup=delete_keyboard(stored_games, 0)
    )
    return DELETE_SELECT


async def delete_game_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    page = int(query.data.split(":", 1)[1])
    store: GamesStore = context.application.bot_data["games_store"]
    await query.edit_message_text(
        "Выберите игру для удаления:", reply_markup=delete_keyboard(store.load(), page)
    )
    return DELETE_SELECT


async def delete_game_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    _, game_id, page = query.data.split(":")
    store: GamesStore = context.application.bot_data["games_store"]
    game = store.get(game_id)
    if game is None:
        await query.edit_message_text("Игра уже удалена.")
        return ConversationHandler.END
    await query.edit_message_text(
        f"Удалить эту запись?\n\n{game_details(game)}",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Удалить", callback_data=f"delete-confirm:{game_id}:{page}")],
                [InlineKeyboardButton("Отмена", callback_data="cancel")],
            ]
        ),
    )
    return DELETE_CONFIRM


async def delete_game_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    _, game_id, _ = query.data.split(":")
    store: GamesStore = context.application.bot_data["games_store"]
    try:
        game = store.get(game_id)
        if game is None:
            await query.edit_message_text("Игра уже удалена.")
            return ConversationHandler.END
        if should_cancel_game_job(game, datetime.now(UTC)):
            cancel_game_jobs(context.application, game_id, context.application.bot_data["chat_ids"])
        store.delete(game_id)
    except GamesStoreError:
        logger.exception("Could not delete game %s", game_id)
        await query.edit_message_text("Не удалось удалить игру. Проверьте лог бота.")
        return ConversationHandler.END

    await query.edit_message_text("Игра удалена из games.json.")
    return ConversationHandler.END


async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("game_draft", None)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("Действие отменено.")
    else:
        await update.effective_message.reply_text("Действие отменено.")
    return ConversationHandler.END


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled exception while processing an update", exc_info=context.error)

    chat_id = None
    if isinstance(update, Update) and update.effective_chat:
        chat_id = update.effective_chat.id
    elif context.job and context.job.chat_id:
        chat_id = context.job.chat_id

    if chat_id is not None:
        await notify_chat_about_error(context.bot, chat_id)
    await notify_owner(
        context.bot,
        context.application.bot_data["owner_user_id"],
        "В работе бота произошла ошибка. Подробности записаны в журнал.",
    )


async def post_init(application: Application) -> None:
    try:
        await application.bot.set_my_commands(BOT_COMMANDS)
    except TelegramError:
        logger.exception("Could not publish the bot command list")


def management_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("addgame", add_game_start),
            CommandHandler("deletegame", delete_game_start),
            CallbackQueryHandler(add_game_start, pattern="^menu-add$"),
            CallbackQueryHandler(delete_game_start, pattern="^menu-delete$"),
        ],
        states={
            ADD_TOUR: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_tour)],
            ADD_MATCH_AT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_match_at)],
            ADD_POLL_AT: [
                CallbackQueryHandler(add_use_suggested_poll, pattern="^add-use-suggested-poll$"),
                CallbackQueryHandler(add_custom_poll_start, pattern="^add-custom-poll$"),
            ],
            ADD_CUSTOM_POLL_AT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_custom_poll_at)],
            ADD_VENUE: [CallbackQueryHandler(add_venue, pattern="^add-venue:")],
            ADD_CUSTOM_VENUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_custom_venue)],
            ADD_FIRST_TEAM: [CallbackQueryHandler(add_first_team, pattern="^add-first-team:")],
            ADD_CUSTOM_FIRST_TEAM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_custom_first_team)
            ],
            ADD_SECOND_TEAM: [CallbackQueryHandler(add_second_team, pattern="^add-second-team:")],
            ADD_CUSTOM_SECOND_TEAM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_custom_second_team)
            ],
            ADD_CONFIRM: [
                CallbackQueryHandler(add_game_confirm, pattern="^add-confirm$"),
                CallbackQueryHandler(add_game_start, pattern="^add-restart$"),
            ],
            DELETE_SELECT: [
                CallbackQueryHandler(delete_game_page, pattern="^delete-page:"),
                CallbackQueryHandler(delete_game_select, pattern="^delete-select:"),
            ],
            DELETE_CONFIRM: [CallbackQueryHandler(delete_game_confirm, pattern="^delete-confirm:")],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_conversation),
            CallbackQueryHandler(cancel_conversation, pattern="^cancel$"),
        ],
    )


def main() -> None:
    if not POLL_BOT_TOKEN:
        raise RuntimeError("POLL_BOT_TOKEN is not configured")

    chat_ids = load_chat_ids()
    owner_user_id = load_owner_user_id()
    try:
        GAMES_STORE.load()
    except GamesStoreError as error:
        raise RuntimeError(f"Could not load games.json: {error}") from error

    application = Application.builder().token(POLL_BOT_TOKEN).post_init(post_init).build()
    if application.job_queue is None:
        raise RuntimeError("JobQueue is unavailable; install dependencies from requirements.txt")

    application.bot_data.update(
        {
            "chat_ids": chat_ids,
            "owner_user_id": owner_user_id,
            "games_store": GAMES_STORE,
        }
    )
    for chat_id in chat_ids:
        set_daily_polls(application, chat_id)
    restore_games(application, GAMES_STORE, chat_ids)

    application.add_handler(CommandHandler(["start", "ping"], start))
    application.add_handler(CommandHandler(["help"], help_command))
    application.add_handler(CommandHandler(["test"], test))
    application.add_handler(CommandHandler(["schedule"], schedule))
    application.add_handler(CommandHandler(["games"], games))
    application.add_handler(management_conversation())
    application.add_error_handler(error_handler)
    application.run_polling()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    main()
