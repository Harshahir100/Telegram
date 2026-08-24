import os
import time
import re
from urllib.parse import quote_plus
from dotenv import load_dotenv
import telebot
from telebot import types
import requests
from bs4 import BeautifulSoup
from flask import Flask, request
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from datetime import datetime, timedelta

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
CACHE_DURATION = 300  # 5 minutes
# ============================================

bot = telebot.TeleBot(TOKEN, parse_mode='HTML', threaded=False)
app = Flask(__name__)

# Global variables
movie_list = []
real_dict = {}
search_results_cache = {}
cache_timestamp = None
user_last_command = {}

# ============ SEARCH FUNCTION ============

def search_tamilmv(query):
    """
    Search for movies on 1Tamilmv and fetch magnet links
    """
    if not query:
        return []
    
    try:
        # Clean the query
        query = query.strip()
        logger.info(f"Searching for: {query}")
        
        # Method 1: Direct search using site's search URL
        search_url = f"{TAMILMV_URL}/index.php?/search/&q={quote_plus(query)}&type=forums_topic"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        }
        
        response = requests.get(search_url, headers=headers, timeout=20)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'lxml')
        results = []
        
        # Find search results
        # Method 1: Find in topic list
        topic_containers = soup.find_all('li', {'class': 'ipsDataItem'})
        
        if not topic_containers:
            # Alternative: Find by article tags
            topic_containers = soup.find_all('article', {'class': 'ipsDataItem'})
        
        for container in topic_containers[:10]:  # Limit to 10 results
            try:
                # Get title
                title_elem = container.find('span', {'class': 'ipsDataItem_title'})
                if not title_elem:
                    title_elem = container.find('a', {'class': 'ipsDataItem_title'})
                
                if not title_elem:
                    continue
                    
                title = title_elem.text.strip()
                
                # Get link
                link = title_elem.get('href')
                if not link:
                    link_elem = container.find('a', href=True)
                    if link_elem:
                        link = link_elem.get('href')
                
                if not link:
                    continue
                    
                if not link.startswith('http'):
                    link = f"{TAMILMV_URL}{link}"
                
                # Get year if available
                year_match = re.search(r'\((\d{4})\)', title)
                year = year_match.group(1) if year_match else ""
                
                results.append({
                    'title': title,
                    'year': year,
                    'url': link
                })
                
            except Exception as e:
                logger.error(f"Error parsing search result: {e}")
                continue
        
        # If no results found, try alternate method - scrape main page
        if not results:
            logger.info("No search results, trying main page scrape")
            results = scrape_main_page_for_search(query)
        
        return results
        
    except requests.RequestException as e:
        logger.error(f"Search network error: {e}")
        return []
    except Exception as e:
        logger.error(f"Search error: {e}")
        return []

def scrape_main_page_for_search(query):
    """
    Fallback: Scrape main page and filter by query
    """
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        response = requests.get(TAMILMV_URL, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'lxml')
        results = []
        
        # Find movie containers
        containers = soup.find_all('div', {'class': 'ipsType_break ipsContained'})
        
        for container in containers[:30]:
            try:
                title_elem = container.find('a')
                if not title_elem:
                    continue
                    
                title = title_elem.text.strip()
                
                # Check if query is in title (case insensitive)
                if query.lower() in title.lower():
                    link = title_elem.get('href')
                    if link and not link.startswith('http'):
                        link = f"{TAMILMV_URL}{link}"
                    
                    # Extract year
                    year_match = re.search(r'\((\d{4})\)', title)
                    year = year_match.group(1) if year_match else ""
                    
                    results.append({
                        'title': title,
                        'year': year,
                        'url': link
                    })
                    
            except Exception as e:
                continue
        
        return results[:10]  # Limit to 10 results
        
    except Exception as e:
        logger.error(f"Main page scrape error: {e}")
        return []

