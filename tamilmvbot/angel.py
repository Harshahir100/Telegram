import os
import time
import logging
import requests
import telebot
from flask import Flask, request
from telebot import types
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ============================================================
# ENV
# ============================================================

load_dotenv()

TOKEN = os.getenv("TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
TAMILMV_URL = os.getenv("TAMILMV_URL", "https://www.1tamilmv.boo")
PORT = int(os.getenv("PORT", 3000))

if not TOKEN:
    raise RuntimeError("TOKEN is missing!")

# ============================================================
# TELEGRAM BOT
# ============================================================

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")

# ============================================================
# FLASK APP
# ============================================================

app = Flask(__name__)

# ============================================================
# HEADERS
# ============================================================

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# ============================================================
# GLOBAL VARIABLES
# ============================================================

movie_list = []
real_dict = {}

# ============================================================
# /start
# ============================================================

@bot.message_handler(commands=["start"])
def random_answer(message):
    text_message = """<b>Hello 👋</b>

<blockquote><b>🎬 Welcome to Angel Bot</b></blockquote>

⚙️ <b>How to use me?</b>

✯ Send /view to see the available movies.

<blockquote><b>🔗 Share and Support 💝</b></blockquote>
"""

    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(
        types.InlineKeyboardButton("🔗 GitHub 🔗", url="https://github.com/SudoR2spr"),
        types.InlineKeyboardButton("⚡ Powered By", url="https://t.me/Opleech_WD")
    )

    try:
        bot.send_photo(
            chat_id=message.chat.id,
            photo="https://graph.org/file/4e8a1172e8ba4b7a0bdfa.jpg",
            caption=text_message,
            reply_markup=keyboard
        )
    except Exception as e:
        logger.exception("Failed to send /start response: %s", e)
        bot.send_message(message.chat.id, text_message, reply_markup=keyboard)

# ============================================================
# /view
# ============================================================

@bot.message_handler(commands=["view"])
def start(message):
    chat_id = message.chat.id

    wait_message = bot.send_message(
        chat_id,
        "<b>🧲 Please wait for 10 ⏰ seconds</b>"
    )

    global movie_list, real_dict

    try:
        movie_list, real_dict = get_movies()
    except Exception as e:
        logger.exception("Movie fetch failed: %s", e)
        bot.edit_message_text(
            "<b>❌ Movie list fetch failed.</b>\n\nPlease try again later.",
            chat_id=chat_id,
            message_id=wait_message.message_id
        )
        return

    if not movie_list:
        bot.edit_message_text(
            "<b>❌ No movies found.</b>\n\nPlease try again later.",
            chat_id=chat_id,
            message_id=wait_message.message_id
        )
        return

    try:
        bot.delete_message(chat_id, wait_message.message_id)
    except Exception:
        pass

    combined_caption = (
        "<b><blockquote>🔗 Select a Movie from the list 🎬</blockquote></b>\n\n"
        "🔘 Please select a movie:"
    )

    keyboard = make_keyboard(movie_list)

    try:
        bot.send_photo(
            chat_id=chat_id,
            photo="https://graph.org/file/4e8a1172e8ba4b7a0bdfa.jpg",
            caption=combined_caption,
            reply_markup=keyboard
        )
    except Exception as e:
        logger.exception("Failed to send movie list: %s", e)
        bot.send_message(chat_id, combined_caption, reply_markup=keyboard)

# ============================================================
# CALLBACK
# ============================================================

@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    global real_dict

    try:
        index = int(call.data)
    except ValueError:
        bot.answer_callback_query(call.id, "Invalid selection.")
        return

    if index < 0 or index >= len(movie_list):
        bot.answer_callback_query(call.id, "Movie not found.")
        return

    title = movie_list[index]
    details = real_dict.get(title, [])
    bot.answer_callback_query(call.id)

    if not details:
        bot.send_message(call.message.chat.id, "<b>❌ Details are not available.</b>")
        return

    for text in details:
        try:
            bot.send_message(call.message.chat.id, text)
        except Exception as e:
            logger.exception("Failed to send movie details: %s", e)

# ============================================================
# KEYBOARD
# ============================================================

def make_keyboard(movies):
    markup = types.InlineKeyboardMarkup()
    for index, title in enumerate(movies[:20]):  # Limit to 20
        markup.add(
            types.InlineKeyboardButton(
                text=title[:64],
                callback_data=str(index)
            )
        )
    return markup

# ============================================================
# MOVIE SCRAPER
# ============================================================

def get_movies():
    """Scrape movies from 1TamilMV"""
    
    if not TAMILMV_URL:
        logger.warning("TAMILMV_URL is not configured.")
        return [], {}
    
    movie_list = []
    real_dict = {}
    
    try:
        response = requests.get(TAMILMV_URL, headers=HEADERS, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        temps = soup.find_all('div', {'class': 'ipsType_break ipsContained'})
        
        if len(temps) < 10:
            logger.warning("Not enough movies found on the page")
            return [], {}
        
        # Limit to 15 movies for speed
        for i in range(min(15, len(temps))):
            try:
                title = temps[i].findAll('a')[0].text.strip()
                link = temps[i].find('a')['href']
                movie_list.append(title)
                
                movie_details = get_movie_details(link)
                real_dict[title] = movie_details
                
            except Exception as e:
                logger.error(f"Error processing movie {i}: {e}")
                continue
            
        return movie_list, real_dict
        
    except Exception as e:
        logger.error(f"Error in get_movies: {e}")
        return [], {}

def get_movie_details(url):
    """Get movie details from URL"""
    try:
        if not url.startswith('http'):
            url = f'{TAMILMV_URL}{url}'
            
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        mag = [a['href'] for a in soup.find_all('a', href=True) if 'magnet:' in a['href']]
        filelink = [a['href'] for a in soup.find_all('a', {"data-fileext": "torrent", 'href': True})]
        
        movie_details = []
        movie_title = soup.find('h1')
        movie_title = movie_title.text.strip() if movie_title else "Unknown Title"
        
        for p in range(len(mag)):
            torrent_link = filelink[p] if p < len(filelink) else None
            if torrent_link and not torrent_link.startswith('http'):
                torrent_link = f'{TAMILMV_URL}{torrent_link}'
            
            message = f"""
<b>📂 Movie Title:</b>
<blockquote>{movie_title}</blockquote>

🧲 <b>Magnet Link:</b>
<pre>{mag[p]}</pre>
"""
            if torrent_link:
                message += f"""
📥 <b>Download Torrent:</b>
<a href="{torrent_link}">🔗 Click Here</a>
"""
            else:
                message += """
📥 <b>Torrent File:</b> Not Available
"""
            
            movie_details.append(message)
            
        return movie_details
        
    except Exception as e:
        logger.error(f"Error retrieving movie details from {url}: {e}")
        return []

# ============================================================
# WEBHOOK ROUTES
# ============================================================

@app.route("/", methods=["GET"])
def index():
    return "Angel Bot is running!", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        if request.headers.get("content-type") != "application/json":
            return "Invalid content type", 403

        json_str = request.get_data().decode("utf-8")
        update = telebot.types.Update.de_json(json_str)
        bot.process_new_updates([update])
        return "OK", 200
    except Exception as e:
        logger.exception(f"Webhook error: {e}")
        return "Webhook error", 500

# ============================================================
# SET WEBHOOK
# ============================================================

def set_webhook():
    """Set webhook for Vercel deployment"""
    try:
        # Remove existing webhook
        bot.remove_webhook()
        time.sleep(1)
        
        # Only set webhook if WEBHOOK_URL is provided
        if WEBHOOK_URL:
            webhook_url = f"{WEBHOOK_URL}/webhook"
            bot.set_webhook(url=webhook_url)
            logger.info(f"✅ Webhook set to: {webhook_url}")
            return True
        else:
            logger.warning("WEBHOOK_URL not set, using polling mode")
            return False
    except Exception as e:
        logger.error(f"❌ Webhook setup failed: {e}")
        return False

# ============================================================
# MAIN - Works for both Local and Vercel
# ============================================================

if __name__ == "__main__":
    # For local development - use polling
    logger.info("Running in local mode with polling")
    bot.remove_webhook()
    bot.polling(non_stop=True)

# For Vercel - webhook is set at module level
# This runs when Vercel loads the app
else:
    # Set webhook for Vercel
    set_webhook()