import time
import re
import json
import os
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError, Browser, Page, BrowserContext
from gnews import GNews
import trafilatura
import requests
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# --- Global constant for network blocking ---
BLOCK_PATTERNS = [
    "cookielaw.org", "onetrust.com", "trustarc.com", "cookiebot.com",
    "consensu.org", "google-analytics.com", "googletagmanager.com",
    "doubleclick.net", "adservice.google.com"
]

def log(message):
    """Prints a message with a timestamp."""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

def prime_google_context(context: BrowserContext):
    """
    A simple and patient function to prime the Google context.
    It clicks the consent button and uses a static wait to avoid race conditions.
    """
    log("Priming Google context to handle consent wall...")
    page = None
    try:
        page = context.new_page()
        page.goto("https://news.google.com", wait_until='domcontentloaded', timeout=30000)
        
        if "consent.google.com" in page.url:
            log("   -> Google consent page detected. Clicking 'Accept all'...")
            page.get_by_role("button", name=re.compile("Accept all", re.IGNORECASE)).click(timeout=10000)
            log("   -> 'Accept all' clicked. Waiting for 3 seconds for page to stabilize...")
            page.wait_for_timeout(3000)
            log("   -> Context should now be primed.")
        else:
            log("   -> No consent page detected. Context is likely already primed.")
    except Exception as e:
        log(f"   -> An error occurred while priming Google context: {e}")
    finally:
        if page: page.close()

def resolve_google_redirect(url: str, context: BrowserContext) -> str:
    """A simple function to resolve redirects using the already-primed Google context."""
    final_url = None
    page = None
    try:
        page = context.new_page()
        page.goto(url, wait_until='domcontentloaded', timeout=20000)
        page.wait_for_timeout(2000)
        final_url = page.url
        if "google.com" in final_url:
            log(f"      -> FAILED to resolve redirect. Still on a Google domain: {final_url}"); return None
        log(f"      -> Redirect resolved: {final_url}")
    except Exception as e:
        log(f"      -> FAILED to resolve redirect for {url}. Reason: {e}"); return None
    finally:
        if page: page.close()
    return final_url

def fetch_tradingview_yesterday_data(browser: Browser, asset_name: str, asset_symbol: str):
    """Fetches OHLC data using a clean, isolated context with robust popup handling."""
    context = None
    try:
        context = browser.new_context()
        page = context.new_page()
        page.set_viewport_size({"width": 1920, "height": 1080})
        log(f"Processing Price Data for {asset_name}...")
        
        page.goto(f"https://www.tradingview.com/chart/?symbol={asset_symbol.replace(':', '%3A')}", wait_until="domcontentloaded", timeout=90000)
        
        log("   -> Clearing potential popups...")
        try:
            page.wait_for_timeout(3000) 

            page.keyboard.press("Escape")
            page.wait_for_timeout(500)

            cookie_btn = page.get_by_role("button", name="Accept all")
            if cookie_btn.is_visible():
                cookie_btn.click()
                log("      -> Cookie banner accepted.")

            overlays = page.locator('div[data-name="overlap-manager-root"] button[class*="close"], button[aria-label="Close"], button[name="Got it!"]')
            if overlays.count() > 0:
                for i in range(overlays.count()):
                    if overlays.nth(i).is_visible():
                        overlays.nth(i).click(force=True)
                        log("      -> Closed an overlay/popup.")
                        page.wait_for_timeout(500)
                        
        except Exception as e:
            log(f"      -> Warning during popup clearing: {e}")

        chart_area = page.locator("div.chart-gui-wrapper")
        
        log("   -> Activating chart area...")
        chart_area.click(position={'x': 100, 'y': 100}, force=True)
        
        
        def get_ohlc_values():
            try:
                
                o_text = page.get_by_text(re.compile(r"^O[\d.,]+")).first.inner_text(timeout=500)
                h_text = page.get_by_text(re.compile(r"^H[\d.,]+")).first.inner_text(timeout=500)
                l_text = page.get_by_text(re.compile(r"^L[\d.,]+")).first.inner_text(timeout=500)
                c_text = page.get_by_text(re.compile(r"^C[\d.,]+")).first.inner_text(timeout=500)
                
                
                def clean_val(txt):
                    return float(re.sub(r"[^\d.]", "", txt.replace(",", "")))

                return {
                    "open": clean_val(o_text),
                    "high": clean_val(h_text),
                    "low": clean_val(l_text),
                    "close": clean_val(c_text)
                }
            except Exception: return None

        
        log("   -> Finding the most recent historical candle...")
        chart_area.press('Home') 
        page.wait_for_timeout(500)
        chart_area.press('End')  
        page.wait_for_timeout(1000)

        
        last_known_close = 0.0
        
        
        chart_area.press('End') 
        page.wait_for_timeout(1000)
        
        
        final_ohlc = get_ohlc_values()
        
        if not final_ohlc:
            chart_area.press('ArrowLeft')
            page.wait_for_timeout(200)
            final_ohlc = get_ohlc_values()

        if not final_ohlc: 
            raise Exception("Failed to retrieve final OHLC values after navigation.")

        if "/" in asset_name:
            price_format = ",.4f"
        else:
            price_format = ",.3f"
        
        day_range = final_ohlc['high'] - final_ohlc['low']
        
        log(f"   -> Success: {asset_name} closed at {final_ohlc['close']}")

        return {"asset_name": asset_name, "symbol": asset_symbol, "status": "Success", "data": {
            "day_range": f"{day_range:{price_format}}",
            "open": f"{final_ohlc['open']:{price_format}}",
            "close": f"{final_ohlc['close']:{price_format}}",
            "high": f"{final_ohlc['high']:{price_format}}",
            "low": f"{final_ohlc['low']:{price_format}}"
        }}

    except Exception as e:
        log(f"!!! ERROR processing {asset_name}: {e}")
        # Capture screenshot on error for debugging
        try:
            timestamp = datetime.now().strftime("%H%M%S")
            page.screenshot(path=f"error_{asset_name}_{timestamp}.png")
            log(f"      -> Screenshot saved to error_{asset_name}_{timestamp}.png")
        except: pass
        return {"asset_name": asset_name, "symbol": asset_symbol, "status": "Failed", "error": str(e)}
    finally:
        if context: context.close()

