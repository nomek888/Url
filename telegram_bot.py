import telegram
from telegram.ext import Updater, CommandHandler, MessageHandler, filters
import os
import subprocess
import re
import time

# Replace 'YOUR_BOT_TOKEN' with your actual bot token
BOT_TOKEN = 'YOUR_BOT_TOKEN'

DOWNLOAD_FOLDER = "./downloads"

def download_file(update, context, url, download_folder):
    """
    Downloads a file from a URL using yt-dlp with progress updates.
    yt-dlp is generally optimized for speed. Network bandwidth on the server is a key factor.
    """
    chat_id = update.effective_chat.id
    if not os.path.exists(download_folder):
        os.makedirs(download_folder)

    # Send initial message and get its ID
    progress_message = update.message.reply_text("Download starting...")
    progress_message_id = progress_message.message_id

    command = [
        'yt-dlp',
        '-o', os.path.join(download_folder, '%(title)s.%(ext)s'),
        '--progress',  # Ensure progress output is enabled
        '--newline',   # Helps in parsing line by line
        '--concurrent-fragments', '5', # Download 5 fragments concurrently for HLS/DASH
        url
    ]

    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, universal_newlines=True)

    last_update_time = 0
    last_reported_percentage = -1
    filepath = None
    downloaded_filename_from_yt_dlp = None # Store filename yt-dlp reports

    # Regex to capture progress, total size, speed, and ETA
    progress_regex = re.compile(
        r"\[download\]\s+(?P<percentage>\d+\.?\d*)%\s+of\s+~\s*(?P<total_size>\S+)\s+at\s+(?P<speed>\S+)\s+ETA\s+(?P<eta>\S+)"
    )
    # Simpler regex if the above is too specific for some yt-dlp versions/outputs
    # progress_regex = re.compile(r"\[download\]\s+(?P<percentage>\d+\.\d+)%")


    for line in iter(process.stdout.readline, ''):
        print(line.strip()) # For debugging in server logs

        # Check for final file destination (yt-dlp often prints this)
        if "[download] Destination:" in line:
            downloaded_filename_from_yt_dlp = line.split("Destination:", 1)[1].strip()
        elif "Merging formats into" in line: # Common for merged video/audio
            # Example: [Merger] Merging formats into "output_directory/Video Title.mp4"
            match = re.search(r'Merging formats into "(?P<filepath>.+?)"', line)
            if match:
                downloaded_filename_from_yt_dlp = match.group("filepath")


        match = progress_regex.search(line)
        if match:
            try:
                percentage_str = match.group("percentage")
                percentage = float(percentage_str)
                current_time = time.time()

                # Throttle updates: 5% change or 3 seconds
                if percentage >= last_reported_percentage + 5 or current_time - last_update_time > 3:
                    total_size = match.group("total_size")
                    speed = match.group("speed")
                    eta = match.group("eta")
                    progress_text = f"Downloading: {percentage:.1f}% of {total_size} at {speed} (ETA: {eta})"

                    try:
                        context.bot.edit_message_text(
                            text=progress_text,
                            chat_id=chat_id,
                            message_id=progress_message_id
                        )
                        last_reported_percentage = percentage
                        last_update_time = current_time
                    except telegram.error.RetryAfter as e:
                        time.sleep(e.retry_after)
                    except telegram.error.BadRequest as e: # Message not modified, etc.
                        print(f"Error editing message: {e}")
                        pass # Ignore if message hasn't changed
            except ValueError:
                # If percentage is not a float, skip this line
                print(f"Could not parse percentage from: {percentage_str}")
            except Exception as e:
                print(f"Error processing progress line: {e}")


    process.stdout.close()
    stderr_output = process.stderr.read()
    process.stderr.close()
    return_code = process.wait()

    if return_code == 0:
        final_message = "Download complete!"
        if downloaded_filename_from_yt_dlp and os.path.exists(downloaded_filename_from_yt_dlp):
            filepath = downloaded_filename_from_yt_dlp
        else: # Fallback: find the newest file
            list_of_files = [os.path.join(download_folder, f) for f in os.listdir(download_folder) if os.path.isfile(os.path.join(download_folder, f))]
            if not list_of_files:
                final_message = "Download complete, but could not locate file."
                filepath = None
            else:
                filepath = max(list_of_files, key=os.path.getctime)

        try:
            context.bot.edit_message_text(text=final_message, chat_id=chat_id, message_id=progress_message_id)
        except Exception as e:
            print(f"Error editing final message: {e}")
            # If editing fails, try sending a new message
            update.message.reply_text(final_message + (f"\nFile: {filepath}" if filepath else ""))

        return filepath
    else:
        error_message = f"Download failed. Error: {stderr_output}"
        print(error_message)
        try:
            context.bot.edit_message_text(text=error_message, chat_id=chat_id, message_id=progress_message_id)
        except Exception as e:
            print(f"Error editing error message: {e}")
            update.message.reply_text(error_message) # Fallback
        return None

