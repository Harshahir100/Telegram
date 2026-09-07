import os
import time
import re
import logging
import requests
import telebot
from flask import Flask, request as flask_request
from telebot import types
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from urllib.parse import quote_plus

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ============================================================
# ENV
# ============================================================

load_dotenv()

TOKEN       = os.getenv("TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")          # e.g. https://yourapp.vercel.app
TAMILMV_URL = os.getenv("TAMILMV_URL", "")
PORT        = int(os.getenv("PORT", 3000))

if not TOKEN:
    raise RuntimeError("TOKEN environment variable is missing!")

# ============================================================
# BOT + FLASK
# ============================================================

bot = telebot.TeleBot(TOKEN, parse_mode="HTML", threaded=True)
app = Flask(__name__)

# ============================================================
# IN-MEMORY CACHE  (survives within one Vercel invocation)
# ============================================================

_cache = {
    "movie_list": [],
    "real_dict":  {},
    "search_cache": {},   # chat_id -> {results, timestamp}
}

# ============================================================
# BROWSER-LIKE SESSION
# ============================================================

def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection":      "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest":  "document",
        "Sec-Fetch-Mode":  "navigate",
        "Sec-Fetch-Site":  "none",
        "Sec-Fetch-User":  "?1",
    })
    return s


def warm_session(s: requests.Session):
    """Visit homepage once so Cloudflare sets cookies."""
    try:
        s.get(TAMILMV_URL, timeout=12)
        time.sleep(0.8)
    except Exception as e:
        logger.warning(f"Session warm-up failed (non-fatal): {e}")


# ============================================================
# SCRAPE – LATEST MOVIES  (/view)
# ============================================================

def scrape_movies():
    """
    Fetch the 10 latest movies from 1TamilMV homepage.
    Returns (movie_list, real_dict).
    """
    s = make_session()
    warm_session(s)

    try:
        resp = s.get(TAMILMV_URL, timeout=15)
        resp.raise_for_status()
        logger.info(f"Homepage status: {resp.status_code}, size: {len(resp.text)}")
    except Exception as e:
        logger.error(f"Homepage fetch error: {e}")
        return [], {}

    soup = BeautifulSoup(resp.text, "lxml")

    # ── Selector strategy: try multiple patterns ──────────────
    movie_divs = (
        soup.find_all("div", {"class": "ipsType_break ipsContained"})
        or soup.find_all("div", class_=lambda c: c and "ipsType_break" in c)
        or soup.select("h2.ipsDataItem_title")
        or soup.select("a[href*='/topic/']")
    )

    if not movie_divs:
        logger.error("No movie containers found on homepage")
        return [], {}

    movies  = []
    details = {}

    for div in movie_divs[:10]:
        try:
            a_tag = div.find("a") if hasattr(div, "find") else div
            if a_tag is None or a_tag.name != "a":
                a_tag = div if div.name == "a" else None
            if not a_tag:
                continue

            title = a_tag.get_text(strip=True)
            link  = a_tag.get("href", "")

            if not title or not link:
                continue
            if not link.startswith("http"):
                link = TAMILMV_URL + link

            logger.info(f"Found movie: {title[:50]}")
            movies.append(title)
            details[title] = get_movie_details(link, s)

        except Exception as e:
            logger.error(f"Error processing movie div: {e}")
            continue

    logger.info(f"Total movies scraped: {len(movies)}")
    return movies, details


# ============================================================
# SCRAPE – MAGNET LINKS FROM A TOPIC PAGE
# ============================================================

