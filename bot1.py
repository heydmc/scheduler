import logging
import os
import asyncio  # <-- Import asyncio
from datetime import timedelta

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# --- Load environment variables from .env file ---
load_dotenv()

# --- Configuration ---
BOT_TOKEN = os.getenv("REMINDER_BOT_TOKEN") # Use a new token or the same scheduler token

# --- Set up logging ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Bot Functions ---

def alarm(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends the reminder message."""
    context.bot.send_message(
        chat_id=context.job.chat_id,
        text=f"🔔 Reminder: {context.job.data}"
    )
    logger.info(f"Sent reminder to {context.job.chat_id}: {context.job.data}")


async def set_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sets a new reminder for the user."""
    chat_id = update.effective_message.chat_id
    try:
        # Expected format: /set <seconds> <message>
        if len(context.args) < 2:
            await update.effective_message.reply_text("Usage: /set <seconds> <your message>")
            return

        delay = int(context.args[0])
        if delay < 1:
            await update.effective_message.reply_text("Sorry, the delay must be at least 1 second.")
            return

        # The reminder text is everything after the delay number
        reminder_text = " ".join(context.args[1:])

        # Schedule the job (job_queue methods are sync)
        context.application.job_queue.run_once(
            alarm, delay, data=reminder_text, chat_id=chat_id
        )

        # Confirm to the user
        confirmation_message = f"✅ Got it! I will remind you in {timedelta(seconds=delay)}."
        await update.effective_message.reply_text(confirmation_message)
        logger.info(f"Set reminder for {chat_id} in {delay} seconds.")

    except (IndexError, ValueError):
        await update.effective_message.reply_text("Usage: /set <seconds> <your message>")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message."""
    await update.message.reply_text(
        "Hi! I'm a simple reminder bot.\n\n"
        "Use me like this:\n`/set 300 Go to the meeting`\n\n"
        "This will send you a reminder in 300 seconds (5 minutes)."
    )


async def main() -> None:  # <-- Changed to async def
    """Starts the bot."""
    if not BOT_TOKEN:
        logger.critical("!!! ERROR: REMINDER_BOT_TOKEN not found in .env file. !!!")
        return

    # Create the Application and pass it your bot's token.
    application = Application.builder().token(BOT_TOKEN).build()

    # on different commands - answer in Telegram
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", start))
    application.add_handler(CommandHandler("set", set_reminder))

    # Run the bot until the user presses Ctrl-C
    logger.info("Simple Reminder Bot is running...")
    await application.run_polling() # <-- Changed to await


if __name__ == "__main__":
    asyncio.run(main()) # <-- Changed to use asyncio.run()
