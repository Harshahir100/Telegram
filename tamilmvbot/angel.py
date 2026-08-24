import os
import time
import re
from urllib.parse import quote_plus, urljoin
from dotenv import load_dotenv
import telebot
from telebot import types
import requests
from bs4 import BeautifulSoup
from flask import Flask, request
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

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
# ============================================

bot = telebot.TeleBot(TOKEN, parse_mode='HTML', threaded=False)
app = Flask(__name__)

# Global variables
movie_list = []
real_dict = {}
search_results_cache = {}

# ============ ADVANCED FULL SITE SEARCH ============

def search_tamilmv_full_site(query):
    """
    Full site search using multiple strategies
    """
    if not query:
        return []
    
    query = query.strip()
    logger.info(f"Full site search for: {query}")
    
    all_results = []
    seen_urls = set()
    
    # STRATEGY 1: Search via site's built-in search
    logger.info("Strategy 1: Site search")
    results = site_search(query)
    if results:
        for r in results:
            url = r.get('url', '')
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_results.append(r)
        logger.info(f"Found {len(results)} results from site search")
    
    # STRATEGY 2: Search through all pages (sitemap/category)
    if len(all_results) < 10:
        logger.info("Strategy 2: Category page search")
        results = category_page_search(query)
        if results:
            for r in results:
                url = r.get('url', '')
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_results.append(r)
            logger.info(f"Found {len(results)} results from category search")
    
    # STRATEGY 3: Google site search (most reliable for old movies)
    if len(all_results) < 10:
        logger.info("Strategy 3: Google site search")
        results = google_site_search(query)
        if results:
            for r in results:
                url = r.get('url', '')
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_results.append(r)
            logger.info(f"Found {len(results)} results from Google search")
    
    # STRATEGY 4: Scrape recent pages (for new movies)
    if len(all_results) < 5:
        logger.info("Strategy 4: Recent pages scrape")
        results = scrape_recent_pages(query)
        if results:
            for r in results:
                url = r.get('url', '')
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_results.append(r)
            logger.info(f"Found {len(results)} results from recent pages")
    
    # Sort results by relevance (title match quality)
    all_results = sort_by_relevance(all_results, query)
    
    return all_results[:20]  # Return top 20 results

def site_search(query):
    """
    Strategy 1: Use 1Tamilmv's built-in search
    """
    results = []
    
    # Different search URL formats that 1Tamilmv might use
    search_formats = [
        f"{TAMILMV_URL}/index.php?/search/&q={quote_plus(query)}&type=forums_topic&search_in=titles",
        f"{TAMILMV_URL}/index.php?/search/&q={quote_plus(query)}&type=forums_topic",
        f"{TAMILMV_URL}/index.php?/search/&q={quote_plus(query)}",
        f"{TAMILMV_URL}/search/&q={quote_plus(query)}",
        f"{TAMILMV_URL}/search?q={quote_plus(query)}",
    ]
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1'
    }
    
    for search_url in search_formats:
        try:
            response = requests.get(search_url, headers=headers, timeout=20, allow_redirects=True)
            if response.status_code != 200:
                continue
            
            soup = BeautifulSoup(response.text, 'lxml')
            
            # Try multiple selectors for search results
            selectors = [
                ('li', {'class': 'ipsDataItem'}),
                ('article', {'class': 'ipsDataItem'}),
                ('div', {'class': 'ipsDataItem_main'}),
                ('div', {'class': 'ipsResult'}),
                ('li', {'class': 'ipsResult'}),
                ('div', {'class': 'cSearchResult'}),
            ]
            
            containers = []
            for tag, attrs in selectors:
                containers = soup.find_all(tag, attrs)
                if containers:
                    break
            
            if not containers:
                # Try generic search
                containers = soup.find_all(['li', 'article', 'div'], class_=re.compile(r'search|result|item'))
            
            for container in containers:
                try:
                    # Find title
                    title_elem = (
                        container.find('span', {'class': 'ipsDataItem_title'}) or
                        container.find('a', {'class': 'ipsDataItem_title'}) or
                        container.find('h3') or
                        container.find('h4') or
                        container.find('a', href=True)
                    )
                    
                    if not title_elem:
                        continue
                    
                    title = title_elem.text.strip()
                    if not title or len(title) < 3:
                        continue
                    
                    # Get link
                    link = title_elem.get('href') if hasattr(title_elem, 'get') else None
                    if not link:
                        link_elem = container.find('a', href=True)
                        if link_elem:
                            link = link_elem.get('href')
                    
                    if not link:
                        continue
                    
                    # Fix relative URLs
                    if link.startswith('/'):
                        link = f"{TAMILMV_URL}{link}"
                    elif not link.startswith('http'):
                        link = f"{TAMILMV_URL}/{link}"
                    
                    # Extract year
                    year_match = re.search(r'[\(\[](\d{4})[\)\]]', title)
                    year = year_match.group(1) if year_match else ""
                    
                    results.append({
                        'title': title,
                        'year': year,
                        'url': link,
                        'relevance': 100  # High relevance for direct search
                    })
                    
                except Exception as e:
                    continue
            
            if results:
                break  # Stop if we found results
                
        except Exception as e:
            logger.error(f"Site search error for {search_url}: {e}")
            continue
    
    return results

