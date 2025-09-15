import time
import re
import json
import os
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError, Browser, Page
from gnews import GNews
import trafilatura
import requests
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# (Logging and other functions are unchanged)
def log(message):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

# --- REWRITTEN: The new, high-speed price scraping function ---
def fetch_tradingview_yesterday_data(browser: Browser, asset_name: str, asset_symbol: str):
    url = f"https://www.tradingview.com/chart/?symbol={asset_symbol.replace(':', '%3A')}"
    context = browser.new_context()
    page = context.new_page()
    page.set_viewport_size({"width": 1920, "height": 1080})
    
    try:
        log(f"Processing Price Data for {asset_name}...")
        page.goto(url, wait_until="networkidle", timeout=90000)
        time.sleep(3)
        
        try:
            log("Attempting to accept cookies...")
            page.get_by_role("button", name="Accept all").click(timeout=7000)
            log("Cookie banner accepted.")
        except TimeoutError:
            log("No cookie banner found or it timed out.")
            pass

        chart_area = page.locator("div.chart-gui-wrapper")
        # Click on the right side of the chart to ensure it has focus
        chart_box = chart_area.bounding_box()
        if chart_box:
            click_x = chart_box['width'] * 0.9
            click_y = chart_box['height'] * 0.5
            log(f"Clicking chart at relative x={click_x}, y={click_y} to focus.")
            chart_area.click(position={'x': click_x, 'y': click_y})
        else:
            # Fallback to the old method if bounding_box is not available
            log("Could not get chart bounding box, using fallback click position.")
            chart_area.click(position={'x': 300, 'y': 200})
        
        try:
            log("Attempting to close 'Got it!' popup...")
            page.get_by_role("button", name="Got it!").click(timeout=7000)
            log("'Got it!' popup closed.")
        except TimeoutError:
            log("No 'Got it!' popup found or it timed out.")
            pass
        time.sleep(1)
        
        def get_ohlc_values():
            try:
                o_text = page.get_by_text(re.compile(r"^O[0-9,]+\.[0-9]+$")).inner_text()
                h_text = page.get_by_text(re.compile(r"^H[0-9,]+\.[0-9]+$")).inner_text()
                l_text = page.get_by_text(re.compile(r"^L[0-9,]+\.[0-9]+$")).inner_text()
                c_text = page.get_by_text(re.compile(r"^C[0-9,]+\.[0-9]+$")).inner_text()
                ohlc = {"open": float(o_text[1:].replace(",", "")), "high": float(h_text[1:].replace(",", "")), "low": float(l_text[1:].replace(",", "")), "close": float(c_text[1:].replace(",", ""))}
                log(f"   -> OHLC values detected: {ohlc}")
                return ohlc
            except Exception as e:
                log(f"   -> Could not detect OHLC values: {e}")
                raise

        # --- THE KEY FIX: The new "Jump and Hunt" Algorithm ---
        log("Finding the most recent historical candle...")
        
        log("   Step 1: Jumping to the beginning of the chart history...")
        chart_area.press('Home') # This is the high-speed jump
        page.wait_for_timeout(500) # Pause for the chart to render

        log("   Step 2: Hunting forward for the true end (with faster iteration)...")
        last_known_close = 0.0
        for i in range(150): # Increased range for longer histories
            chart_area.press('ArrowRight')
            page.wait_for_timeout(20) # A very brief pause for the UI, reduced for speed
            try:
                current_close = get_ohlc_values()['close']
                if i > 1 and current_close == last_known_close:
                    log("   -> Price stopped changing. End of chart found.")
                    chart_area.press('ArrowLeft')
                    page.wait_for_timeout(500)
                    break
                last_known_close = current_close
            except Exception:
                log("   -> OHLC not available on this candle, continuing hunt.")
                continue
        else: 
            raise Exception("Could not find the end of the chart.")
        
        log("   Step 3: Moving left to select yesterday's candle...")
        chart_area.press('ArrowLeft')
        page.wait_for_timeout(500)
        
        log("Retrieving final OHLC values for yesterday...")
        final_ohlc = get_ohlc_values()
        log(f"Final OHLC: {final_ohlc}")

        day_range = final_ohlc['high'] - final_ohlc['low']
        market_snapshot = {"asset_name": asset_name, "symbol": asset_symbol, "status": "Success", "data": {"day_range": f"{day_range:,.3f}", "open": f"{final_ohlc['open']:,.3f}", "close": f"{final_ohlc['close']:,.3f}", "high": f"{final_ohlc['high']:,.3f}", "low": f"{final_ohlc['low']:,.3f}"}}
        context.close()
        log(f"Successfully processed {asset_name}.")
        return market_snapshot
    except Exception as e:
        log(f"!!! ERROR processing {asset_name}: {e}")
        context.close()
        return {"asset_name": asset_name, "symbol": asset_symbol, "status": "Failed", "error": str(e)}
        
