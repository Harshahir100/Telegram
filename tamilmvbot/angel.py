import os
import logging
import requests
import telebot
from flask import Flask, request
from telebot import types
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import time

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

# ============================================================
# BETTER HEADERS
# ============================================================

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Cache-Control": "max-age=0",
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
    chat_id = message.chat.id
    
    # Send waiting message
    wait_msg = bot.send_message(chat_id, "⏳ <b>Fetching movies...</b>")

    try:
        # Fetch movies with better error handling
        global movie_list, real_dict
        movie_list, real_dict = scrape_movies()
        
        # Debug: Send raw data
        logger.info(f"Movies found: {len(movie_list)}")
        
        if not movie_list:
            # Try alternative URL
            bot.edit_message_text(
                "❌ <b>No movies found.</b>\n\n"
                "Trying alternative source...",
                chat_id=chat_id,
                message_id=wait_msg.message_id
            )
            
            # Try alternative
            movie_list, real_dict = scrape_movies_alternative()
            
            if not movie_list:
                bot.edit_message_text(
                    "❌ <b>No movies found.</b>\n\n"
                    "Please try again later or contact @Opleech_WD",
                    chat_id=chat_id,
                    message_id=wait_msg.message_id
                )
                return
        
        # Delete waiting message
        bot.delete_message(chat_id, wait_msg.message_id)
        
        # Create keyboard
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
        
        # Send movie list
        bot.send_message(
            chat_id,
            f"🎬 <b>Select a movie:</b>\n\n📊 Total: {len(movie_list)} movies",
            reply_markup=keyboard
        )
        
    except Exception as e:
        logger.error(f"Error in get_movie_list: {e}")
        bot.edit_message_text(
            f"❌ <b>Error:</b>\n{str(e)[:200]}",
            chat_id=chat_id,
            message_id=wait_msg.message_id
        )

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
        
        # Send details
        for detail in details[:3]:
            bot.send_message(call.message.chat.id, detail)
            
    except Exception as e:
        logger.error(f"Callback error: {e}")
        bot.answer_callback_query(call.id, "Error!")

# ============================================================
# SCRAPING FUNCTIONS - FIXED
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
        
        logger.info(f"Response status: {response.status_code}")
        logger.info(f"Response length: {len(response.text)}")
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Try different selectors
        movie_divs = []
        
        # Method 1: Original selector
        movie_divs = soup.find_all('div', {'class': 'ipsType_break ipsContained'})
        
        if not movie_divs:
            # Method 2: Try different class
            movie_divs = soup.find_all('div', class_=lambda x: x and 'ipsType' in x)
        
        if not movie_divs:
            # Method 3: Find all links with movie titles
            all_links = soup.find_all('a', href=True)
            movie_links = []
            for link in all_links:
                href = link.get('href', '')
                if '/topic/' in href and link.text.strip():
                    movie_links.append(link)
            movie_divs = movie_links
        
        logger.info(f"Movie divs found: {len(movie_divs)}")
        
        if not movie_divs:
            # Save HTML for debugging
            with open('debug.html', 'w', encoding='utf-8') as f:
                f.write(response.text)
            logger.info("Saved debug.html for inspection")
            return [], {}
        
        # Get movies
        count = 0
        for item in movie_divs:
            try:
                if count >= 10:
                    break
                    
                # Get title and link
                if hasattr(item, 'find_all'):
                    # It's a div
                    links = item.find_all('a')
                    if not links:
                        continue
                    title = links[0].text.strip()
                    link = links[0].get('href')
                else:
                    # It's already a link
                    title = item.text.strip()
                    link = item.get('href')
                
                if not title or not link:
                    continue
                
                # Fix link
                if not link.startswith('http'):
                    link = f'{TAMILMV_URL}{link}'
                
                movies.append(title)
                
                # Get details
                movie_details = get_movie_details(link)
                if movie_details:
                    details[title] = movie_details
                else:
                    # Add placeholder
                    details[title] = [f"<b>📂 {title}</b>\n\nNo details available"]
                
                count += 1
                time.sleep(0.5)  # Be gentle
                
            except Exception as e:
                logger.error(f"Error processing item: {e}")
                continue
        
        logger.info(f"Successfully fetched {len(movies)} movies")
        return movies, details
        
    except Exception as e:
        logger.error(f"Scrape error: {e}")
        return [], {}