def get_magnet_links_from_search(link):
    """
    Fetch magnet links from a movie detail page
    """
    if not link:
        return []
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        response = requests.get(link, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'lxml')
        
        # Get movie title
        title_elem = soup.find('h1')
        movie_title = title_elem.text.strip() if title_elem else "Unknown Title"
        
        # Find magnet links
        mag_links = []
        file_links = []
        
        # Method 1: Find all links with magnet:
        for a in soup.find_all('a', href=True):
            href = a.get('href', '')
            if 'magnet:' in href:
                mag_links.append(href)
            elif 'torrent' in href.lower() or a.get('data-fileext') == 'torrent':
                file_links.append(href)
        
        # Method 2: Check code blocks for magnet
        if not mag_links:
            code_blocks = soup.find_all('pre')
            for code in code_blocks:
                text = code.text.strip()
                if 'magnet:' in text:
                    # Extract magnet link from text
                    magnet_match = re.search(r'magnet:\?xt=urn:btih:[a-zA-Z0-9]+[^\s]*', text)
                    if magnet_match:
                        mag_links.append(magnet_match.group(0))
        
        if not mag_links:
            return []
        
        # Build messages
        movie_details = []
        
        for i, magnet in enumerate(mag_links):
            torrent_link = file_links[i] if i < len(file_links) else None
            
            # Fix torrent URL
            if torrent_link and not torrent_link.startswith('http'):
                torrent_link = f"{TAMILMV_URL}{torrent_link}" if torrent_link.startswith('/') else f"{TAMILMV_URL}/{torrent_link}"
            
            message = f"""<b>🎬 Movie:</b> <b>{movie_title}</b>

🧲 <b>Magnet Link:</b>
<code>{magnet}</code>
"""
            
            if torrent_link:
                message += f"""
📥 <b>Download Torrent:</b>
<a href="{torrent_link}">🔗 Click Here to Download</a>
"""
            else:
                message += """
📥 <b>Torrent File:</b> ❌ Not Available
"""
            
            movie_details.append(message)
        
        return movie_details
        
    except Exception as e:
        logger.error(f"Error getting magnet links: {e}")
        return []

# ============ COMMAND HANDLERS ============

