import os
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
}

# ============================================================
# GLOBAL VARIABLES
# ============================================================

movie_list = []
real_dict = {}

# ============================================================
# START COMMAND
# ============================================================

@bot.message_handler(commands=['start'])
def send_welcome(message):
    text = """<b>👋 Hello! Welcome to Angel Bot</b>

🎬 <b>Get latest movies from 1Tamilmv</b>

⚙️ <b>Commands:</b>
/view - Get movie list

<b>🔗 Share and Support 💝</b>"""

    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(
        types.InlineKeyboardButton("🔗 GitHub", url="https://github.com/SudoR2spr"),
        types.InlineKeyboardButton("⚡ Powered By", url="https://t.me/Opleech_WD")
    )

    bot.send_message(message.chat.id, text, reply_markup=keyboard)

# ============================================================
# VIEW COMMAND
# ============================================================

@bot.message_handler(commands=['view'])
def get_movies_list(message):
    chat_id = message.chat.id
    
    wait_msg = bot.send_message(chat_id, "⏳ <b>Fetching movies...</b>")

    global movie_list, real_dict
    
    try:
        movie_list, real_dict = scrape_movies()
        
        if not movie_list:
            bot.edit_message_text(
                "❌ <b>No movies found. Please try again.</b>",
                chat_id=chat_id,
                message_id=wait_msg.message_id
            )
            return
        
        bot.delete_message(chat_id, wait_msg.message_id)
        
        keyboard = types.InlineKeyboardMarkup(row_width=2)
        buttons = []
        for i, title in enumerate(movie_list[:15]):
            buttons.append(
                types.InlineKeyboardButton(
                    text=title[:25],
                    callback_data=str(i)
                )
            )
        keyboard.add(*buttons)
        
        bot.send_message(
            chat_id,
            f"🎬 <b>Select a movie:</b>\n\n📊 Total: {len(movie_list)} movies",
            reply_markup=keyboard
        )
        
    except Exception as e:
        logger.error(f"Error: {e}")
        bot.edit_message_text(
            f"❌ <b>Error:</b> {str(e)[:100]}",
            chat_id=chat_id,
            message_id=wait_msg.message_id
        )

# ============================================================
# CALLBACK
# ============================================================

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    global real_dict, movie_list
    
    try:
        index = int(call.data)
        
        if index < 0 or index >= len(movie_list):
            bot.answer_callback_query(call.id, "Movie not found!")
            return
        
        title = movie_list[index]
        details = real_dict.get(title, [])
        
        bot.answer_callback_query(call.id)
        
        if not details:
            bot.send_message(call.message.chat.id, "❌ No details available.")
            return
        
        for detail in details[:3]:
            bot.send_message(call.message.chat.id, detail)
            
    except Exception as e:
        logger.error(f"Callback error: {e}")
        bot.answer_callback_query(call.id, "Error!")

# ============================================================
# SCRAPING FUNCTIONS
# ============================================================

def scrape_movies():
    movies = []
    details = {}
    
    try:
        logger.info(f"Fetching from: {TAMILMV_URL}")
        
        response = requests.get(TAMILMV_URL, headers=HEADERS, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Find movie divs
        movie_divs = soup.find_all('div', {'class': 'ipsType_break ipsContained'})
        
        if not movie_divs:
            logger.warning("No movie divs found")
            # Return sample data for testing
            return get_sample_movies()
        
        count = 0
        for div in movie_divs:
            if count >= 10:
                break
                
            try:
                link_tag = div.find('a')
                if not link_tag:
                    continue
                    
                title = link_tag.text.strip()
                link = link_tag.get('href')
                
                if not title or not link:
                    continue
                
                if not link.startswith('http'):
                    link = f'{TAMILMV_URL}{link}'
                
                movies.append(title)
                details[title] = get_movie_details(link)
                count += 1
                
            except Exception as e:
                logger.error(f"Error processing: {e}")
                continue
        
        if not movies:
            return get_sample_movies()
        
        return movies, details
        
    except Exception as e:
        logger.error(f"Scrape error: {e}")
        return get_sample_movies()

def get_movie_details(url):
    try:
        response = requests.get(url, headers=HEADERS, timeout=8)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        title = soup.find('h1')
        title = title.text.strip() if title else "Unknown"
        
        # Find magnet links
        magnets = []
        for a in soup.find_all('a', href=True):
            if 'magnet:' in a.get('href', ''):
                magnets.append(a['href'])
        
        # Find torrent links
        torrents = []
        for a in soup.find_all('a', {'data-fileext': 'torrent', 'href': True}):
            torrents.append(a.get('href'))
        
        messages = []
        
        if magnets:
            magnet = magnets[0]
            torrent = torrents[0] if torrents else None
            
            if torrent and not torrent.startswith('http'):
                torrent = f'{TAMILMV_URL}{torrent}'
            
            msg = f"""<b>📂 {title}</b>

🧲 <b>Magnet Link:</b>
<code>{magnet[:100]}...</code>"""
            
            if torrent:
                msg += f"\n\n📥 <a href='{torrent}'>⬇️ Download Torrent</a>"
            
            messages.append(msg)
        elif torrents:
            torrent = torrents[0]
            if not torrent.startswith('http'):
                torrent = f'{TAMILMV_URL}{torrent}'
            
            msg = f"""<b>📂 {title}</b>

📥 <a href='{torrent}'>⬇️ Download Torrent</a>"""
            messages.append(msg)
        
        return messages if messages else [f"<b>📂 {title}</b>\n\nNo download links available"]
        
    except Exception as e:
        logger.error(f"Details error: {e}")
        return []

def get_sample_movies():
    """Sample movies if scraping fails"""
    movies = [
        "Movie 1: Sample Film",
        "Movie 2: Test Movie", 
        "Movie 3: Demo Film",
        "Movie 4: Example Movie",
        "Movie 5: Trial Film"
    ]
    
    details = {}
    for movie in movies:
        details[movie] = [
            f"""<b>📂 {movie}</b>

🧲 <b>Magnet Link:</b>
<code>magnet:?xt=urn:btih:test123456</code>

📥 <a href='https://example.com'>⬇️ Download Torrent</a>

⚠️ This is sample data. Website might be blocking requests."""
        ]
    
    return movies, details

# ============================================================
# WEBHOOK ROUTES
# ============================================================

@app.route("/", methods=["GET"])
def home():
    return "Angel Bot is running! 🚀", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        if request.headers.get("content-type") != "application/json":
            return "Invalid content type", 403
        
        json_data = request.get_data().decode("utf-8")
        update = telebot.types.Update.de_json(json_data)
        bot.process_new_updates([update])
        return "OK", 200
        
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return "Webhook error", 500

# ============================================================
# SET WEBHOOK
# ============================================================

def setup_webhook():
    try:
        if WEBHOOK_URL:
            webhook_url = f"{WEBHOOK_URL}/webhook"
            bot.remove_webhook()
            time.sleep(1)
            bot.set_webhook(url=webhook_url)
            logger.info(f"✅ Webhook set to: {webhook_url}")
            return True
        else:
            logger.warning("WEBHOOK_URL not set")
            return False
    except Exception as e:
        logger.error(f"❌ Webhook setup failed: {e}")
        return False

# ============================================================
# MAIN
# ============================================================

# For Vercel - runs when module loads
if not os.getenv("VERCEL"):
    setup_webhook()

if __name__ == "__main__":
    # Local development
    logger.info("Running locally with polling...")
    bot.remove_webhook()
    bot.polling(non_stop=True)