def scrape_movies_alternative():
    """Alternative scraping method"""
    movies = []
    details = {}
    
    try:
        # Try different URL
        alt_urls = [
            "https://www.1tamilmv.boo/index.php",
            "https://www.1tamilmv.boo/forum/",
            "https://www.1tamilmv.boo/discover/"
        ]
        
        for url in alt_urls:
            try:
                response = requests.get(url, headers=HEADERS, timeout=10)
                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, 'html.parser')
                    
                    # Find any links that look like movie topics
                    links = soup.find_all('a', href=True)
                    for link in links:
                        href = link.get('href', '')
                        if '/topic/' in href and link.text.strip():
                            title = link.text.strip()
                            if len(title) > 5 and not title.startswith('['):
                                movies.append(title)
                                details[title] = [f"<b>📂 {title}</b>\n\nCheck on website"]
                                if len(movies) >= 10:
                                    break
                    
                    if movies:
                        break
            except:
                continue
        
        return movies, details
        
    except Exception as e:
        logger.error(f"Alternative scrape error: {e}")
        return [], {}

def get_movie_details(url):
    """Get movie details from URL"""
    try:
        logger.info(f"Fetching details from: {url}")
        
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Get title
        title = soup.find('h1')
        title = title.text.strip() if title else "Unknown"
        
        # Get all links
        all_links = soup.find_all('a', href=True)
        
        # Find magnet links
        magnets = []
        torrents = []
        
        for a in all_links:
            href = a.get('href', '')
            if 'magnet:' in href:
                magnets.append(href)
            elif '.torrent' in href or 'download' in href:
                torrents.append(href)
        
        messages = []
        
        if magnets:
            magnet = magnets[0]
            torrent = torrents[0] if torrents else None
            
            if torrent and not torrent.startswith('http'):
                torrent = f'{TAMILMV_URL}{torrent}'
            
            msg = f"""<b>📂 {title}</b>

🧲 <b>Magnet Link:</b>
<code>{magnet[:150]}...</code>"""
            
            if torrent:
                msg += f"\n\n📥 <a href='{torrent}'>⬇️ Download Torrent</a>"
            
            messages.append(msg)
        elif torrents:
            torrent = torrents[0]
            if not torrent.startswith('http'):
                torrent = f'{TAMILMV_URL}{torrent}'
            
            msg = f"""<b>📂 {title}</b>

📥 <a href='{torrent}'>⬇️ Download Torrent</a>

⚠️ No magnet link available"""
            messages.append(msg)
        else:
            # Try to find any download link
            download_links = [a for a in all_links if 'download' in a.get('href', '').lower()]
            if download_links:
                link = download_links[0].get('href')
                if not link.startswith('http'):
                    link = f'{TAMILMV_URL}{link}'
                msg = f"""<b>📂 {title}</b>

📥 <a href='{link}'>⬇️ Download Link</a>"""
                messages.append(msg)
        
        return messages
        
    except Exception as e:
        logger.error(f"Details error for {url}: {e}")
        return []

# ============================================================
# DEBUG COMMAND
# ============================================================

@bot.message_handler(commands=["debug"])
def debug_command(message):
    """Debug command to check website"""
    try:
        response = requests.get(TAMILMV_URL, headers=HEADERS, timeout=10)
        msg = f"""<b>🔍 Debug Info</b>

Status: {response.status_code}
Content Length: {len(response.text)}
Content Type: {response.headers.get('content-type', 'Unknown')}

First 200 chars:
<code>{response.text[:200]}</code>"""
        
        bot.send_message(message.chat.id, msg)
        
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Error: {e}")

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
        webhook_url = f"{WEBHOOK_URL}/webhook"
        bot.remove_webhook()
        time.sleep(1)
        bot.set_webhook(url=webhook_url)
        logger.info(f"✅ Webhook set to: {webhook_url}")
        return True
    except Exception as e:
        logger.error(f"❌ Webhook setup failed: {e}")
        return False

# ============================================================
# MAIN
# ============================================================

setup_webhook()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 3000))
    app.run(host="0.0.0.0", port=port)