def get_movie_details(url: str, session: requests.Session = None) -> list:
    """
    Open a movie's topic page and return a list of formatted
    Telegram HTML messages, one per quality/link found.
    """
    s = session or make_session()

    try:
        resp = s.get(url, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"Topic page fetch error ({url}): {e}")
        return []

    soup = BeautifulSoup(resp.text, "lxml")

    # ── Movie title ───────────────────────────────────────────
    h1 = soup.find("h1")
    movie_title = h1.get_text(strip=True) if h1 else "Unknown Title"

    # ── Magnet links – 3 strategies ──────────────────────────
    mag_links = []

    # Strategy 1: <a href="magnet:…">
    for a in soup.find_all("a", href=True):
        if a["href"].startswith("magnet:"):
            mag_links.append(a["href"])

    # Strategy 2: inside <pre>/<code> blocks
    if not mag_links:
        for block in soup.select("pre, code"):
            found = re.findall(
                r'magnet:\?xt=urn:btih:[a-zA-Z0-9]+[^\s"\'<>]*',
                block.get_text()
            )
            mag_links.extend(found)

    # Strategy 3: regex scan on raw HTML (nuclear option)
    if not mag_links:
        mag_links = re.findall(
            r'magnet:\?xt=urn:btih:[a-zA-Z0-9]+[^\s"\'<>]*',
            resp.text
        )

    # ── Torrent file links ────────────────────────────────────
    torrent_links = [
        a["href"]
        for a in soup.find_all("a", {"data-fileext": "torrent", "href": True})
    ]

    if not mag_links and not torrent_links:
        logger.warning(f"No download links found on: {url}")
        return [f"<b>📂 {movie_title}</b>\n\n❌ No download links found on this page."]

    # ── Build one message per quality entry ───────────────────
    messages = []
    max_entries = max(len(mag_links), len(torrent_links))

    for i in range(min(max_entries, 5)):   # cap at 5 entries per movie
        magnet  = mag_links[i]   if i < len(mag_links)    else None
        torrent = torrent_links[i] if i < len(torrent_links) else None

        if torrent and not torrent.startswith("http"):
            torrent = TAMILMV_URL + torrent

        msg = f"<b>🎬 {movie_title}</b>\n\n"

        if magnet:
            msg += f"🧲 <b>Magnet Link:</b>\n<code>{magnet}</code>\n"

        if torrent:
            msg += f"\n📥 <b>Torrent File:</b>\n<a href='{torrent}'>⬇️ Download .torrent</a>"
        elif not magnet:
            msg += "\n❌ No links available"

        messages.append(msg)

    return messages


# ============================================================
# SEARCH – FULL SITE SEARCH  (/search)
# ============================================================

def search_tamilmv(query: str) -> list:
    """
    Search entire 1TamilMV site using IPB built-in search.
    Returns list of {title, year, url} dicts.
    """
    query = query.strip()
    if not query:
        return []

    s = make_session()
    warm_session(s)

    # IPB search URL patterns to try
    search_urls = [
        (f"{TAMILMV_URL}/index.php?/search/"
         f"&q={quote_plus(query)}&type=forums_topic&search_in=titles&search_and_or=or"),
        (f"{TAMILMV_URL}/index.php?/search/"
         f"&q={quote_plus(query)}&type=forums_topic"),
        f"{TAMILMV_URL}/search/?q={quote_plus(query)}",
    ]

    for url in search_urls:
        try:
            logger.info(f"Search URL: {url}")
            resp = s.get(url, timeout=20)
            logger.info(f"  Status: {resp.status_code}, Size: {len(resp.text)}")

            if resp.status_code != 200 or len(resp.text) < 500:
                continue

            results = _parse_search_results(resp.text)
            if results:
                return results[:12]

        except Exception as e:
            logger.error(f"Search URL error: {e}")
            continue

    # Fallback: filter recent movies by query
    logger.info("Falling back to recent-pages search")
    return _search_recent_pages(s, query)