def fetch_and_scrape_news(browser: Browser, google_context: BrowserContext, search_queries, max_to_save=3):
    """Uses the primed Google context for redirects and clean contexts for scraping."""
    try:
        google_news = GNews(language='en', country='US', period='1d', max_results=30)
        raw_articles = [];
        for i, query in enumerate(search_queries):
            log(f"   News Hunt Tier {i+1}: {query}")
            results = google_news.get_news(query)
            if results: raw_articles = results; break
        unique_articles = {article['url']: article for article in raw_articles}.values()
        successfully_scraped_articles = []
        for article_info in unique_articles:
            if len(successfully_scraped_articles) >= max_to_save: break
            log(f"   Attempting to process article: {article_info['title']}")
            final_url = resolve_google_redirect(article_info['url'], google_context)
            if not final_url: continue
            scraping_context = None
            try:
                user_agent_string = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36"
                scraping_context = browser.new_context(user_agent=user_agent_string)
                page = scraping_context.new_page()
                page.route(re.compile("|".join(BLOCK_PATTERNS)), lambda route: route.abort())
                page.route(re.compile(r"(\.png$)|(\.jpeg$)|(\.jpg$)|(\.gif$)|(\.css$)"), lambda route: route.abort())
                page.goto(final_url, wait_until="domcontentloaded", timeout=60000)
                html = page.content()
                body_text = trafilatura.extract(html, include_comments=False, include_tables=False)
                if not body_text or len(body_text) < 250:
                    log(f"      -> SCRAPE FAILED: trafilatura could not extract a valid article body."); continue
                successfully_scraped_articles.append({"title": article_info['title'], "source": article_info['publisher']['title'], "url": final_url, "body": body_text[:4000]})
            except Exception as e:
                log(f"       -> SCRAPE FAILED for {final_url}. Reason: {e}")
            finally:
                if scraping_context: scraping_context.close()
        return successfully_scraped_articles
    except Exception as e:
        log(f"!!! ERROR fetching news for query '{search_queries[0]}': {e}"); return []

def generate_market_summary(scraped_articles, asset_name, api_key, model):
    """
    Generates a financial market summary using an AI model.
    """
    if not api_key:
        return "**ERROR: API key is missing.**"
    if not scraped_articles:
        # This is not an error, but a valid state for the report.
        return "No relevant news articles were found to generate a summary."

    log(f"Aggregating {len(scraped_articles)} articles about {asset_name} for AI summary...")
    dossier = "".join(
        [f"--- ARTICLE {i+1}: {a['title']} ---\n{a['body']}\n\n" for i, a in enumerate(scraped_articles)]
    )
    
    prompt = f"""
### ROLE ###
You are an expert financial analyst. Your task is to analyze news articles about {asset_name} and generate a visually structured, concise market briefing for a busy sales agent.

### INSTRUCTIONS ###
1. Your tone must be professional, direct, and highly scannable.
2. Use Markdown for formatting.
3. **Bold** key figures, price levels, and critical terms (e.g., **$220**, **bullish momentum**, **hawkish**).
4. Strictly follow the structure and style of the example below.

### EXAMPLE OF PERFECT OUTPUT ###
### Gold (XAU/USD) Market Briefing

* **Key Drivers:** Persistent U.S. inflation data is increasing bets on a more **hawkish** Federal Reserve, strengthening the dollar.
* **Price Action:** Broke below the key psychological level of **$2,300**, showing significant **bearish** momentum.
* **Technical Outlook:** Immediate support is near the 50-day moving average at **$2,280**, with resistance at **$2,300**.

---
**Overall Sentiment:** Negative 📉

### YOUR TASK ###
Now, generate the same report for **{asset_name}** based on the following articles. Your output must be in Markdown and match the example's format exactly. Do not add any preamble or explanation.

### ARTICLES ###
{dossier}
"""

    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}]},
            timeout=180
        )
        response.raise_for_status()
        raw_text = response.json()['choices'][0]['message']['content'].strip()
        
        # THIS IS THE FIX for '\n' artifacts.
        cleaned_text = raw_text.replace('\\n', '\n')
        
        return cleaned_text

    except requests.exceptions.RequestException as e:
        log(f"ERROR: AI summary request failed due to a network issue: {e}")
        return f"ERROR: Could not connect to the AI service: {e}"
    except Exception as e:
        log(f"ERROR: AI summary request failed with an unexpected error: {e}")
        return f"ERROR: An unexpected error occurred during AI summary generation: {e}"

