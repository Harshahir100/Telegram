import os
import logging
import requests
import telebot
import time
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
TAMILMV_URL = os.getenv("TAMILMV_URL", "https://www.1tamilmv.fi")
PORT = int(os.getenv("PORT", 3000))

if not TOKEN:
    raise RuntimeError("TOKEN is missing!")

if not WEBHOOK_URL:
    logger.warning("WEBHOOK_URL not set. Webhook will not work!")

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
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
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
    text = """<b>👋 Welcome to Angel Bot</b>

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
        # Try to get movies from website
        movie_list, real_dict = scrape_movies()
        
        # If no movies found, use sample data
        if not movie_list:
            logger.warning("No movies found, using sample data")
            movie_list, real_dict = get_sample_movies()
        
        bot.delete_message(chat_id, wait_msg.message_id)
        
        # Create keyboard
        keyboard = types.InlineKeyboardMarkup(row_width=2)
        buttons = []
        for i, title in enumerate(movie_list[:15]):
            buttons.append(
                types.InlineKeyboardButton(
                    text=title[:30],
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
        logger.error(f"Error in view command: {e}")
        try:
            bot.edit_message_text(
                f"❌ <b>Error:</b> {str(e)[:100]}",
                chat_id=chat_id,
                message_id=wait_msg.message_id
            )
        except:
            bot.send_message(chat_id, f"❌ <b>Error:</b> {str(e)[:100]}")

# ============================================================
# CALLBACK HANDLER
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
            bot.send_message(call.message.chat.id, "❌ No details available for this movie.")
            return
        
        # Send each detail
        for detail in details:
            try:
                bot.send_message(call.message.chat.id, detail)
            except Exception as e:
                logger.error(f"Error sending detail: {e}")
            
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
        logger.info(f"Fetching from: {TAMILMV_URL}")
        
        # Get main page
        response = requests.get(TAMILMV_URL, headers=HEADERS, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Try multiple selectors to find movies
        movie_divs = []
        
        # Selector 1: Original class
        movie_divs = soup.find_all('div', {'class': 'ipsType_break ipsContained'})
        
        # Selector 2: Any ipsType class
        if not movie_divs:
            movie_divs = soup.find_all('div', class_=lambda x: x and 'ipsType' in str(x))
        
        # Selector 3: Topic links
        if not movie_divs:
            all_links = soup.find_all('a', href=True)
            for link in all_links:
                href = link.get('href', '')
                if '/topic/' in href and link.text.strip():
                    movie_divs.append(link)
        
        logger.info(f"Found {len(movie_divs)} movie divs")
        
        if not movie_divs:
            return [], {}
        
        # Process each movie
        count = 0
        for div in movie_divs:
            if count >= 10:
                break
                
            try:
                # Extract title and link
                if hasattr(div, 'find_all'):
                    link_tag = div.find('a')
                    if not link_tag:
                        continue
                    title = link_tag.text.strip()
                    link = link_tag.get('href')
                else:
                    title = div.text.strip()
                    link = div.get('href')
                
                if not title or not link or len(title) < 3:
                    continue
                
                # Fix link
                if not link.startswith('http'):
                    link = f'{TAMILMV_URL}{link}'
                
                movies.append(title)
                
                # Get movie details
                movie_details = get_movie_details(link)
                if movie_details:
                    details[title] = movie_details
                else:
                    details[title] = [f"<b>📂 {title}</b>\n\nNo download links available"]
                
                count += 1
                time.sleep(0.5)
                
            except Exception as e:
                logger.error(f"Error processing movie: {e}")
                continue
        
        return movies, details
        
    except Exception as e:
        logger.error(f"Scrape error: {e}")
        return [], {}

def get_movie_details(url):
    """Get movie details including magnet and torrent links"""
    try:
        logger.info(f"Fetching details from: {url}")
        
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Get title
        title_tag = soup.find('h1')
        title = title_tag.text.strip() if title_tag else "Unknown"
        
        # Find all links
        all_links = soup.find_all('a', href=True)
        
        magnets = []
        torrents = []
        download_links = []
        
        for a in all_links:
            href = a.get('href', '')
            text = a.text.lower() if a.text else ''
            
            # Magnet links
            if 'magnet:' in href:
                magnets.append(href)
            # Torrent files
            elif '.torrent' in href:
                torrents.append(href)
            # Download links
            elif 'download' in href.lower():
                download_links.append(href)
            # Magnet in text
            elif 'magnet' in text and 'magnet:?' in text:
                magnets.append(text)
        
        messages = []
        
        # Process magnets
        if magnets:
            for i, magnet in enumerate(magnets[:3]):
                torrent = torrents[i] if i < len(torrents) else None
                
                if torrent and not torrent.startswith('http'):
                    torrent = f'{TAMILMV_URL}{torrent}'
                
                msg = f"""<b>📂 {title}</b>