def category_page_search(query):
    """
    Strategy 2: Search through category pages
    """
    results = []
    
    # Common category/movie sections on 1Tamilmv
    categories = [
        '/forums/forum/23-tamil-movies',
        '/forums/forum/24-hollywood-movies',
        '/forums/forum/25-hindi-movies',
        '/forums/forum/26-malayalam-movies',
        '/forums/forum/27-telugu-movies',
        '/forums/forum/28-kannada-movies',
        '/forums/forum/4-tamil-movies',
        '/forums/forum/3-movies',
    ]
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    # Check first 3 pages of each category
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = []
        for category in categories[:3]:  # Limit to first 3 categories
            for page in [1, 2, 3]:  # Check pages 1-3
                page_url = f"{TAMILMV_URL}{category}?page={page}" if page > 1 else f"{TAMILMV_URL}{category}"
                futures.append(executor.submit(scrape_category_page, page_url, query, headers))
        
        for future in as_completed(futures):
            try:
                page_results = future.result(timeout=15)
                if page_results:
                    results.extend(page_results)
            except Exception as e:
                logger.error(f"Category page error: {e}")
    
    return results

def scrape_category_page(url, query, headers):
    """
    Scrape a category page for matching movies
    """
    results = []
    try:
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code != 200:
            return results
        
        soup = BeautifulSoup(response.text, 'lxml')
        
        # Find topic containers
        containers = soup.find_all('li', {'class': 'ipsDataItem'})
        if not containers:
            containers = soup.find_all('article', {'class': 'ipsDataItem'})
        
        for container in containers:
            try:
                title_elem = container.find('span', {'class': 'ipsDataItem_title'})
                if not title_elem:
                    title_elem = container.find('a', {'class': 'ipsDataItem_title'})
                if not title_elem:
                    continue
                
                title = title_elem.text.strip()
                
                # Check if query is in title (case insensitive)
                if query.lower() in title.lower():
                    link = title_elem.get('href')
                    if link:
                        if link.startswith('/'):
                            link = f"{TAMILMV_URL}{link}"
                        elif not link.startswith('http'):
                            link = f"{TAMILMV_URL}/{link}"
                        
                        # Extract year
                        year_match = re.search(r'[\(\[](\d{4})[\)\]]', title)
                        year = year_match.group(1) if year_match else ""
                        
                        results.append({
                            'title': title,
                            'year': year,
                            'url': link,
                            'relevance': 80
                        })
                        
            except Exception:
                continue
        
        return results
        
    except Exception:
        return []