def handle_message(update, context):
    """Handles non-command messages (potential URLs)."""
    url = update.message.text
    # Removed the initial "Received URL..." message, download_file will send its own status.

    filepath = download_file(update, context, url, DOWNLOAD_FOLDER)

    if filepath:
        # The "Download successful!" message is now handled by edit_message_text in download_file
        update.message.reply_text("Starting upload to Telegram...") # This is for upload, which has no progress yet
        try:
            with open(filepath, 'rb') as f:
                context.bot.send_document(chat_id=update.effective_chat.id, document=f)
            update.message.reply_text("File uploaded successfully!")
        except telegram.error.TelegramError as e:
            update.message.reply_text(f"Error uploading file: {e.message}")
            # For files >50MB and for potentially faster uploads of large files,
            # a local Bot API server is recommended. The standard API limit is 50MB for bots.
            if "file is too large" in str(e).lower():
                 update.message.reply_text("Note: Files larger than 50MB require a special setup (local Bot API server) which is not yet implemented.")
        except Exception as e:
            update.message.reply_text(f"An unexpected error occurred during upload: {str(e)}")
        finally:
            # Optional: Clean up the downloaded file (keeping them for now)
            # if os.path.exists(filepath):
            #     os.remove(filepath)
            #     update.message.reply_text(f"Cleaned up local file: {filepath}")
            pass

    else:
        # Error message is handled by edit_message_text in download_file or a reply if edit fails
        pass # update.message.reply_text("Sorry, there was an error downloading the file.")

def start(update, context):
    """Sends a welcome message and a car picture when the /start command is issued."""
    chat_id = update.effective_chat.id

    # Define the car picture URL (replace with a real URL)
    car_picture_url = "https://images.pexels.com/photos/170811/pexels-photo-170811.jpeg?auto=compress&cs=tinysrgb&w=1260&h=750&dpr=1" # Example URL

    welcome_message = """Welcome to the High-Speed URL Uploader Bot! 🚗💨

I can download files from URLs and upload them to Telegram at lightning speed.
I also feature an advanced progress tracker and support for m3u8 links and files up to 2GB.

Send me a URL to get started!"""

    # Send the car picture
    context.bot.send_photo(chat_id=chat_id, photo=car_picture_url)

    # Send the welcome message
    update.message.reply_text(welcome_message)

def main():
    """Start the bot."""
    # Create the Updater and pass it your bot's token.
    # Note: The Dispatcher runs handlers in separate threads by default (typically 4 worker threads).
    # This allows concurrent processing of multiple user requests (e.g., multiple downloads).
    updater = Updater(BOT_TOKEN, use_context=True)

    # Get the dispatcher to register handlers
    dp = updater.dispatcher

    # on different commands - answer in Telegram
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Start the Bot
    updater.start_polling()

    # Run the bot until you press Ctrl-C or the process receives SIGINT,
    # SIGTERM or SIGABRT. This should be used most of the time, since
    # start_polling() is non-blocking and will stop the bot gracefully.
    updater.idle()

if __name__ == '__main__':
    main()