@bot.message_handler(commands=['start'])
def random_answer(message):
    text_message = """<b>👋 Hello! Welcome to Movie Magnet Bot</b>

<blockquote><b>🎬 Get Magnet Links for any Movie</b></blockquote>

⚙️ <b>How to use me:</b>

✯ <b>/search</b> - Search for any movie and get magnet links
✯ <b>/view</b> - View latest movies from 1Tamilmv

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
    bot.send_message(message.chat.id, "<b>🧲 Fetching latest movies...</b>")
    
    global movie_list, real_dict
    movie_list, real_dict = tamilmv()
    
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
    # Get search query
    query = message.text.replace('/search', '', 1).strip()
    
    if not query:
        bot.send_message(
            message.chat.id,
            "🔎 Please enter a movie name to search.\n\nExample:\n/search Inception\n/search Avengers\n/search KGF"
        )
        return
    
    # Send "searching" message
    searching_msg = bot.send_message(
        message.chat.id,
        f"🔍 Searching for: <b>{query}</b>\n\n⏳ Please wait..."
    )
    
    try:
        # Search for movies
        results = search_tamilmv(query)
        
        if not results:
            bot.edit_message_text(
                f"❌ No results found for: <b>{query}</b>\n\n💡 Try using a different keyword or check the spelling.",
                chat_id=message.chat.id,
                message_id=searching_msg.message_id
            )
            return
        
        # Create keyboard with results
        keyboard = types.InlineKeyboardMarkup(row_width=1)
        for idx, movie in enumerate(results):
            title = movie.get('title', 'Unknown')
            year = movie.get('year', '')
            display_text = f"{title} {year}" if year else title
            # Truncate if too long
            if len(display_text) > 60:
                display_text = display_text[:57] + "..."
            keyboard.add(
                types.InlineKeyboardButton(
                    text=display_text,
                    callback_data=f"search_{idx}"
                )
            )
        
        # Store results for callback
        search_results_cache[message.chat.id] = {
            'results': results,
            'timestamp': time.time()
        }
        
        # Update message with results
        bot.edit_message_text(
            f"🔎 <b>Search Results for:</b> {query}\n\n📌 Click on a movie to get magnet link:",
            chat_id=message.chat.id,
            message_id=searching_msg.message_id,
            reply_markup=keyboard
        )
        
    except Exception as e:
        logger.error(f"Search error: {e}")
        bot.edit_message_text(
            f"❌ Error searching for: <b>{query}</b>\n\nPlease try again later.",
            chat_id=message.chat.id,
            message_id=searching_msg.message_id
        )

# ============ CALLBACK HANDLERS ============

@bot.callback_query_handler(func=lambda call: call.data.startswith("search_"))
def handle_search_callback(call):
    try:
        # Get index from callback data
        idx = int(call.data.split('_')[1])
        
        # Get cached results
        cache_data = search_results_cache.get(call.message.chat.id, {})
        results = cache_data.get('results', [])
        
        if idx >= len(results):
            bot.answer_callback_query(call.id, "❌ This result is no longer available.")
            return
        
        movie = results[idx]
        movie_title = movie.get('title', 'Unknown')
        movie_url = movie.get('url', '')
        
        if not movie_url:
            bot.answer_callback_query(call.id, "❌ Movie link not available.")
            return
        
        # Send "fetching" message
        fetching_msg = bot.send_message(
            call.message.chat.id,
            f"📥 Fetching magnet links for: <b>{movie_title}</b>\n\n⏳ Please wait..."
        )
        
        # Get magnet links
        magnet_details = get_magnet_links_from_search(movie_url)
        
        if not magnet_details:
            bot.edit_message_text(
                f"❌ No magnet links found for: <b>{movie_title}</b>\n\n💡 Try another movie.",
                chat_id=call.message.chat.id,
                message_id=fetching_msg.message_id
            )
            return
        
        # Send each magnet link
        for detail in magnet_details:
            bot.send_message(call.message.chat.id, detail, disable_web_page_preview=True)
        
        # Delete the fetching message
        bot.delete_message(
            chat_id=call.message.chat.id,
            message_id=fetching_msg.message_id
        )
        
        # Answer callback
        bot.answer_callback_query(call.id, f"✅ Magnet links sent for {movie_title}")
        
    except ValueError:
        bot.answer_callback_query(call.id, "❌ Invalid selection.")
    except Exception as e:
        logger.error(f"Error in search callback: {e}")
        bot.answer_callback_query(call.id, "❌ Error fetching movie details.")

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
                    bot.send_message(call.message.chat.id, text=msg, disable_web_page_preview=True)
                bot.answer_callback_query(call.id, f"✅ Details sent for {title}")
            else:
                bot.send_message(call.message.chat.id, "❌ Movie details not available.")
                bot.answer_callback_query(call.id, "❌ No details available")
        else:
            bot.answer_callback_query(call.id, "❌ Invalid selection.")
    except (ValueError, IndexError):
        bot.answer_callback_query(call.id, "❌ Invalid selection.")

# ============ HELPER FUNCTIONS ============

def makeKeyboard(movie_list):
    markup = types.InlineKeyboardMarkup(row_width=1)
    for key, value in enumerate(movie_list[:25]):
        display_text = value[:50] if len(value) > 50 else value
        markup.add(
            types.InlineKeyboardButton(
                text=display_text,
                callback_data=f"{key}"
            )
        )
    return markup

def tamilmv():
    """Get latest movies from 1Tamilmv"""
    mainUrl = TAMILMV_URL
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }

    movie_list = []
    real_dict = {}

    try:
        web = requests.get(mainUrl, headers=headers, timeout=15)
        web.raise_for_status()
        soup = BeautifulSoup(web.text, 'lxml')

        temps = soup.find_all('div', {'class': 'ipsType_break ipsContained'})
        
        if len(temps) < 25:
            logger.warning("Not enough movies found on the page")
            return [], {}

        for i in range(min(25, len(temps))):
            try:
                title = temps[i].findAll('a')[0].text.strip()
                link = temps[i].find('a')['href']
                if not link.startswith('http'):
                    link = f"{TAMILMV_URL}{link}"
                movie_list.append(title)
                
                # Fetch movie details
                movie_details = get_movie_details(link)
                real_dict[title] = movie_details
                
                # Small delay to avoid rate limiting
                time.sleep(0.1)
                
            except Exception as e:
                logger.error(f"Error processing movie {i}: {e}")
                continue

        return movie_list, real_dict
        
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

            message = f"""<b>📂 Movie:</b> <b>{movie_title}</b>

🧲 <b>Magnet Link:</b>
<code>{mag[p]}</code>
"""
            if torrent_link:
                message += f"""
📥 <b>Download Torrent:</b>
<a href="{torrent_link}">🔗 Click Here</a>
"""
            else:
                message += """
📥 <b>Torrent File:</b> ❌ Not Available
"""
            movie_details.append(message)

        return movie_details
        
    except Exception as e:
        logger.error(f"Error retrieving movie details: {e}")
        return []

# ============ FLASK ROUTES ============

@app.route('/')
def health_check():
    return "Movie Magnet Bot - Healthy", 200

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

# ============ MAIN ============

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