def _parse_search_results(html: str) -> list:
    soup    = BeautifulSoup(html, "lxml")
    results = []

    # IPB 4.x – stream layout
    for sel in [
        "li.ipsStreamItem",
        "div.ipsStreamItem",
        "article.ipsStreamItem",
        "li[data-role='activityItem']",
    ]:
        items = soup.select(sel)
        if not items:
            continue

        for item in items[:15]:
            a = (
                item.select_one("h2 a")
                or item.select_one("h3 a")
                or item.select_one("a.ipsSeoLink")
                or item.select_one("a[href*='/topic/']")
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

        if results:
            break   # stop after first working selector

    # Fallback: any topic link
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
            if len(results) >= 12:
                break

    return _dedup(results)


def _search_recent_pages(s: requests.Session, query: str) -> list:
    results = []
    pages   = [TAMILMV_URL, f"{TAMILMV_URL}/index.php?/forum/"]

    for url in pages:
        try:
            resp = s.get(url, timeout=15)
            if resp.status_code != 200:
                continue

            soup = BeautifulSoup(resp.text, "lxml")

            for a in soup.select(
                "a[href*='/topic/'], "
                "div.ipsType_break a, "
                "h2.ipsDataItem_title a"
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
            logger.error(f"Recent pages error ({url}): {e}")
            continue

    return _dedup(results)[:12]


def _dedup(lst: list) -> list:
    seen, out = set(), []
    for r in lst:
        if r["url"] not in seen:
            seen.add(r["url"])
            out.append(r)
    return out


# ============================================================
# /start COMMAND
# ============================================================

@bot.message_handler(commands=["start"])
def cmd_start(message):
    text = (
        "<b>👋 Hello! Welcome to Angel Bot</b>\n\n"
        "🎬 <b>Get latest movies from 1Tamilmv</b>\n\n"
        "⚙️ <b>Commands:</b>\n"
        "/view — Latest movies list\n"
        "/search Movie Name — Search entire site\n\n"
        "<b>🔗 Share and Support 💝</b>"
    )
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("🔗 GitHub",     url="https://github.com/SudoR2spr"),
        types.InlineKeyboardButton("⚡ Powered By", url="https://t.me/Opleech_WD"),
    )
    bot.send_message(message.chat.id, text, reply_markup=kb)


# ============================================================
# /view COMMAND
# ============================================================

@bot.message_handler(commands=["view"])
def cmd_view(message):
    chat_id  = message.chat.id
    wait_msg = bot.send_message(chat_id, "⏳ <b>Fetching latest movies…</b>")

    try:
        movies, details = scrape_movies()

        if not movies:
            bot.edit_message_text(
                "❌ <b>Could not fetch movies right now.</b>\n\n"
                "The site might be temporarily blocking requests.\n"
                "Please try again in a minute.",
                chat_id=chat_id,
                message_id=wait_msg.message_id,
            )
            return

        # Save to in-memory cache
        _cache["movie_list"] = movies
        _cache["real_dict"]  = details

        bot.delete_message(chat_id, wait_msg.message_id)

        # Build keyboard (2 columns, max 15 movies)
        kb = types.InlineKeyboardMarkup(row_width=1)
        for i, title in enumerate(movies[:15]):
            label = title[:45] + "…" if len(title) > 45 else title
            kb.add(types.InlineKeyboardButton(text=f"🎬 {label}", callback_data=f"view_{i}"))

        bot.send_message(
            chat_id,
            f"🎬 <b>Latest Movies ({len(movies)} found)</b>\n\n👇 Select a movie:",
            reply_markup=kb,
        )

    except Exception as e:
        logger.error(f"cmd_view error: {e}")
        bot.edit_message_text(
            f"❌ <b>Error:</b> {str(e)[:200]}",
            chat_id=chat_id,
            message_id=wait_msg.message_id,
        )


# ============================================================
# /search COMMAND
# ============================================================

@bot.message_handler(commands=["search"])
def cmd_search(message):
    query = message.text.replace("/search", "", 1).strip()

    if not query:
        bot.send_message(
            message.chat.id,
            "🔎 Movie name likhna bhool gaye!\n\n"
            "<b>Examples:</b>\n"
            "/search Inception\n"
            "/search KGF\n"
            "/search Pushpa 2",
        )
        return

    status = bot.send_message(message.chat.id, f"🔍 <b>Searching:</b> {query}\n⏳ Please wait…")

    try:
        results = search_tamilmv(query)

        if not results:
            bot.edit_message_text(
                f"❌ <b>No results for:</b> {query}\n\n"
                "💡 Tips:\n"
                "• English spelling try karo\n"
                "• Shorter name try karo\n"
                "• Year add karo: /search KGF 2022",
                chat_id=message.chat.id,
                message_id=status.message_id,
            )
            return

        # Cache results for callback
        _cache["search_cache"][message.chat.id] = {
            "results":   results,
            "timestamp": time.time(),
        }

        kb = types.InlineKeyboardMarkup(row_width=1)
        for i, movie in enumerate(results):
            year  = movie.get("year", "")
            label = movie["title"]
            if year:
                label += f" ({year})"
            if len(label) > 60:
                label = label[:57] + "…"
            kb.add(types.InlineKeyboardButton(text=label, callback_data=f"search_{i}"))

        bot.edit_message_text(
            f"🔎 <b>Results for:</b> {query}\n"
            f"📌 {len(results)} movie(s) mili — click karo magnet ke liye:",
            chat_id=message.chat.id,
            message_id=status.message_id,
            reply_markup=kb,
        )

    except Exception as e:
        logger.error(f"cmd_search error: {e}")
        bot.edit_message_text(
            f"❌ Search error. Please try again.\n<code>{str(e)[:200]}</code>",
            chat_id=message.chat.id,
            message_id=status.message_id,
        )


# ============================================================
# CALLBACK HANDLERS
# ============================================================

@bot.callback_query_handler(func=lambda c: c.data.startswith("view_"))
def cb_view(call):
    try:
        idx     = int(call.data.split("_")[1])
        movies  = _cache["movie_list"]
        details = _cache["real_dict"]

        if idx >= len(movies):
            bot.answer_callback_query(call.id, "❌ Movie not found — /view dobara chalao")
            return

        title   = movies[idx]
        msgs    = details.get(title, [])

        bot.answer_callback_query(call.id)

        if not msgs:
            bot.send_message(call.message.chat.id, "❌ No download links available for this movie.")
            return

        for msg in msgs[:5]:
            bot.send_message(call.message.chat.id, msg, disable_web_page_preview=True)

    except Exception as e:
        logger.error(f"cb_view error: {e}")
        bot.answer_callback_query(call.id, "❌ Error fetching details.")


@bot.callback_query_handler(func=lambda c: c.data.startswith("search_"))
def cb_search(call):
    try:
        idx        = int(call.data.split("_")[1])
        cache_data = _cache["search_cache"].get(call.message.chat.id, {})
        results    = cache_data.get("results", [])

        if idx >= len(results):
            bot.answer_callback_query(call.id, "❌ Result no longer available — /search dobara karo")
            return

        movie = results[idx]
        title = movie["title"]
        url   = movie["url"]

        fetching = bot.send_message(
            call.message.chat.id,
            f"📥 <b>Fetching links for:</b>\n{title}\n⏳ Please wait…"
        )

        s    = make_session()
        msgs = get_movie_details(url, s)

        bot.delete_message(call.message.chat.id, fetching.message_id)

        if not msgs:
            bot.send_message(
                call.message.chat.id,
                f"❌ No magnet links found for:\n<b>{title}</b>"
            )
            bot.answer_callback_query(call.id)
            return

        for msg in msgs[:5]:
            bot.send_message(call.message.chat.id, msg, disable_web_page_preview=True)

        bot.answer_callback_query(call.id, f"✅ Done!")

    except Exception as e:
        logger.error(f"cb_search error: {e}")
        bot.answer_callback_query(call.id, "❌ Error. Please try again.")


# ============================================================
# FLASK ROUTES
# ============================================================

@app.route("/", methods=["GET"])
def home():
    return "Angel Bot is running! 🚀", 200


@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        if flask_request.headers.get("content-type") != "application/json":
            return "Invalid content type", 403

        json_data = flask_request.get_data().decode("utf-8")
        update    = telebot.types.Update.de_json(json_data)
        bot.process_new_updates([update])
        return "OK", 200

    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return "Webhook error", 500


@app.route("/set-webhook", methods=["GET"])
def set_webhook_route():
    """Call this URL once after deploying to register the webhook."""
    try:
        webhook_url = f"{WEBHOOK_URL.rstrip('/')}/webhook"
        bot.remove_webhook()
        time.sleep(1)
        result = bot.set_webhook(url=webhook_url)
        return f"Webhook set: {webhook_url} → {result}", 200
    except Exception as e:
        return f"Webhook setup failed: {e}", 500


# ============================================================
# MAIN  (local development only)
# ============================================================

if __name__ == "__main__":
    logger.info("Running locally with polling…")
    bot.remove_webhook()
    bot.polling(non_stop=True, interval=1)