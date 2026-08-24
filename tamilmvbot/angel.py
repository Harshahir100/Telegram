import os
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from datetime import datetime, timedelta
from dotenv import load_dotenv
import telebot
from telebot import types
import requests
from bs4 import BeautifulSoup
from flask import Flask, request

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

load_dotenv()

# ============ Configuration =================
TOKEN = os.getenv('TOKEN')
WEBHOOK_URL = os.getenv('WEBHOOK_URL')
TAMILMV_URL = os.getenv('TAMILMV_URL', 'https://www.1tamilmv.boo')
PORT = int(os.getenv('PORT', 3000))
CACHE_DURATION = int(os.getenv('CACHE_DURATION', 300))  # 5 minutes
MAX_SEARCH_RESULTS = 10
# ============================================

bot = telebot.TeleBot(TOKEN, parse_mode='HTML', threaded=False)
app = Flask(__name__)

# Global variables with type hints
movie_list: list = []
real_dict: dict = {}
search_results: dict = {}
cache_timestamp: datetime = None

# Thread pool for parallel scraping
executor = ThreadPoolExecutor(max_workers=5)

# User rate limiting (simple in-memory)
user_last_command = {}

# ============ Helper Functions ============

def is_rate_limited(user_id: int, cooldown: int = 5) -> bool:
    """Check if user is rate limited (cooldown in seconds)"""
    now = time.time()
    if user_id in user_last_command:
        if now - user_last_command[user_id] < cooldown:
            return True
    user_last_command[user_id] = now
    return False

def sanitize_input(text: str) -> str:
    """Sanitize user input"""
    return text.strip()[:100]  # Limit length

@lru_cache(maxsize=1)
def get_cached_movies():
    """Cache movie list for CACHE_DURATION seconds"""
    global cache_timestamp, movie_list, real_dict
    
    if cache_timestamp and (datetime.now() - cache_timestamp).seconds < CACHE_DURATION:
        logger.info("Returning cached movie data")
        return movie_list, real_dict
    
    logger.info("Fetching fresh movie data")
    movie_list, real_dict = tamilmv()
    cache_timestamp = datetime.now()
    return movie_list, real_dict

# ============ Command Handlers ============

@bot.message_handler(commands=['start'])
def random_answer(message):
    if is_rate_limited(message.chat.id):
        return
    
    text_message = """<b>Hello 👋</b>

<blockquote><b>🎬 Get latest Movies from 1Tamilmv</b></blockquote>

⚙️ <b>How to use me??</b> 🤔

✯ Please enter /view command and you'll get magnet link as well as link to torrent file 😌

<blockquote><b>🔗 Share and Support 💝</b></blockquote>"""

    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(
        types.InlineKeyboardButton('🔗 GitHub 🔗', url='https://github.com/SudoR2spr'),
        types.InlineKeyboardButton(text="⚡ Powered By", url='https://t.me/Opleech_WD')
    )

    bot.send_photo(
        chat_id=message.chat.id,
        photo='https://graph.org/file/4e8a1172e8ba4b7a0bdfa.jpg',
        caption=text_message,
        reply_markup=keyboard
    )

@bot.message_handler(commands=['view'])
def view_movies(message):
    if is_rate_limited(message.chat.id, 10):
        bot.send_message(message.chat.id, "⏳ Please wait 10 seconds between requests")
        return
    
    bot.send_message(message.chat.id, "<b>🧲 Fetching latest movies...</b>")
    
    # Use cached data
    global movie_list, real_dict
    movie_list, real_dict = get_cached_movies()
    
    if not movie_list:
        bot.send_message(message.chat.id, "❌ Failed to fetch movies. Please try again later.")
        return

    combined_caption = """<b><blockquote>🔗 Select a Movie from the list 🎬</blockquote></b>\n\n🔘 Please select a movie:"""
    keyboard = makeKeyboard(movie_list)

    bot.send_photo(
        chat_id=message.chat.id,
        photo='https://graph.org/file/4e8a1172e8ba4b7a0bdfa.jpg',
        caption=combined_caption,
        reply_markup=keyboard
    )

@bot.message_handler(commands=['search'])
def search_movie(message):
    if is_rate_limited(message.chat.id, 5):
        bot.send_message(message.chat.id, "⏳ Please wait 5 seconds between searches")
        return
    
    query = sanitize_input(message.text.replace('/search', '', 1).strip())

    if not query:
        bot.send_message(
            message.chat.id,
            "🔎 Please provide a movie name.\n\nExample:\n/search Inception"
        )
        return

    results = search_authorized_catalog(query)

    if not results:
        bot.send_message(
            message.chat.id,
            f"❌ No results found for: <b>{query}</b>"
        )
        return

    keyboard = types.InlineKeyboardMarkup()
    for index, movie in enumerate(results[:MAX_SEARCH_RESULTS]):
        title = movie["title"]
        year = movie.get("year", "")
        keyboard.add(
            types.InlineKeyboardButton(
                text=f"{title} {year}",
                callback_data=f"movie_{index}"
            )
        )

    search_results[message.chat.id] = results[:MAX_SEARCH_RESULTS]
    bot.send_message(
        message.chat.id,
        f"🔎 <b>Search results for:</b> {query}",
        reply_markup=keyboard
    )

# ============ Callback Handlers ============

@bot.callback_query_handler(func=lambda call: call.data.startswith("movie_"))
def movie_callback(call):
    results = search_results.get(call.message.chat.id, [])
    
    try:
        index = int(call.data.split("_")[1])
        movie = results[index]
        
        bot.send_message(
            call.message.chat.id,
            f"""🎬 <b>{movie['title']}</b>

📅 Year: {movie.get('year', 'N/A')}

🔗 <a href="{movie['url']}">Open Movie</a>"""
        )
    except (ValueError, IndexError, KeyError):
        bot.send_message(call.message.chat.id, "❌ Movie result is no longer available.")

