import os
import time
import logging
import threading

import requests
import telebot

from dotenv import load_dotenv
from flask import Flask
from telebot import types
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup


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
PORT = int(os.getenv("PORT", "3000"))
TAMILMV_URL = os.getenv("TAMILMV_URL", "https://www.1tamilmv.boo")

if not TOKEN:
    raise RuntimeError(
        "TOKEN is missing. Add TOKEN=... to your .env file."
    )


# ============================================================
# TELEGRAM BOT
# ============================================================

bot = telebot.TeleBot(
    TOKEN,
    parse_mode="HTML",
    threaded=False
)


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


@app.route("/")
def health_check():
    return "Angel Bot Healthy", 200


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()

retry = Retry(
    total=3,
    connect=3,
    read=3,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"]
)

adapter = HTTPAdapter(max_retries=retry)

session.mount("https://", adapter)
session.mount("http://", adapter)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
}


# ============================================================
# GLOBAL MOVIE DATA
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
        types.InlineKeyboardButton(
            "🔗 GitHub 🔗",
            url="https://github.com/SudoR2spr"
        ),
        types.InlineKeyboardButton(
            "⚡ Powered By",
            url="https://t.me/Opleech_WD"
        )
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

        bot.send_message(
            message.chat.id,
            text_message,
            reply_markup=keyboard
        )


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
            "<b>❌ Movie list fetch failed.</b>\n\n"
            "Please try again later.",
            chat_id=chat_id,
            message_id=wait_message.message_id
        )

        return

    if not movie_list:

        bot.edit_message_text(
            "<b>❌ No movies found.</b>\n\n"
            "Please try again later.",
            chat_id=chat_id,
            message_id=wait_message.message_id
        )

        return

    try:
        bot.delete_message(
            chat_id,
            wait_message.message_id
        )
    except Exception:
        pass

    combined_caption = (
        "<b><blockquote>"
        "🔗 Select a Movie from the list 🎬"
        "</blockquote></b>\n\n"
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

        bot.send_message(
            chat_id,
            combined_caption,
            reply_markup=keyboard
        )


# ============================================================
# CALLBACK
# ============================================================

@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):

    global real_dict

    try:
        index = int(call.data)

    except ValueError:
        bot.answer_callback_query(
            call.id,
            "Invalid selection."
        )
        return

    if index < 0 or index >= len(movie_list):

        bot.answer_callback_query(
            call.id,
            "Movie not found."
        )

        return

    title = movie_list[index]

    details = real_dict.get(title, [])

    bot.answer_callback_query(
        call.id
    )

    if not details:

        bot.send_message(
            call.message.chat.id,
            "<b>❌ Details are not available.</b>"
        )

        return

    for text in details:

        try:
            bot.send_message(
                call.message.chat.id,
                text
            )

        except Exception as e:
            logger.exception(
                "Failed to send movie details: %s",
                e
            )


# ============================================================
# KEYBOARD
# ============================================================

def make_keyboard(movies):

    markup = types.InlineKeyboardMarkup()

    for index, title in enumerate(movies):

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
    
    mainUrl = TAMILMV_URL
    movie_list = []
    real_dict = {}
    
    try:
        response = session.get(
            mainUrl,
            headers=HEADERS,
            timeout=(10, 30)
        )
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'lxml')
        
        temps = soup.find_all('div', {'class': 'ipsType_break ipsContained'})
        
        if len(temps) < 25:
            logger.warning("Not enough movies found on the page")
            return [], {}
        
        for i in range(25):
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
            
        response = session.get(
            url,
            headers=HEADERS,
            timeout=(10, 15)
        )
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'lxml')
        
        mag = [a['href'] for a in soup.find_all('a', href=True) if 'magnet:' in a['href']]
        filelink = [a['href'] for a in soup.find_all('a', {"data-fileext": "torrent", 'href': True})]
        
        movie_details = []
        movie_title = soup.find('h1').text.strip() if soup.find('h1') else "Unknown Title"
        
        if not mag:
            # No magnet links found, try alternative method
            try:
                # Try to find download links
                download_links = soup.find_all('a', {'data-fileext': 'torrent'})
                if download_links:
                    for link in download_links:
                        torrent_link = link.get('href')
                        if torrent_link and not torrent_link.startswith('http'):
                            torrent_link = f'{TAMILMV_URL}{torrent_link}'
                        
                        message = f"""
<b>📂 Movie Title:</b>
<blockquote>{movie_title}</blockquote>

📥 <b>Download Torrent:</b>
<a href="{torrent_link}">🔗 Click Here</a>

<b>⚠️ Note:</b> Magnet link not available for this movie.
"""
                        movie_details.append(message)
            except Exception:
                pass
        
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
# FLASK SERVER
# ============================================================

def run_flask():

    logger.info(
        "Starting Flask health server on port %s",
        PORT
    )

    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False
    )


# ============================================================
# TELEGRAM POLLING
# ============================================================

def run_bot():

    logger.info(
        "Removing previous Telegram webhook..."
    )

    try:
        bot.remove_webhook()
        time.sleep(1)

    except Exception as e:

        logger.warning(
            "Could not remove webhook: %s",
            e
        )

    logger.info(
        "Starting Telegram bot polling..."
    )

    while True:

        try:

            bot.infinity_polling(
                skip_pending=True,
                timeout=30,
                long_polling_timeout=30
            )

        except Exception as e:

            logger.exception(
                "Telegram polling error: %s",
                e
            )

            logger.info(
                "Restarting polling in 5 seconds..."
            )

            time.sleep(5)


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    # Flask health server runs in background
    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True
    )

    flask_thread.start()

    # Telegram bot
    run_bot()