def google_site_search(query):
    """
    Strategy 3: Search via Google using site: operator
    This finds movies that might not be in the recent listings
    """
    results = []
    try:
        # Use Google search with site: operator
        search_query = f'site:{TAMILMV_URL.replace("https://", "")} "{query}" movie'
        google_url = f"https://www.google.com/search?q={quote_plus(search_query)}"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        }
        
        response = requests.get(google_url, headers=headers, timeout=15)
        if response.status_code != 200:
            return results
        
        soup = BeautifulSoup(response.text, 'lxml')
        
        # Find Google search results
        for container in soup.find_all('div', {'class': 'g'}):
            try:
                # Get title
                title_elem = container.find('h3')
                if not title_elem:
                    continue
                
                title = title_elem.text.strip()
                
                # Get link
                link_elem = container.find('a', href=True)
                if not link_elem:
                    continue
                
                link = link_elem.get('href')
                if not link:
                    continue
                
                # Extract actual URL from Google's redirect
                if '/url?q=' in link:
                    import urllib.parse
                    parsed = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)
                    link = parsed.get('q', [link])[0]
                
                # Only keep links from tamilmv
                if TAMILMV_URL not in link:
                    continue
                
                # Clean the link
                link = link.split('&')[0]  # Remove tracking parameters
                
                # Extract year from title or snippet
                year_match = re.search(r'[\(\[](\d{4})[\)\]]', title)
                year = year_match.group(1) if year_match else ""
                
                results.append({
                    'title': title,
                    'year': year,
                    'url': link,
                    'relevance': 70
                })
                
            except Exception:
                continue
        
        return results[:15]
        
    except Exception as e:
        logger.error(f"Google search error: {e}")
        return []

def scrape_recent_pages(query):
    """
    Strategy 4: Scrape recent pages for matching movies
    """
    results = []
    seen_titles = set()
    
    # Check main page and next pages
    page_urls = [TAMILMV_URL]
    for i in range(2, 8):  # Check pages 2-7
        page_urls.append(f"{TAMILMV_URL}/page/{i}")
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(scrape_page_for_movies, url, query, headers): url for url in page_urls}
        
        for future in as_completed(futures):
            try:
                page_results = future.result(timeout=15)
                for movie in page_results:
                    title = movie.get('title', '').lower()
                    if title not in seen_titles:
                        seen_titles.add(title)
                        movie['relevance'] = 60
                        results.append(movie)
            except Exception as e:
                logger.error(f"Error scraping page: {e}")
    
    return results

def scrape_page_for_movies(url, query, headers):
    """
    Scrape a single page for movies matching query
    """
    results = []
    try:
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code != 200:
            return results
        
        soup = BeautifulSoup(response.text, 'lxml')
        
        # Find movie containers - multiple selectors
        containers = (
            soup.find_all('div', {'class': 'ipsType_break ipsContained'}) or
            soup.find_all('div', {'class': re.compile(r'ipsDataItem|ipsBox')}) or
            soup.find_all('li', {'class': 'ipsDataItem'})
        )
        
        for container in containers:
            try:
                title_elem = container.find('a')
                if not title_elem:
                    continue
                
                title = title_elem.text.strip()
                
                # Check if query matches
                if query.lower() in title.lower():
                    link = title_elem.get('href')
                    if link:
                        if link.startswith('/'):
                            link = f"{TAMILMV_URL}{link}"
                        elif not link.startswith('http'):
                            link = f"{TAMILMV_URL}/{link}"
                        
                        year_match = re.search(r'[\(\[](\d{4})[\)\]]', title)
                        year = year_match.group(1) if year_match else ""
                        
                        results.append({
                            'title': title,
                            'year': year,
                            'url': link,
                            'relevance': 60
                        })
                        
            except Exception:
                continue
        
        return results
        
    except Exception:
        return []