@bot.callback_query_handler(func=lambda call: call.data.isdigit())
def movie_selection_callback(call):
    """Handle movie selection from /view command"""
    global real_dict
    
    try:
        key = int(call.data)
        if key < len(movie_list):
            title = movie_list[key]
            if title in real_dict and real_dict[title]:
                for msg in real_dict[title]:
                    bot.send_message(call.message.chat.id, text=msg)
            else:
                bot.send_message(call.message.chat.id, "❌ Movie details not available.")
        else:
            bot.send_message(call.message.chat.id, "❌ Invalid selection.")
    except (ValueError, IndexError):
        bot.send_message(call.message.chat.id, "❌ Invalid selection.")

# ============ Helper Functions ============

def makeKeyboard(movie_list):
    markup = types.InlineKeyboardMarkup(row_width=1)
    for key, value in enumerate(movie_list[:50]):  # Limit to 50 movies
        markup.add(
            types.InlineKeyboardButton(
                text=value[:50],  # Truncate long titles
                callback_data=f"{key}"
            )
        )
    return markup

def get_movie_details_parallel(urls):
    """Fetch movie details in parallel"""
    movie_data = {}
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_url = {executor.submit(get_movie_details, url): url for url in urls}
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            try:
                result = future.result(timeout=30)
                if result:
                    movie_data[url] = result
            except Exception as e:
                logger.error(f"Error fetching {url}: {e}")
                movie_data[url] = []
    
    return movie_data

def tamilmv():
    """Scrape movie list with parallel detail fetching"""
    mainUrl = TAMILMV_URL
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }

    movie_list = []
    movie_urls = []

    try:
        web = requests.get(mainUrl, headers=headers, timeout=15)
        web.raise_for_status()
        soup = BeautifulSoup(web.text, 'lxml')

        temps = soup.find_all('div', {'class': 'ipsType_break ipsContained'})
        
        if len(temps) < 25:
            logger.warning("Not enough movies found on the page")
            return [], {}

        # Collect movie titles and URLs
        for i in range(min(25, len(temps))):
            try:
                title = temps[i].findAll('a')[0].text.strip()
                link = temps[i].find('a')['href']
                if not link.startswith('http'):
                    link = f"{TAMILMV_URL}{link}"
                movie_list.append(title)
                movie_urls.append(link)
            except (AttributeError, IndexError) as e:
                logger.error(f"Error parsing movie {i}: {e}")
                continue

        # Fetch details in parallel
        logger.info(f"Fetching details for {len(movie_urls)} movies in parallel")
        details_dict = get_movie_details_parallel(movie_urls)
        
        real_dict = {}
        for title, url in zip(movie_list, movie_urls):
            real_dict[title] = details_dict.get(url, [])

        return movie_list, real_dict
        
    except requests.RequestException as e:
        logger.error(f"Network error in tamilmv function: {e}")
        return [], {}
    except Exception as e:
        logger.error(f"Error in tamilmv function: {e}")
        return [], {}

def get_movie_details(url):
    """Fetch movie details from a single page"""
    if not url:
        return []
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        html = requests.get(url, timeout=15, headers=headers)
        html.raise_for_status()
        soup = BeautifulSoup(html.text, 'lxml')

        mag = [a['href'] for a in soup.find_all('a', href=True) if 'magnet:' in a['href']]
        filelink = [a['href'] for a in soup.find_all('a', {"data-fileext": "torrent", 'href': True})]

        if not mag:
            return []

        movie_title = soup.find('h1')
        movie_title = movie_title.text.strip() if movie_title else "Unknown Title"

        movie_details = []
        for p in range(len(mag)):
            torrent_link = filelink[p] if p < len(filelink) else None
            if torrent_link and not torrent_link.startswith('http'):
                torrent_link = f'{TAMILMV_URL}{torrent_link}'

            message = f"""<b>📂 Movie Title:</b>
<blockquote>{movie_title}</blockquote>

🧲 <b>Magnet Link:</b>
<pre>{mag[p][:500]}</pre>
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
        
    except requests.RequestException as e:
        logger.error(f"Network error retrieving movie details from {url}: {e}")
        return []
    except Exception as e:
        logger.error(f"Error retrieving movie details from {url}: {e}")
        return []

def search_authorized_catalog(query):
    """Replace with actual search implementation"""
    # This is a placeholder - implement your actual search here
    return []

# ============ Flask Routes ============

@app.route('/')
def health_check():
    return "Angel Bot Healthy", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        if not request.is_json:
            return 'Invalid content type', 403

        update = telebot.types.Update.de_json(request.get_data().decode('utf-8'))
        logger.info("Telegram update received")
        
        bot.process_new_updates([update])
        logger.info("Telegram update processed")
        return '', 200

    except Exception as e:
        logger.exception(f"Webhook processing failed: {e}")
        return 'Webhook error', 500

# ============ Main ============

if __name__ == "__main__":
    # Clean webhook URL
    webhook_url = WEBHOOK_URL.rstrip('/')
    
    # Remove any previous webhook
    bot.remove_webhook()
    time.sleep(1)

    # Set webhook
    webhook_full_url = f"{webhook_url}/webhook"
    logger.info(f"Setting webhook to: {webhook_full_url}")
    bot.set_webhook(url=webhook_full_url)

    # Start Flask app
    app.run(host='0.0.0.0', port=PORT)