# (All other functions are unchanged)
def scrape_article_body(url, context):
    page = None
    try:
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=25000)
        handle_consent_banners(page)
        html = page.content()
        body_text = trafilatura.extract(html, include_comments=False, include_tables=False)
        if not body_text or len(body_text) < 250:
            return "Scraping failed: trafilatura could not extract a valid article body."
        return body_text[:4000]
    except Exception as e:
        return f"Could not scrape article body. Reason: {str(e)}"
    finally:
        if page: page.close()

def resolve_google_redirect(google_url, context):
    page = None
    try:
        page = context.new_page()
        page.goto(google_url, wait_until="domcontentloaded", timeout=15000)
        if "consent.google.com" in page.url:
            page.get_by_role("button", name="Accept all").click(timeout=5000)
            page.wait_for_load_state("networkidle", timeout=15000)
        return page.url
    except Exception as e:
        log(f"      -> FAILED to resolve redirect: {e}")
        return None
    finally:
        if page: page.close()

def fetch_and_scrape_news(browser: Browser, search_queries, google_context, max_to_save=3):
    try:
        google_news = GNews(language='en', country='US', period='1d', max_results=30)
        raw_articles = []
        for i, query in enumerate(search_queries):
            log(f"   News Hunt Tier {i+1}: {query}")
            results = google_news.get_news(query)
            if results:
                raw_articles = results
                break
        unique_articles = {article['url']: article for article in raw_articles}.values()
        successfully_scraped_articles = []
        for candidate in unique_articles:
            if len(successfully_scraped_articles) >= max_to_save: break
            log(f"   Attempting to process article: {candidate['title']}")
            final_url = resolve_google_redirect(candidate['url'], google_context)
            if not final_url: continue
            clean_context = browser.new_context()
            try:
                body = scrape_article_body(final_url, clean_context)
                if "failed" in body or not body:
                    log(f"      -> SCRAPE FAILED. Reason: {body or 'No content found'}.")
                    continue
                successfully_scraped_articles.append({"title": candidate['title'], "source": candidate['publisher']['title'], "url": final_url, "body": body})
            finally:
                clean_context.close()
        return successfully_scraped_articles
    except Exception as e:
        log(f"!!! ERROR fetching news for query '{search_queries[0]}': {e}")
        return []

def generate_market_summary(scraped_articles, asset_name, api_key, model):
    if not api_key: return "ERROR: OpenRouter API key not found."
    valid_articles = [a for a in scraped_articles if "failed" not in a.get('body', '')]
    if not valid_articles: return "**No relevant news articles with clean content were found.**"
    log(f"Aggregating {len(valid_articles)} articles for AI summary...")
    dossier = "".join([f"--- ARTICLE {i+1}: {a['title']} ---\n{a['body']}\n\n" for i, a in enumerate(valid_articles)])
    prompt = f"Analyze the following news articles regarding {asset_name}. Provide a 3-4 sentence holistic market summary. Following the summary, on a new line, provide the overall market sentiment. The sentiment must be one of: Positive, Neutral, Negative, or Mixed.\n\nHere is the required format:\nSUMMARY: [Your summary here]\nSENTIMENT: [Your sentiment here]\n\nArticles Dossier: ###\n{dossier}\n###"
    try:
        response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers={"Authorization": f"Bearer {api_key}"}, json={"model": model, "messages": [{"role": "system", "content": "You are an expert financial analyst. Follow the user's format precisely."}, {"role": "user", "content": prompt}]})
        response.raise_for_status()
        raw_text = response.json()['choices'][0]['message']['content'].strip()
        summary = re.search(r"SUMMARY:\s*(.*)", raw_text, re.DOTALL|re.I).group(1).strip()
        sentiment = re.search(r"SENTIMENT:\s*(.*)", raw_text, re.I).group(1).strip()
        return f"{summary}\n\n**Overall Market Sentiment:** {sentiment}"
    except Exception as e: return f"ERROR: AI summary request failed: {e}"

def generate_markdown_report(all_snapshots, folder):
    date_str = datetime.now().strftime("%Y-%m-%d")
    filename = os.path.join(folder, f"briefing_{date_str}.md")
    log(f"Generating Markdown report at '{filename}'...")
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(f"# Daily Market Briefing - {date_str}\n\n")
        for snap in all_snapshots:
            if snap['status'] != 'Success': continue
            f.write(f"## {snap['asset_name']} ({snap['symbol']})\n\n### Market Snapshot\n*   **Yesterday's Close:** {snap['data']['close']}\n*   **Day's Range:** {snap['data']['day_range']}\n*   **Open:** {snap['data']['open']}\n*   **High:** {snap['data']['high']}\n*   **Low:** {snap['data']['low']}\n\n### AI Market Summary\n{snap.get('market_summary', 'N/A')}\n\n### Source Articles\n")
            if snap.get('source_articles'):
                for i, art in enumerate(snap['source_articles']): f.write(f"{i+1}. [{art['title']}]({art['url']}) - *{art['source']}*\n")
            else: f.write("No source articles found.\n")
            f.write("\n---\n\n")
        f.write(f"*Report generated at {datetime.now().strftime('%H:%M:%S UTC')}*")

