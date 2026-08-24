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

movie_list = []
real_dict = {}
search_results_cache = {}


# ─────────────────────────────────────────────────────────────────
#  BROWSER-LIKE SESSION  (Cloudflare bypass)
# ─────────────────────────────────────────────────────────────────

def make_session():
    """
    Returns a requests.Session that looks like a real Chrome browser.
    1TamilMV uses Cloudflare; sending proper headers avoids most 403s.
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    })
    return session


def warm_session(session):
    """
    Visit the homepage first so Cloudflare sets cf_clearance cookie.
    Call this once before doing any search or page requests.
    """
    try:
        session.get(TAMILMV_URL, timeout=15)
        time.sleep(1)          # let Cloudflare settle
    except Exception as e:
        logger.warning(f"Session warm-up failed (non-fatal): {e}")


# ─────────────────────────────────────────────────────────────────
#  SEARCH  (puri 1TamilMV site mein)
# ─────────────────────────────────────────────────────────────────

def search_tamilmv(query):
    """
    Search the entire 1TamilMV site using its built-in IPB search.
    Tries three URL patterns and multiple CSS selector strategies
    so it keeps working even after minor HTML changes.
    """
    if not query or not query.strip():
        return []

    query = query.strip()
    logger.info(f"Searching 1TamilMV for: {query}")

    session = make_session()
    warm_session(session)   # homepage visit → Cloudflare cookie

    # ── 1.  Try every known IPB search URL pattern ──────────────
    search_urls = [
        # titles-only search (most precise)
        f"{TAMILMV_URL}/index.php?/search/&q={quote_plus(query)}"
        f"&type=forums_topic&search_in=titles&search_and_or=or",

        # broader search (titles + content)
        f"{TAMILMV_URL}/index.php?/search/&q={quote_plus(query)}"
        f"&type=forums_topic",

        # some mirrors use a slightly different path
        f"{TAMILMV_URL}/search/?q={quote_plus(query)}",
    ]

    for url in search_urls:
        logger.info(f"Trying search URL: {url}")
        try:
            resp = session.get(url, timeout=20)
            logger.info(f"  Status: {resp.status_code}  Size: {len(resp.text)}")

            if resp.status_code != 200 or len(resp.text) < 500:
                continue

            results = _parse_search_results(resp.text, query)
            if results:
                logger.info(f"  Found {len(results)} results")
                return results

        except Exception as e:
            logger.error(f"  Request error: {e}")
            continue

    # ── 2.  Fallback: search inside recently added movies ────────
    logger.info("Search URLs gave 0 results → trying recent-posts fallback")
    return _search_recent_pages(session, query)


def _parse_search_results(html, query):
    """
    Parse IPB (Invision Power Board) search-result HTML.
    Tries every known selector variation so mirror HTML differences
    don't break the bot.
    """
    soup = BeautifulSoup(html, "lxml")
    results = []

    # ── Selector set A: IPB 4.x stream layout ───────────────────
    #    <li class="ipsStreamItem ..."> … <h2><a href="…">Title</a></h2>
    items = (
        soup.select("li.ipsStreamItem")
        or soup.select("div.ipsStreamItem")
        or soup.select("article.ipsStreamItem")
    )

    if items:
        for item in items[:15]:
            try:
                # Title link variations
                a = (
                    item.select_one("h2.ipsStreamItem_title a")
                    or item.select_one("span.ipsStreamItem_title a")
                    or item.select_one("a.ipsStreamItem_title")
                    or item.select_one("h2 a")
                    or item.select_one("h3 a")
                    or item.select_one("a[href*='topic']")
                )
                if not a:
                    continue

                title = a.get_text(strip=True)
                link  = a.get("href", "")
                if not link.startswith("http"):
                    link = TAMILMV_URL + link

                year_m = re.search(r'\((\d{4})\)', title)
                results.append({
                    "title": title,
                    "year":  year_m.group(1) if year_m else "",
                    "url":   link,
                })
            except Exception:
                continue

    # ── Selector set B: IPB 4.x data-list layout ────────────────
    #    <li data-role="activityItem"> … <a class="ipsSeoLink">Title</a>
    if not results:
        items = soup.select("li[data-role='activityItem']")
        for item in items[:15]:
            try:
                a = (
                    item.select_one("a.ipsSeoLink")
                    or item.select_one("a[href*='topic']")
                )
                if not a:
                    continue

                title = a.get_text(strip=True)
                link  = a.get("href", "")
                if not link.startswith("http"):
                    link = TAMILMV_URL + link

                year_m = re.search(r'\((\d{4})\)', title)
                results.append({
                    "title": title,
                    "year":  year_m.group(1) if year_m else "",
                    "url":   link,
                })
            except Exception:
                continue

    # ── Selector set C: flat list of topic links ─────────────────
    #    Some mirrors just list <a href="/topic/123-movie-name/">
    if not results:
        for a in soup.select("a[href*='/topic/']"):
            title = a.get_text(strip=True)
            if len(title) < 5:
                continue
            link = a.get("href", "")
            if not link.startswith("http"):
                link = TAMILMV_URL + link

            year_m = re.search(r'\((\d{4})\)', title)
            results.append({
                "title": title,
                "year":  year_m.group(1) if year_m else "",
                "url":   link,
            })
            if len(results) >= 15:
                break

    # ── Selector set D: any <a> whose text contains the query ────
    if not results:
        for a in soup.find_all("a", href=True):
            title = a.get_text(strip=True)
            if query.lower() in title.lower() and len(title) > 5:
                link = a.get("href", "")
                if not link.startswith("http"):
                    link = TAMILMV_URL + link
                year_m = re.search(r'\((\d{4})\)', title)
                results.append({
                    "title": title,
                    "year":  year_m.group(1) if year_m else "",
                    "url":   link,
                })
                if len(results) >= 15:
                    break

    # Deduplicate by URL
    seen  = set()
    clean = []
    for r in results:
        if r["url"] not in seen:
            seen.add(r["url"])
            clean.append(r)

    return clean


def _search_recent_pages(session, query):
    """
    Fallback: scrape the first few pages of the 'latest topics' list
    and filter titles that contain the query.
    This covers movies that haven't been indexed by IPB search yet.
    """
    results = []

    # 1TamilMV latest topics pages
    page_urls = [
        f"{TAMILMV_URL}/",
        f"{TAMILMV_URL}/index.php?/forum/",
        f"{TAMILMV_URL}/index.php?/forum/&page=2",
    ]

    for url in page_urls:
        try:
            resp = session.get(url, timeout=15)
            if resp.status_code != 200:
                continue

            soup = BeautifulSoup(resp.text, "lxml")

            # All typical topic-link selectors
            for a in soup.select(
                "a[href*='/topic/'], "
                "div.ipsType_break a, "
                "h2.ipsDataItem_title a, "
                "span.ipsDataItem_title a"
            ):
                title = a.get_text(strip=True)
                if query.lower() not in title.lower() or len(title) < 5:
                    continue

                link = a.get("href", "")
                if not link.startswith("http"):
                    link = TAMILMV_URL + link

                year_m = re.search(r'\((\d{4})\)', title)
                results.append({
                    "title": title,
                    "year":  year_m.group(1) if year_m else "",
                    "url":   link,
                })

        except Exception as e:
            logger.error(f"Recent-pages fallback error ({url}): {e}")
            continue

    # Deduplicate
    seen  = set()
    clean = []
    for r in results:
        if r["url"] not in seen:
            seen.add(r["url"])
            clean.append(r)

    return clean[:15]


# ─────────────────────────────────────────────────────────────────
#  MAGNET LINK EXTRACTOR  (unchanged logic, more selectors added)
# ─────────────────────────────────────────────────────────────────

def get_magnet_links_from_search(link):
    """Fetch magnet links from a 1TamilMV movie detail/topic page."""
    if not link:
        return []

    session = make_session()

    try:
        resp = session.get(link, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        title_el = soup.find("h1")
        movie_title = title_el.get_text(strip=True) if title_el else "Unknown"

        # ── Magnet links ────────────────────────────────────────
        mag_links = []

        # Selector 1: direct href
        for a in soup.find_all("a", href=True):
            if a["href"].startswith("magnet:"):
                mag_links.append(a["href"])

        # Selector 2: inside <pre> / <code> blocks
        if not mag_links:
            for block in soup.select("pre, code"):
                for m in re.findall(
                    r'magnet:\?xt=urn:btih:[a-zA-Z0-9]+[^\s"\'<>]*', block.get_text()
                ):
                    mag_links.append(m)

        # Selector 3: raw regex scan entire page
        if not mag_links:
            mag_links = re.findall(
                r'magnet:\?xt=urn:btih:[a-zA-Z0-9]+[^\s"\'<>]*', resp.text
            )

        if not mag_links:
            logger.warning(f"No magnet links found on: {link}")
            return []

        # ── Torrent file links ──────────────────────────────────
        file_links = [
            a["href"]
            for a in soup.find_all("a", {"data-fileext": "torrent", "href": True})
        ]

        # ── Build messages ──────────────────────────────────────
        messages = []
        for i, magnet in enumerate(mag_links):
            torrent = file_links[i] if i < len(file_links) else None
            if torrent and not torrent.startswith("http"):
                torrent = TAMILMV_URL + torrent

            msg = (
                f"<b>🎬 Movie:</b> <b>{movie_title}</b>\n\n"
                f"🧲 <b>Magnet Link:</b>\n"
                f"<code>{magnet}</code>\n"
            )
            if torrent:
                msg += f'\n📥 <b>Download Torrent:</b>\n<a href="{torrent}">🔗 Click Here</a>\n'
            else:
                msg += "\n📥 <b>Torrent File:</b> ❌ Not Available\n"

            messages.append(msg)

        return messages

    except Exception as e:
        logger.error(f"Error getting magnet links from {link}: {e}")
        return []


# ─────────────────────────────────────────────────────────────────
#  /view  HELPER FUNCTIONS  (unchanged)
# ─────────────────────────────────────────────────────────────────

def get_movie_details(url):
    session = make_session()
    try:
        html = session.get(url, timeout=15)
        html.raise_for_status()
        soup = BeautifulSoup(html.text, "lxml")

        mag = [a["href"] for a in soup.find_all("a", href=True) if "magnet:" in a["href"]]
        filelink = [a["href"] for a in soup.find_all("a", {"data-fileext": "torrent", "href": True})]

        if not mag:
            return []

        movie_title = soup.find("h1")
        movie_title = movie_title.text.strip() if movie_title else "Unknown Title"

        movie_details = []
        for p in range(len(mag)):
            torrent_link = filelink[p] if p < len(filelink) else None
            if torrent_link and not torrent_link.startswith("http"):
                torrent_link = f"{TAMILMV_URL}{torrent_link}"

            message = (
                f"<b>📂 Movie:</b> <b>{movie_title}</b>\n\n"
                f"🧲 <b>Magnet Link:</b>\n"
                f"<code>{mag[p]}</code>\n"
            )
            if torrent_link:
                message += f'\n📥 <b>Download Torrent:</b>\n<a href="{torrent_link}">🔗 Click Here</a>\n'
            else:
                message += "\n📥 <b>Torrent File:</b> ❌ Not Available\n"

            movie_details.append(message)

        return movie_details

    except Exception as e:
        logger.error(f"Error retrieving movie details: {e}")
        return []


def tamilmv():
    session = make_session()
    movie_list_local = []
    real_dict_local  = {}

    try:
        web = session.get(TAMILMV_URL, timeout=15)
        web.raise_for_status()
        soup = BeautifulSoup(web.text, "lxml")

        temps = soup.find_all("div", {"class": "ipsType_break ipsContained"})
        if len(temps) < 5:
            logger.warning("Not enough movies on main page")
            return [], {}

        for i in range(min(25, len(temps))):
            try:
                a_tag = temps[i].find("a")
                if not a_tag:
                    continue
                title = a_tag.text.strip()
                link  = a_tag["href"]
                if not link.startswith("http"):
                    link = f"{TAMILMV_URL}{link}"

                movie_list_local.append(title)
                real_dict_local[title] = get_movie_details(link)
                time.sleep(0.2)

            except Exception as e:
                logger.error(f"Error processing movie {i}: {e}")
                continue

    except Exception as e:
        logger.error(f"tamilmv() error: {e}")

    return movie_list_local, real_dict_local


def makeKeyboard(ml):
    markup = types.InlineKeyboardMarkup(row_width=1)
    for key, value in enumerate(ml[:25]):
        display = value[:50] if len(value) > 50 else value
        markup.add(types.InlineKeyboardButton(text=display, callback_data=str(key)))
    return markup


# ─────────────────────────────────────────────────────────────────
#  COMMAND HANDLERS
# ─────────────────────────────────────────────────────────────────

@bot.message_handler(commands=["start"])
def cmd_start(message):
    text = (
        "<b>👋 Hello! Welcome to Movie Magnet Bot</b>\n\n"
        "<blockquote><b>🎬 Get Magnet Links for any Movie</b></blockquote>\n\n"
        "⚙️ <b>How to use me:</b>\n\n"
        "✯ <b>/search Movie Name</b> — Search puri 1TamilMV site mein\n"
        "✯ <b>/view</b> — Latest movies dekho\n\n"
        "<blockquote><b>🔗 Share and Support 💝</b></blockquote>"
    )
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(
        types.InlineKeyboardButton("🔗 GitHub 🔗", url="https://github.com/SudoR2spr"),
        types.InlineKeyboardButton("⚡ Powered By",  url="https://t.me/Opleech_WD"),
    )
    bot.send_photo(
        chat_id=message.chat.id,
        photo="https://graph.org/file/4e8a1172e8ba4b7a0bdfa.jpg",
        caption=text,
        reply_markup=keyboard,
    )


@bot.message_handler(commands=["view"])
def cmd_view(message):
    bot.send_message(message.chat.id, "<b>🧲 Fetching latest movies…</b>")
    global movie_list, real_dict
    movie_list, real_dict = tamilmv()

    if not movie_list:
        bot.send_message(message.chat.id, "❌ Failed to fetch movies. Please try again later.")
        return

    bot.send_photo(
        chat_id=message.chat.id,
        photo="https://graph.org/file/4e8a1172e8ba4b7a0bdfa.jpg",
        caption="<b><blockquote>🔗 Select a Movie 🎬</blockquote></b>\n\n🔘 Please select a movie:",
        reply_markup=makeKeyboard(movie_list),
    )


@bot.message_handler(commands=["search"])
def cmd_search(message):
    query = message.text.replace("/search", "", 1).strip()

    if not query:
        bot.send_message(
            message.chat.id,
            "🔎 Movie name likhna bhool gaye!\n\n"
            "Example:\n"
            "/search Inception\n"
            "/search KGF\n"
            "/search Pushpa 2",
        )
        return

    # ── "Searching…" status message ─────────────────────────────
    status = bot.send_message(
        message.chat.id,
        f"🔍 <b>Searching:</b> {query}\n\n⏳ Please wait…",
    )

    try:
        results = search_tamilmv(query)

        if not results:
            bot.edit_message_text(
                f"❌ <b>No results found for:</b> {query}\n\n"
                "💡 Tips:\n"
                "• Try English spelling\n"
                "• Try year: <code>/search Inception 2010</code>\n"
                "• Try shorter name: <code>/search Incep</code>",
                chat_id=message.chat.id,
                message_id=status.message_id,
            )
            return

        # ── Build inline keyboard ────────────────────────────────
        keyboard = types.InlineKeyboardMarkup(row_width=1)
        for idx, movie in enumerate(results):
            title = movie.get("title", "Unknown")
            year  = movie.get("year",  "")
            label = f"{title} ({year})" if year else title
            if len(label) > 60:
                label = label[:57] + "…"
            keyboard.add(
                types.InlineKeyboardButton(text=label, callback_data=f"search_{idx}")
            )

        # Cache results for the callback handler
        search_results_cache[message.chat.id] = {
            "results":   results,
            "timestamp": time.time(),
        }

        bot.edit_message_text(
            f"🔎 <b>Results for:</b> {query}\n"
            f"📌 {len(results)} movie(s) found — click to get magnet link:",
            chat_id=message.chat.id,
            message_id=status.message_id,
            reply_markup=keyboard,
        )

    except Exception as e:
        logger.error(f"cmd_search error: {e}")
        bot.edit_message_text(
            f"❌ Error while searching. Please try again.\n<code>{e}</code>",
            chat_id=message.chat.id,
            message_id=status.message_id,
        )


# ─────────────────────────────────────────────────────────────────
#  CALLBACK HANDLERS
# ─────────────────────────────────────────────────────────────────

@bot.callback_query_handler(func=lambda call: call.data.startswith("search_"))
def cb_search(call):
    try:
        idx        = int(call.data.split("_")[1])
        cache_data = search_results_cache.get(call.message.chat.id, {})
        results    = cache_data.get("results", [])

        if idx >= len(results):
            bot.answer_callback_query(call.id, "❌ Result no longer available.")
            return

        movie = results[idx]
        title = movie.get("title", "Unknown")
        url   = movie.get("url",   "")

        if not url:
            bot.answer_callback_query(call.id, "❌ Movie link not available.")
            return

        fetching = bot.send_message(
            call.message.chat.id,
            f"📥 Fetching magnet links for:\n<b>{title}</b>\n\n⏳ Please wait…",
        )

        magnet_details = get_magnet_links_from_search(url)

        if not magnet_details:
            bot.edit_message_text(
                f"❌ No magnet links found for:\n<b>{title}</b>\n\n"
                "💡 Try another result.",
                chat_id=call.message.chat.id,
                message_id=fetching.message_id,
            )
            return

        bot.delete_message(call.message.chat.id, fetching.message_id)

        for detail in magnet_details:
            bot.send_message(call.message.chat.id, detail, disable_web_page_preview=True)

        bot.answer_callback_query(call.id, f"✅ Sent magnet links for {title}")

    except ValueError:
        bot.answer_callback_query(call.id, "❌ Invalid selection.")
    except Exception as e:
        logger.error(f"cb_search error: {e}")
        bot.answer_callback_query(call.id, "❌ Error fetching movie details.")


@bot.callback_query_handler(func=lambda call: call.data.isdigit())
def cb_view(call):
    global real_dict
    try:
        key = int(call.data)
        if key < len(movie_list):
            title = movie_list[key]
            details = real_dict.get(title, [])
            if details:
                for msg in details:
                    bot.send_message(call.message.chat.id, msg, disable_web_page_preview=True)
                bot.answer_callback_query(call.id, f"✅ Details sent for {title}")
            else:
                bot.send_message(call.message.chat.id, "❌ Movie details not available.")
                bot.answer_callback_query(call.id, "❌ No details available")
        else:
            bot.answer_callback_query(call.id, "❌ Invalid selection.")
    except Exception:
        bot.answer_callback_query(call.id, "❌ Invalid selection.")


# ─────────────────────────────────────────────────────────────────
#  FLASK ROUTES
# ─────────────────────────────────────────────────────────────────

@app.route("/")
def health_check():
    return "Movie Magnet Bot - Healthy", 200


@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        if not request.is_json:
            return "Invalid content type", 403
        update = telebot.types.Update.de_json(request.get_data().decode("utf-8"))
        bot.process_new_updates([update])
        return "", 200
    except Exception as e:
        logger.exception(f"Webhook error: {e}")
        return "Webhook error", 500


# ─────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    webhook_url = WEBHOOK_URL.rstrip("/")
    bot.remove_webhook()
    time.sleep(1)
    bot.set_webhook(url=f"{webhook_url}/webhook")
    logger.info(f"Webhook set: {webhook_url}/webhook")
    app.run(host="0.0.0.0", port=PORT)