def generate_markdown_report(all_snapshots, folder):
    date_str = datetime.now().strftime("%Y-%m-%d")
    briefings_dir = os.path.join(folder, "briefings")
    os.makedirs(briefings_dir, exist_ok=True)
    filename = os.path.join(briefings_dir, f"briefing_{date_str}.md")
    log(f"Generating Markdown report at '{filename}'...")
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(f"# Daily Market Briefing - {date_str}\n\n")
        for snap in all_snapshots:
            if snap['status'] != 'Success': continue
            f.write(f"## {snap['asset_name']} ({snap['symbol']})\n\n### Market Snapshot\n* **Yesterday's Close:** {snap['data']['close']}\n* **Day's Range:** {snap['data']['day_range']}\n* **Open:** {snap['data']['open']}\n* **High:** {snap['data']['high']}\n* **Low:** {snap['data']['low']}\n\n### AI Market Summary\n{snap.get('market_summary', 'N/A')}\n\n### Source Articles\n")
            if snap.get('source_articles'):
                for i, art in enumerate(snap['source_articles']): f.write(f"{i+1}. [{art['title']}]({art['url']}) - *{art['source']}*\n")
            else: f.write("No source articles found.\n")
            f.write("\n---\n\n")
        f.write(f"*Report generated at {datetime.now().strftime('%H:%M:%S UTC')}*")

# --- Main Execution Block ---
if __name__ == "__main__":
    main_start_time = time.time()
    log("--- SCRIPT START ---")
    
    try:
        with open(os.path.join(BASE_DIR, 'config.json'), 'r') as f: config = json.load(f)
        load_dotenv(dotenv_path=os.path.join(BASE_DIR, '.env'))
        OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
        
        assets_to_scrape = config.get('assets', [])
        ai_model = config['news_config']['ai_model']
        log(f"Using AI Model: {ai_model}")
        
        all_snapshots = []
        with sync_playwright() as p:
            browser_args = ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu']
            browser = p.chromium.launch(headless=True, args=browser_args)
            
            log("Creating and priming a dedicated browser context for Google operations...")
            google_context = browser.new_context()
            prime_google_context(google_context)
            
            for asset in assets_to_scrape:
                asset_start_time = time.time()
                log(f"--- Starting asset: {asset['name']} ---")
                
                snapshot = fetch_tradingview_yesterday_data(browser, asset['name'], asset['symbol'])
                
                if snapshot and snapshot['status'] == 'Success':
                    news_start_time = time.time()
                    search_queries = asset.get('search_queries', [])
                    related_news = fetch_and_scrape_news(browser, google_context, search_queries)
                    log(f"News fetching took {time.time() - news_start_time:.2f} seconds.")
                    
                    ai_start_time = time.time()
                    market_summary = generate_market_summary(related_news, asset['name'], OPENROUTER_API_KEY, ai_model)
                    log(f"AI summarization took {time.time() - ai_start_time:.2f} seconds.")

                    snapshot['market_summary'] = market_summary
                    snapshot['source_articles'] = related_news
                
                if snapshot: all_snapshots.append(snapshot)
                log(f"--- Finished asset: {asset['name']}. Total time: {time.time() - asset_start_time:.2f} seconds. ---")

            google_context.close()
            browser.close()

        snapshot_folder = os.path.join(BASE_DIR, "snapshots")
        os.makedirs(snapshot_folder, exist_ok=True)
        date_str = datetime.now().strftime("%m_%d_%Y")
        json_filename = os.path.join(snapshot_folder, f"snapshot_{date_str}.json")
        log(f"Saving raw JSON data to '{json_filename}'...")
        with open(json_filename, 'w') as f: json.dump(all_snapshots, f, indent=4)
        generate_markdown_report(all_snapshots, snapshot_folder)
        
    except Exception as e:
        log(f"A critical error occurred in the main block: {e}")

    log(f"--- SCRIPT END. Total execution time: {time.time() - main_start_time:.2f} seconds. ---")