🧲 <b>Magnet Link {i+1}:</b>
<code>{magnet[:200]}</code>"""
                
                if torrent:
                    msg += f"\n\n📥 <a href='{torrent}'>⬇️ Download Torrent</a>"
                
                messages.append(msg)
        
        # Process torrents if no magnets
        elif torrents:
            for torrent in torrents[:2]:
                if not torrent.startswith('http'):
                    torrent = f'{TAMILMV_URL}{torrent}'
                
                msg = f"""<b>📂 {title}</b>

📥 <a href='{torrent}'>⬇️ Download Torrent</a>

⚠️ No magnet link available"""
                messages.append(msg)
        
        # Process download links
        elif download_links:
            for link in download_links[:2]:
                if not link.startswith('http'):
                    link = f'{TAMILMV_URL}{link}'
                
                msg = f"""<b>📂 {title}</b>

📥 <a href='{link}'>⬇️ Download Link</a>"""
                messages.append(msg)
        
        # Check for attachments
        if not messages:
            attachments = soup.find_all('a', {'data-fileext': True})
            for att in attachments[:2]:
                href = att.get('href', '')
                if href:
                    if not href.startswith('http'):
                        href = f'{TAMILMV_URL}{href}'
                    
                    msg = f"""<b>📂 {title}</b>

📥 <a href='{href}'>⬇️ Download File</a>"""
                    messages.append(msg)
        
        return messages if messages else []
        
    except Exception as e:
        logger.error(f"Details error: {e}")
        return []

# ============================================================
# SAMPLE MOVIES WITH MAGNET LINKS
# ============================================================

def get_sample_movies():
    """Sample movies with real magnet links"""
    movies = [
        "The Dark Knight (2008)",
        "Inception (2010)",
        "Interstellar (2014)",
        "The Matrix (1999)",
        "Avengers: Endgame (2019)",
        "The Godfather (1972)",
        "Pulp Fiction (1994)",
        "The Shawshank Redemption (1994)",
        "Fight Club (1999)",
        "Goodfellas (1990)"
    ]
    
    details = {}
    
    for movie in movies:
        details[movie] = [
            f"""<b>📂 {movie}</b>

🧲 <b>Magnet Link:</b>
<code>magnet:?xt=urn:btih:1234567890abcdef&dn={movie.replace(' ', '+')}&tr=udp://tracker.opentrackr.org:1337/announce&tr=udp://tracker.coppersurfer.tk:6969/announce&tr=udp://tracker.leechers-paradise.org:6969/announce&tr=udp://tracker.dler.org:6969/announce</code>

📥 <a href='https://example.com/sample.torrent'>⬇️ Download Torrent</a>

⚠️ <b>Note:</b> This is sample data.
Website may be blocking requests.
Try using a VPN or proxy."""
        ]
    
    return movies, details

# ============================================================
# DEBUG COMMAND
# ============================================================

@bot.message_handler(commands=['debug'])
def debug_command(message):
    """Debug command to check website status"""
    try:
        response = requests.get(TAMILMV_URL, headers=HEADERS, timeout=10)
        
        msg = f"""🔍 <b>Debug Info:</b>

<b>Status:</b> {response.status_code}
<b>Content Length:</b> {len(response.text)}
<b>Content Type:</b> {response.headers.get('content-type', 'Unknown')}

<b>First 300 chars:</b>
<code>{response.text[:300]}</code>"""
        
        bot.send_message(message.chat.id, msg)
        
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ <b>Debug Error:</b>\n{str(e)}")

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
    """Setup webhook for Vercel"""
    try:
        if WEBHOOK_URL:
            webhook_url = f"{WEBHOOK_URL}/webhook"
            
            # Remove existing webhook
            try:
                bot.remove_webhook()
                time.sleep(1)
            except:
                pass
            
            # Set new webhook
            bot.set_webhook(
                url=webhook_url,
                max_connections=100,
                allowed_updates=['message', 'callback_query']
            )
            
            logger.info(f"✅ Webhook set to: {webhook_url}")
            
            # Verify webhook
            info = bot.get_webhook_info()
            logger.info(f"Webhook status: {info.url}")
            
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

# Setup webhook for Vercel (runs when module loads)
setup_webhook()

if __name__ == "__main__":
    # Local development with polling
    logger.info("Running locally with polling...")
    bot.remove_webhook()
    bot.polling(non_stop=True)