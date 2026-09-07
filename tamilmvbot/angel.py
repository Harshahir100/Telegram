import os
import time
import logging
import requests
import telebot
from flask import Flask, request
from telebot import types
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed

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
    "Accept-Encoding": "gzip, deflate",
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
# /view - FAST VERSION
# ============================================================

@bot.message_handler(commands=["view"])
def start(message):
    chat_id = message.chat.id
    
    # Send initial message
    wait_message = bot.send_message(chat_id, "<b>⏳ Fetching movies... Please wait</b>")

    global movie_list, real_dict

    try:
        # Get movies with timeout protection
        movie_list, real_dict = get_movies_fast()
        
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

    for text in details[:3]:  # Limit to 3 messages
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
    for index, title in enumerate(movies[:15]):  # Only 15 movies for speed
        buttons.append(types.InlineKeyboardButton(
            text=title[:25],
            callback_data=str(index)
        ))
    if buttons:
        markup.add(*buttons)
    return markup

# ============================================================
# FAST MOVIE SCRAPER - Optimized for Vercel
# ============================================================

def get_movies_fast():
    """Fast movie scraper - optimized for Vercel 10s timeout"""
    
    if not TAMILMV_URL:
        return [], {}
    
    movie_list = []
    real_dict = {}
    
    try:
        # Quick main page fetch
        response = requests.get(TAMILMV_URL, headers=HEADERS, timeout=8)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        temps = soup.find_all('div', {'class': 'ipsType_break ipsContained'})
        
        if len(temps) < 10:
            return [], {}
        
        # Get only first 10 movies for speed
        movie_urls = []
        for i in range(min(10, len(temps))):
            try:
                title = temps[i].findAll('a')[0].text.strip()
                link = temps[i].find('a')['href']
                movie_urls.append((title, link))
                movie_list.append(title)
            except Exception as e:
                logger.error(f"Error processing movie {i}: {e}")
                continue
        
        # Fetch details in parallel with timeout
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_movie = {
                executor.submit(get_movie_details_fast, title, link): (title, link)
                for title, link in movie_urls
            }
            
            for future in as_completed(future_to_movie, timeout=8):
                title, link = future_to_movie[future]
                try:
                    details = future.result(timeout=5)
                    if details:
                        real_dict[title] = details
                except Exception as e:
                    logger.error(f"Error fetching details for {title}: {e}")
                    real_dict[title] = []
        
        return movie_list, real_dict
        
    except requests.Timeout:
        logger.error("Request timeout")
        return [], {}
    except Exception as e:
        logger.error(f"Error in get_movies_fast: {e}")
        return [], {}

def get_movie_details_fast(title, url):
    """Fast movie details fetch"""
    try:
        if not url.startswith('http'):
            url = f'{TAMILMV_URL}{url}'
            
        response = requests.get(url, headers=HEADERS, timeout=5)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Get only first magnet link
        mag = [a['href'] for a in soup.find_all('a', href=True) if 'magnet:' in a['href']]
        filelink = [a['href'] for a in soup.find_all('a', {"data-fileext": "torrent", 'href': True})]
        
        movie_title = soup.find('h1')
        movie_title = movie_title.text.strip() if movie_title else title
        
        movie_details = []
        
        if mag:
            # Only first magnet link
            torrent_link = filelink[0] if filelink else None
            if torrent_link and not torrent_link.startswith('http'):
                torrent_link = f'{TAMILMV_URL}{torrent_link}'
            
            message = f"""
<b>📂 {movie_title}</b>

🧲 <b>Magnet:</b>
<pre>{mag[0][:150]}...</pre>"""
            
            if torrent_link:
                message += f"""
📥 <a href="{torrent_link}">⬇️ Download Torrent</a>"""
            
            movie_details.append(message)
        else:
            # Try torrent only
            download_links = soup.find_all('a', {'data-fileext': 'torrent'})
            if download_links:
                torrent_link = download_links[0].get('href')
                if torrent_link and not torrent_link.startswith('http'):
                    torrent_link = f'{TAMILMV_URL}{torrent_link}'
                
                message = f"""
<b>📂 {movie_title}</b>

📥 <a href="{torrent_link}">⬇️ Download Torrent</a>"""
                movie_details.append(message)
        
        return movie_details
        
    except Exception as e:
        logger.error(f"Error for {title}: {e}")
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
# SET WEBHOOK
# ============================================================

def set_webhook():
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
# MAIN
# ============================================================

# Set webhook
set_webhook()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 3000))
    app.run(host="0.0.0.0", port=port)