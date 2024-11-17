from datetime import datetime, timezone, time
from telegram.ext import Application, ContextTypes, CommandHandler
from telegram import Update
import json
from dotenv import load_dotenv, find_dotenv
import os


load_dotenv(find_dotenv())
POLL_BOT_TOKEN = os.getenv('POLL_BOT_TOKEN')
TG_CHAT_IDS = json.loads(os.getenv('TG_CHAT_IDS'))

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
    question = "test"
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

def run_forever():
    if POLL_BOT_TOKEN == None or TG_CHAT_IDS == None:
        print("can't get envs, check .env file")
    try:
        games = [
                { "question": "23.11.2024 (Сб) - 18:20\nПлощадка №3\nКомус - Авито", "poll_date": datetime(2024, 11, 21, 8)},
                { "question": "07.12.2024 (Сб) - 19:40\nПлощадка №3\nАвито - Совкомбанк", "poll_date": datetime(2024, 12, 5, 8)},
                { "question": "14.12.2024 (Сб) - 21:00\nПлощадка №1\nАвито - СберЛизинг", "poll_date": datetime(2024, 12, 12, 8)},
        ]

        application = Application.builder().token(POLL_BOT_TOKEN).build()
        for chat_id in TG_CHAT_IDS:
            set_polls(application, chat_id, games)
        application.add_handler(CommandHandler(["start", "help", "ping"], start))
        application.add_handler(CommandHandler(["test"], test))
        application.add_handler(CommandHandler(["schedule"], schedule))
        application.run_polling()
    except Exception as e:
            print("Something crashed your program. Let's restart it:")
            print(e)
            run_forever() # Careful.. recursive behavior

run_forever()