def sort_by_relevance(results, query):
    """
    Sort results by relevance to the search query
    """
    query_lower = query.lower()
    query_words = set(query_lower.split())
    
    for result in results:
        title_lower = result.get('title', '').lower()
        year = result.get('year', '')
        
        # Calculate relevance score
        score = 0
        
        # Exact match gets highest score
        if query_lower in title_lower:
            score += 50
        
        # Word matches
        title_words = set(title_lower.split())
        common_words = query_words & title_words
        score += len(common_words) * 10
        
        # Year match
        if year and query_lower in year:
            score += 20
        
        # Title length (shorter titles with exact match are better)
        if query_lower in title_lower:
            score += 30 if len(title_lower) < 50 else 10
        
        # Keep original relevance if available
        if 'relevance' in result:
            score += result['relevance']
        
        result['relevance_score'] = score
    
    # Sort by relevance score (highest first)
    results.sort(key=lambda x: x.get('relevance_score', 0), reverse=True)
    
    return results

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
        
        # Find magnet links - Multiple methods
        mag_links = []
        file_links = []
        
        # Method 1: Direct magnet links
        for a in soup.find_all('a', href=True):
            href = a.get('href', '')
            if 'magnet:' in href:
                mag_links.append(href)
            elif 'torrent' in href.lower() or a.get('data-fileext') == 'torrent':
                file_links.append(href)
        
        # Method 2: Code blocks
        if not mag_links:
            for code in soup.find_all(['pre', 'code']):
                text = code.text.strip()
                if 'magnet:' in text:
                    magnet_matches = re.findall(r'magnet:\?xt=urn:btih:[a-zA-Z0-9]+[^\s<>\'"]*', text)
                    mag_links.extend(magnet_matches)
        
        # Method 3: Script tags
        if not mag_links:
            for script in soup.find_all('script'):
                if script.string and 'magnet:' in script.string:
                    magnet_matches = re.findall(r'magnet:\?xt=urn:btih:[a-zA-Z0-9]+[^\s<>\'"]*', script.string)
                    mag_links.extend(magnet_matches)
        
        # Method 4: Hidden divs
        if not mag_links:
            for div in soup.find_all('div', {'style': re.compile(r'display\s*:\s*none|hidden')}):
                text = div.text.strip()
                if 'magnet:' in text:
                    magnet_matches = re.findall(r'magnet:\?xt=urn:btih:[a-zA-Z0-9]+[^\s<>\'"]*', text)
                    mag_links.extend(magnet_matches)
        
        if not mag_links:
            return []
        
        # Build messages
        movie_details = []
        
        for i, magnet in enumerate(mag_links[:5]):  # Limit to 5 magnet links
            torrent_link = None
            # Find corresponding torrent link if available
            if i < len(file_links):
                torrent_link = file_links[i]
            elif file_links:
                torrent_link = file_links[0]  # Use first torrent if available
            
            # Fix torrent URL
            if torrent_link and not torrent_link.startswith('http'):
                if torrent_link.startswith('/'):
                    torrent_link = f"{TAMILMV_URL}{torrent_link}"
                else:
                    torrent_link = f"{TAMILMV_URL}/{torrent_link}"
            
            message = f"""<b>🎬 Movie:</b> <b>{movie_title}</b>

🧲 <b>Magnet Link {i+1}:</b>
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

<blockquote><b>🎬 Get Magnet Links for any Movie from 1Tamilmv</b></blockquote>

⚙️ <b>How to use me:</b>

✯ <b>/search</b> - Search ANY movie and get magnet links
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
    query = message.text.replace('/search', '', 1).strip()
    
    if not query:
        bot.send_message(
            message.chat.id,
            "🔎 Enter a movie name to search.\n\nExamples:\n/search Inception\n/search Avengers\n/search KGF\n/search Vadakkupatti Ramasamy"
        )
        return
    
    searching_msg = bot.send_message(
        message.chat.id,
        f"🔍 Searching entire site for: <b>{query}</b>\n\n⏳ This may take 10-20 seconds..."
    )
    
    try:
        results = search_tamilmv_full_site(query)
        
        if not results:
            bot.edit_message_text(
                f"❌ No results found for: <b>{query}</b>\n\n💡 Tips:\n• Check spelling\n• Try shorter keywords\n• Try English or Tamil name\n• Example: /search Jailer",
                chat_id=message.chat.id,
                message_id=searching_msg.message_id
            )
            return
        
        keyboard = types.InlineKeyboardMarkup(row_width=1)
        for idx, movie in enumerate(results[:15]):
            title = movie.get('title', 'Unknown')
            year = movie.get('year', '')
            display_text = f"{title} {year}" if year else title
            if len(display_text) > 60:
                display_text = display_text[:57] + "..."
            keyboard.add(
                types.InlineKeyboardButton(
                    text=display_text,
                    callback_data=f"search_{idx}"
                )
            )
        
        search_results_cache[message.chat.id] = {
            'results': results,
            'timestamp': time.time()
        }
        
        bot.edit_message_text(
            f"🔎 <b>Search Results for:</b> {query}\n\n📌 Found {len(results)} results. Click on a movie to get magnet link:",
            chat_id=message.chat.id,
            message_id=searching_msg.message_id,
            reply_markup=keyboard
        )
        
    except Exception as e:
        logger.error(f"Search error: {e}")
        bot.edit_message_text(
            f"❌ Error searching: <b>{query}</b>\n\nPlease try again later.",
            chat_id=message.chat.id,
            message_id=searching_msg.message_id
        )

# ============ CALLBACK HANDLERS ============

@bot.callback_query_handler(func=lambda call: call.data.startswith("search_"))
def handle_search_callback(call):
    try:
        idx = int(call.data.split('_')[1])
        
        cache_data = search_results_cache.get(call.message.chat.id, {})
        results = cache_data.get('results', [])
        
        if idx >= len(results):
            bot.answer_callback_query(call.id, "❌ Result not available.")
            return
        
        movie = results[idx]
        movie_title = movie.get('title', 'Unknown')
        movie_url = movie.get('url', '')
        
        if not movie_url:
            bot.answer_callback_query(call.id, "❌ Movie link not available.")
            return
        
        fetching_msg = bot.send_message(
            call.message.chat.id,
            f"📥 Fetching magnet links for: <b>{movie_title}</b>\n\n⏳ Please wait..."
        )
        
        magnet_details = get_magnet_links_from_search(movie_url)
        
        if not magnet_details:
            bot.edit_message_text(
                f"❌ No magnet links found for: <b>{movie_title}</b>\n\n💡 Try another movie.",
                chat_id=call.message.chat.id,
                message_id=fetching_msg.message_id
            )
            return
        
        for detail in magnet_details:
            bot.send_message(call.message.chat.id, detail, disable_web_page_preview=True)
        
        bot.delete_message(
            chat_id=call.message.chat.id,
            message_id=fetching_msg.message_id
        )
        
        bot.answer_callback_query(call.id, f"✅ Magnet links sent for {movie_title}")
        
    except ValueError:
        bot.answer_callback_query(call.id, "❌ Invalid selection.")
    except Exception as e:
        logger.error(f"Error in search callback: {e}")
        bot.answer_callback_query(call.id, "❌ Error fetching details.")

@bot.callback_query_handler(func=lambda call: call.data.isdigit())
def movie_selection_callback(call):
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
                
                movie_details = get_movie_details(link)
                real_dict[title] = movie_details
                
                time.sleep(0.1)
                
            except Exception as e:
                logger.error(f"Error processing movie {i}: {e}")
                continue

        return movie_list, real_dict
        
    except Exception as e:
        logger.error(f"Error in tamilmv function: {e}")
        return [], {}

def get_movie_details(url):
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
    webhook_url = WEBHOOK_URL.rstrip('/')
    
    bot.remove_webhook()
    time.sleep(1)

    webhook_full_url = f"{webhook_url}/webhook"
    logger.info(f"Setting webhook to: {webhook_full_url}")
    bot.set_webhook(url=webhook_full_url)

    app.run(host='0.0.0.0', port=PORT)