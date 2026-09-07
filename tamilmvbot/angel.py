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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ============================================================
# GLOBAL VARIABLES
# ============================================================

movie_list = []
real_dict = {}

# ============================================================
# COMMANDS
# ============================================================

@bot.message_handler(commands=["start"])
def send_welcome(message):
    """Send welcome message"""
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

@bot.message_handler(commands=["view"])
def get_movie_list(message):
    """Get movie list"""
    chat_id = message.chat.id
    
    # Send waiting message
    wait_msg = bot.send_message(chat_id, "⏳ <b>Fetching movies...</b>")

    try:
        # Fetch movies
        global movie_list, real_dict
        movie_list, real_dict = scrape_movies()
        
        if not movie_list:
            bot.edit_message_text(
                "❌ <b>No movies found. Please try again later.</b>",
                chat_id=chat_id,
                message_id=wait_msg.message_id
            )
            return
        
        # Delete waiting message
        bot.delete_message(chat_id, wait_msg.message_id)
        
        # Create keyboard
        keyboard = types.InlineKeyboardMarkup(row_width=2)
        buttons = []
        for i, title in enumerate(movie_list[:15]):  # Max 15 movies
            buttons.append(
                types.InlineKeyboardButton(
                    text=title[:25],
                    callback_data=str(i)
                )
            )
        keyboard.add(*buttons)
        
        # Send movie list
        bot.send_message(
            chat_id,
            f"🎬 <b>Select a movie:</b>\n\n📊 Total: {len(movie_list)} movies",
            reply_markup=keyboard
        )
        
    except Exception as e:
        logger.error(f"Error in get_movie_list: {e}")
        bot.edit_message_text(
            f"❌ <b>Error fetching movies:</b>\n{str(e)}",
            chat_id=chat_id,
            message_id=wait_msg.message_id
        )

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    """Handle movie selection"""
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
            bot.send_message(call.message.chat.id, "❌ No details available for this movie.")
            return
        
        # Send details
        for detail in details[:3]:
            bot.send_message(call.message.chat.id, detail)
            
    except Exception as e:
        logger.error(f"Callback error: {e}")
        bot.answer_callback_query(call.id, "Error!")

# ============================================================
# SCRAPING FUNCTIONS
# ============================================================

def scrape_movies():
    """Scrape movies from 1TamilMV"""
    movies = []
    details = {}
    
    try:
        # Get main page
        response = requests.get(TAMILMV_URL, headers=HEADERS, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        movie_divs = soup.find_all('div', {'class': 'ipsType_break ipsContained'})
        
        if not movie_divs:
            return [], {}
        
        # Get first 10 movies
        for i in range(min(10, len(movie_divs))):
            try:
                title = movie_divs[i].find('a').text.strip()
                link = movie_divs[i].find('a')['href']
                movies.append(title)
                
                # Get movie details
                movie_details = get_movie_details(link)
                if movie_details:
                    details[title] = movie_details
                    
            except Exception as e:
                logger.error(f"Error processing movie {i}: {e}")
                continue
                
        return movies, details
        
    except Exception as e:
        logger.error(f"Scrape error: {e}")
        return [], {}

def get_movie_details(url):
    """Get movie details from URL"""
    try:
        if not url.startswith('http'):
            url = f'{TAMILMV_URL}{url}'
            
        response = requests.get(url, headers=HEADERS, timeout=8)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Get title
        title = soup.find('h1')
        title = title.text.strip() if title else "Unknown"
        
        # Get magnet links
        magnets = []
        for a in soup.find_all('a', href=True):
            if 'magnet:' in a['href']:
                magnets.append(a['href'])
        
        # Get torrent links
        torrents = []
        for a in soup.find_all('a', {'data-fileext': 'torrent', 'href': True}):
            torrents.append(a['href'])
        
        messages = []
        
        if magnets:
            # Send first magnet only
            torrent_link = torrents[0] if torrents else None
            if torrent_link and not torrent_link.startswith('http'):
                torrent_link = f'{TAMILMV_URL}{torrent_link}'
            
            msg = f"""<b>📂 {title}</b>

🧲 <b>Magnet Link:</b>
<pre>{magnets[0][:100]}...</pre>"""
            
            if torrent_link:
                msg += f"\n\n📥 <a href='{torrent_link}'>⬇️ Download Torrent</a>"
            
            messages.append(msg)
        elif torrents:
            # Only torrent available
            torrent_link = torrents[0]
            if not torrent_link.startswith('http'):
                torrent_link = f'{TAMILMV_URL}{torrent_link}'
            
            msg = f"""<b>📂 {title}</b>

📥 <a href='{torrent_link}'>⬇️ Download Torrent</a>

⚠️ No magnet link available"""
            messages.append(msg)
        
        return messages
        
    except Exception as e:
        logger.error(f"Details error: {e}")
        return []

# ============================================================
# WEBHOOK ROUTES
# ============================================================

@app.route("/", methods=["GET"])
def home():
    """Health check"""
    return "Angel Bot is running! 🚀", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    """Handle webhook requests"""
    try:
        if request.headers.get("content-type") != "application/json":
            return "Invalid content type", 403
        
        # Parse update
        json_data = request.get_data().decode("utf-8")
        update = telebot.types.Update.de_json(json_data)
        
        # Process update
        bot.process_new_updates([update])
        
        return "OK", 200
        
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return "Webhook error", 500

# ============================================================
# SET WEBHOOK
# ============================================================

def setup_webhook():
    """Setup webhook on startup"""
    try:
        webhook_url = f"{WEBHOOK_URL}/webhook"
        
        # Remove existing webhook
        bot.remove_webhook()
        
        # Set new webhook
        bot.set_webhook(
            url=webhook_url,
            max_connections=100
        )
        
        logger.info(f"✅ Webhook set to: {webhook_url}")
        return True
        
    except Exception as e:
        logger.error(f"❌ Webhook setup failed: {e}")
        return False

# ============================================================
# MAIN
# ============================================================

# Setup webhook when app starts
setup_webhook()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 3000))
    app.run(host="0.0.0.0", port=port)