def handle_consent_banners(page: Page):
    """
    A generic function to find and click common cookie consent buttons.
    It checks for buttons directly on the page and within any iframes.
    """
    buttons_to_try = [
        "Accept all",
        "Yes, I Accept",
        "I Accept",
        "Agree",
        "YES, I AGREE",
        "I AGREE",
        "ACCEPT"
    ]
    clicked = False
    for button_text in buttons_to_try:
        # Try to find the button on the main page
        try:
            page.get_by_role("button", name=re.compile(button_text, re.IGNORECASE)).click(timeout=2000)
            log(f"Clicked '{button_text}' on the main page.")
            page.wait_for_timeout(1000) # Wait for the banner to disappear
            clicked = True
            return # Exit after the first successful click
        except TimeoutError:
            pass # Button not found on the main page

        # Try to find the button within any iframes
        try:
            for frame in page.frames:
                try:
                    frame.get_by_role("button", name=re.compile(button_text, re.IGNORECASE)).click(timeout=1000)
                    log(f"Clicked '{button_text}' in an iframe.")
                    page.wait_for_timeout(1000) # Wait for the banner to disappear
                    clicked = True
                    return # Exit after the first successful click
                except TimeoutError:
                    continue # Button not found in this frame
        except Exception as e:
            log(f"An error occurred while checking iframes: {e}")
            pass
    if not clicked:
        log("Could not find any common consent buttons to click.")
        
# --- Main Execution Block ---
if __name__ == "__main__":
    main_start_time = time.time()
    log("--- SCRIPT START ---")
    config_path = os.path.join(BASE_DIR, 'config.json')
    dotenv_path = os.path.join(BASE_DIR, '.env')
    load_dotenv(dotenv_path=dotenv_path)
    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
    if not OPENROUTER_API_KEY: log("!!! WARNING: OPENROUTER_API_KEY not found.")
    try:
        with open(config_path, 'r') as f: config = json.load(f)
        assets_to_scrape = config.get('assets', [])
        ai_model = config['news_config']['ai_model']
        log(f"Using AI Model: {ai_model}")
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            google_context_path = os.path.join(BASE_DIR, 'google_state.json')
            google_context = browser.new_context(storage_state=google_context_path if os.path.exists(google_context_path) else None)
            all_snapshots = []
            
            for asset in assets_to_scrape:
                asset_start_time = time.time()
                log(f"--- Starting asset: {asset['name']} ---")
                
                snapshot = fetch_tradingview_yesterday_data(browser, asset['name'], asset['symbol'])
                
                if snapshot and snapshot['status'] == 'Success':
                    news_start_time = time.time()
                    search_queries = asset.get('search_queries', [])
                    related_news = fetch_and_scrape_news(browser, search_queries, google_context)
                    log(f"News fetching took {time.time() - news_start_time:.2f} seconds.")
                    
                    ai_start_time = time.time()
                    market_summary = generate_market_summary(related_news, asset['name'], OPENROUTER_API_KEY, ai_model)
                    log(f"AI summarization took {time.time() - ai_start_time:.2f} seconds.")

                    snapshot['market_summary'] = market_summary
                    snapshot['source_articles'] = related_news
                
                if snapshot: all_snapshots.append(snapshot)
                log(f"--- Finished asset: {asset['name']}. Total time: {time.time() - asset_start_time:.2f} seconds. ---")

            google_context.storage_state(path=google_context_path)
            google_context.close()
            browser.close()

        snapshot_folder = os.path.join(BASE_DIR, "snapshots")
        os.makedirs(snapshot_folder, exist_ok=True)
        date_str = datetime.now().strftime("%m_%d_%Y")
        json_filename = os.path.join(snapshot_folder, f"snapshot_{date_str}.json")
        
        log(f"Saving raw JSON data to '{json_filename}'...")
        with open(json_filename, 'w') as f: json.dump(all_snapshots, f, indent=4)
        generate_markdown_report(all_snapshots, snapshot_folder)
        
        log(f"--- SCRIPT END. Total execution time: {time.time() - main_start_time:.2f} seconds. ---")
    except FileNotFoundError:
        log(f"FATAL ERROR: config.json not found at '{config_path}'.")
    except KeyError as e:
        log(f"FATAL ERROR: A required key is missing from your config.json file: {e}.")
    except Exception as e:
        log(f"A critical error occurred in the main block: {e}")