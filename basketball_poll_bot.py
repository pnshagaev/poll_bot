from datetime import datetime, timezone, time
from telegram.ext import Application, ContextTypes, CommandHandler
from telegram import Update
import json
from dotenv import load_dotenv, find_dotenv
import os


load_dotenv(find_dotenv())
POLL_BOT_TOKEN = os.getenv('POLL_BOT_TOKEN')
TG_CHAT_IDS = json.loads(os.getenv('TG_CHAT_IDS'))
OFFICE_CHAT_IDS = json.loads(os.getenv('OFFICE_CHAT_IDS'))

async def go_to_office_poll(context: ContextTypes.DEFAULT_TYPE) -> None:
    question = "Когда в офис? 💜"
    options = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Не на этой неделе"]
    message = await context.bot.send_poll(chat_id=context.job.chat_id, question=question, options=options, is_anonymous=False, allows_multiple_answers=True)
    await context.bot.pin_chat_message(
            chat_id=context.job.chat_id,
            message_id=message.message_id,
        )

async def send_training_poll(context: ContextTypes.DEFAULT_TYPE) -> None:
    question = "Тренировка завтра"
    options = ["Буду", "Не буду", "50/50"]
    message = await context.bot.send_poll(chat_id=context.job.chat_id, question=question, options=options, is_anonymous=False)
    await context.bot.pin_chat_message(
            chat_id=context.job.chat_id,
            message_id=message.message_id,
        )

async def send_game_training_poll(context: ContextTypes.DEFAULT_TYPE) -> None:
    question = "Игровая завтра"
    options = ["Буду", "Не буду", "50/50"]
    message = await context.bot.send_poll(chat_id=context.job.chat_id, question=question, options=options, is_anonymous=False)
    await context.bot.pin_chat_message(
            chat_id=context.job.chat_id,
            message_id=message.message_id,
        )


async def send_lchb_poll(context: ContextTypes.DEFAULT_TYPE) -> None:
    options = ["Буду", "Не буду", "50/50"]
    message = await context.bot.send_poll(chat_id=context.job.chat_id, question=context.job.data["question"], options=options, is_anonymous=False)
    await context.bot.pin_chat_message(
            chat_id=context.job.chat_id,
            message_id=message.message_id,
        )
def set_office_polls(application, chat_id) -> None:

    application.job_queue.run_daily(
        callback=go_to_office_poll,
        time=time(15, 0, 0, 0),
        days = (5,),
        chat_id=chat_id,
        name=str("Опрос по офису"),
    )

def set_polls(application, chat_id, games) -> None:

    application.job_queue.run_daily(
        callback=send_training_poll,
        time=time(9, 0, 0, 0),
        days = (0,),
        chat_id=chat_id,
        name=str("Опрос по треням в ПН"),
    )

    application.job_queue.run_daily(
        callback=send_game_training_poll,
        time=time(9, 0, 0, 0),
        days = (2,),
        chat_id=chat_id,
        name=str("Опрос по игровым в СР"),
    )

    for game in games:
         application.job_queue.run_once(
            callback=send_lchb_poll,
            when=game["poll_date"],
            data=game,
            chat_id=chat_id,
            name=str(game["question"]),
        )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Привет! Это бот опросник"
    )

async def test(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    question = "current chat id: " + str(update.message.chat_id)
    options = ["answer 1", "answer 2", "answer 3"]
    message = await context.bot.send_poll(chat_id=update.message.chat_id, question=question, options=options, is_anonymous=False)
    await context.bot.pin_chat_message(
        chat_id=update.message.chat_id,
        message_id=message.message_id,
    )

async def schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    job_names = [job.name for job in context.job_queue.jobs()]

    text = ""
    for k in range(len(job_names)):
        text += "{}\n\n".format(job_names[k])
    await update.message.reply_text(
        text
    )

def main() -> None:

    if not POLL_BOT_TOKEN:
        raise RuntimeError("POLL_BOT_TOKEN is not configured")

    if TG_CHAT_IDS is None:
        raise RuntimeError("TG_CHAT_IDS is not configured")

    if OFFICE_CHAT_IDS is None:
        raise RuntimeError("OFFICE_CHAT_IDS is not configured")

    games = [
            { "question": "Тур 5 (Сб) 18.10.2025	21:00\nПлощадка №3\nEndorphin Group	-	Авито", "poll_date": datetime(2025, 10, 16, 8)},
    ]

    application = Application.builder().token(POLL_BOT_TOKEN).build()

    for chat_id in TG_CHAT_IDS:
        set_polls(application, chat_id, games)
    
    for chat_id in OFFICE_CHAT_IDS:
        set_office_polls(application, chat_id)
    
    application.add_handler(CommandHandler(["start", "help", "ping"], start))
    application.add_handler(CommandHandler(["test"], test))
    application.add_handler(CommandHandler(["schedule"], schedule))

    application.run_polling()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    main()
