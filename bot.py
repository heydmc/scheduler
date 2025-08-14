import logging
import os
import flask
import sqlite3
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    PersistenceInput,
    PicklePersistence,
)



# --- FLASK APP FOR RENDER HEALTH CHECK ---
app = Flask(__name__)

@app.route("/")
def home():
    return "Quiz Bot is running!"

# Function to start Flask in a separate thread
def start_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)




# --- Load environment variables from .env file ---
load_dotenv()

# --- Configuration ---
# You'll need a NEW token for this scheduler bot
BOT_TOKEN = os.getenv("SCHEDULER_BOT_TOKEN") 
# This should be the SAME admin ID as your main bot
ADMIN_USERID = os.getenv("ADMIN_USERID") 
DB_FILE = "scheduler_jobs.db"

# --- Set up logging ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Database Functions ---

def init_db():
    """Initializes the database and creates the schedules table if it doesn't exist."""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schedules (
                job_id TEXT PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                command_to_send TEXT NOT NULL,
                details_command_to_send TEXT NOT NULL,
                run_at DATETIME NOT NULL
            )
        """)
        conn.commit()
    logger.info("Database initialized.")

def add_schedule_to_db(job_id, chat_id, command, details_command, run_at):
    """Adds a new scheduled job to the database."""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO schedules (job_id, chat_id, command_to_send, details_command_to_send, run_at) VALUES (?, ?, ?, ?, ?)",
            (job_id, chat_id, command, details_command, run_at)
        )
        conn.commit()
    logger.info(f"Added job {job_id} to DB.")

def remove_schedule_from_db(job_id):
    """Removes a completed or cancelled job from the database."""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM schedules WHERE job_id = ?", (job_id,))
        conn.commit()
    logger.info(f"Removed job {job_id} from DB.")

def get_pending_schedules():
    """Retrieves all schedules from the DB that have not yet run."""
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        # Get jobs that are supposed to run in the future
        cursor.execute("SELECT * FROM schedules WHERE run_at > ?", (datetime.now(timezone.utc),))
        return cursor.fetchall()

# --- Bot Functions ---

async def alarm(context: ContextTypes.DEFAULT_TYPE) -> None:
    """The function called by the job queue to send the scheduled message."""
    job = context.job
    
    # Send the two required messages back to the admin
    await context.bot.send_message(job.chat_id, text=f"🔔 *Reminder from Scheduler Bot* 🔔\n\nThe following plan has expired\\. Forward the commands below to your main bot to process it\\.", parse_mode='MarkdownV2')
    await context.bot.send_message(job.chat_id, text=job.data["command"])
    await context.bot.send_message(job.chat_id, text=job.data["details_command"])
    
    # Clean up the database
    remove_schedule_from_db(job.data["job_id"])
    logger.info(f"Executed and removed job {job.data['job_id']}.")

async def schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /schedule command to set a new reminder."""
    user_id = update.effective_user.id

    # --- Security Check: Only the admin can use this bot ---
    if str(user_id) != ADMIN_USERID:
        logger.warning(f"Unauthorized user {user_id} tried to use the scheduler.")
        return

    try:
        # Expected format: /schedule <seconds> /freecredential <target_user_id>
        if len(context.args) != 3:
            raise ValueError("Incorrect number of arguments.")
            
        delay_seconds = int(context.args[0])
        command_name = context.args[1]
        target_user_id = context.args[2]

        if not command_name == "/freecredential" or not target_user_id.isdigit():
             raise ValueError("Invalid command format.")

        # Reconstruct the commands to be sent later
        command_to_send = f"/freecredential {target_user_id}"
        details_command_to_send = f"/seedetails {target_user_id}"
        
        # Unique ID for the job and DB entry
        job_id = str(uuid4())
        
        # Add the job to the bot's job queue
        context.job_queue.run_once(
            alarm,
            when=delay_seconds,
            data={
                "chat_id": user_id,
                "command": command_to_send,
                "details_command": details_command_to_send,
                "job_id": job_id
            },
            name=job_id,
        )
        
        # Store job in DB for persistence
        run_at_time = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
        add_schedule_to_db(job_id, user_id, command_to_send, details_command_to_send, run_at_time)
        
        # Confirm to the admin
        await update.message.reply_text(f"✅ Understood! I will remind you in {timedelta(seconds=delay_seconds)}.")

    except (IndexError, ValueError) as e:
        logger.error(f"Error parsing schedule command: {e}")
        await update.message.reply_text(
            "Invalid format. Please use the format you get from the main bot:\n"
            "`/schedule <seconds> /freecredential <user_id>`"
        )

def main() -> None:

    """Start Flask in a thread and the bot using polling."""
    # Start Flask server in a background thread
    threading.Thread(target=start_flask, daemon=True).start()
    """Starts the bot, initializes the DB, and reschedules jobs on restart."""
    if not BOT_TOKEN or not ADMIN_USERID:
        logger.critical("!!! ERROR: SCHEDULER_BOT_TOKEN or ADMIN_USERID not found in .env file. !!!")
        return

    # Initialize the database
    init_db()

    # Build the application
    application = Application.builder().token(BOT_TOKEN).build()

    # --- Load pending jobs from DB on restart ---
    job_queue = application.job_queue
    pending_jobs = get_pending_schedules()
    restored_count = 0
    now = datetime.now(timezone.utc)
    
    for job_data in pending_jobs:
        job_id = job_data["job_id"]
        run_at = datetime.fromisoformat(job_data["run_at"]).replace(tzinfo=timezone.utc)
        delay = (run_at - now).total_seconds()
        
        # If the bot was down past the run time, run immediately (delay=0)
        if delay < 0:
            delay = 0

        job_queue.run_once(
            alarm,
            when=delay,
            data={
                "chat_id": job_data["chat_id"],
                "command": job_data["command_to_send"],
                "details_command": job_data["details_command_to_send"],
                "job_id": job_id
            },
            name=job_id
        )
        restored_count += 1
    
    if restored_count > 0:
        logger.info(f"Restored {restored_count} pending jobs from the database.")

    # Add command handlers
    application.add_handler(CommandHandler("schedule", schedule))
    application.add_handler(CommandHandler("start", schedule)) # Can also use /start

    logger.info("Scheduler bot is running...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":

    main()

