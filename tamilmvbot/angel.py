import os
import time
import logging
import requests
import telebot
from flask import Flask, request
from telebot import types
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from cachetools import TTLCache

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

if not TOKEN:
    raise RuntimeError("TOKEN is missing!")

if not WEBHOOK_URL:
    raise RuntimeError("WEBHOOK_URL is missing!")

# ============================================================
# CACHE
# ============================================================

cache = TTLCache(maxsize=100, ttl=600)  # 10 minutes

# ============================================================
# TELEGRAM BOT
# ============================================================

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")

# ============================================================
# FLASK APP
# ============================================================

app = Flask(__name__)

# Global variables
movie_list = []
real_dict = {}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
}

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
    wait_message = bot.send_message(chat_id, "<b>⏳ Fetching movies... Please wait</b>")

    global movie_list, real_dict

    try:
        cache_key = "movies_data"
        if cache_key in cache:
            logger.info("Using cached movies")
            movie_list, real_dict = cache[cache_key]
        else:
            logger.info("Fetching fresh movies")
            movie_list, real_dict = get_movies()
            if movie_list:
                cache[cache_key] = (movie_list, real_dict)
            
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
        f"📊 Total: {len(movie_list)} movies"
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
    markup = types.InlineKeyboardMarkup(row_width=2)
    buttons = []
    for index, title in enumerate(movies[:30]):  # Limit to 30
        buttons.append(types.InlineKeyboardButton(
            text=title[:30],
            callback_data=str(index)
        ))
    markup.add(*buttons)
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
        
        if len(temps) < 20:
            logger.warning("Not enough movies found on the page")
            return [], {}
        
        for i in range(min(25, len(temps))):
            try:
                title = temps[i].findAll('a')[0].text.strip()
                link = temps[i].find('a')['href']
                movie_list.append(title)
                
                # Fetch details for each movie
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
        
        if mag:
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
        else:
            # Try torrent links
            download_links = soup.find_all('a', {'data-fileext': 'torrent'})
            if download_links:
                for link in download_links[:3]:
                    torrent_link = link.get('href')
                    if torrent_link and not torrent_link.startswith('http'):
                        torrent_link = f'{TAMILMV_URL}{torrent_link}'
                    
                    message = f"""
<b>📂 Movie Title:</b>
<blockquote>{movie_title}</blockquote>

📥 <b>Download Torrent:</b>
<a href="{torrent_link}">🔗 Click Here</a>

⚠️ <b>Note:</b> Magnet link not available
"""
                    movie_details.append(message)
            
        return movie_details
        
    except Exception as e:
        logger.error(f"Error retrieving movie details from {url}: {e}")
        return []

# ============================================================
# WEBHOOK
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
# SET WEBHOOK (Runs on Vercel deployment)
# ============================================================

def set_webhook():
    """Set webhook on startup"""
    try:
        webhook_url = f"{WEBHOOK_URL}/webhook"
        bot.remove_webhook()
        time.sleep(1)
        bot.set_webhook(url=webhook_url)
        logger.info(f"Webhook set to: {webhook_url}")
        return True
    except Exception as e:
        logger.error(f"Failed to set webhook: {e}")
        return False

# ============================================================
# MAIN - Vercel uses this
# ============================================================

# Set webhook when app starts
set_webhook()

if __name__ == "__main__":
    # For local testing only
    port = int(os.getenv("PORT", 3000))
    app.run(host="0.0.0.0", port=port)