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
    "*.cookielaw.org", "*.onetrust.com", "*.trustarc.com", "*.cookiebot.com",
    "*.consensu.org", "google-analytics.com", "googletagmanager.com",
    "doubleclick.net", "adservice.google.com"
]

def log(message):
    """Prints a message with a timestamp."""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

def resolve_google_redirect(url: str, context: BrowserContext) -> str:
    """A robust function to navigate through Google's redirect service."""
    final_url = url
    page = None
    try:
        page = context.new_page()
        page.goto(url, wait_until='domcontentloaded', timeout=20000)
        page.wait_for_function("!window.location.href.startsWith('https://news.google.com')", timeout=15000)
        final_url = page.url
        log(f"      -> Redirect resolved: {final_url}")
    except Exception as e:
        log(f"      -> FAILED to resolve redirect for {url}. Reason: {e}")
        return None
    finally:
        if page: page.close()
    return final_url

def fetch_tradingview_yesterday_data(context: BrowserContext, asset_name: str, asset_symbol: str):
    """A robust function to fetch OHLC data from TradingView using a shared browser context."""
    url = f"https://www.tradingview.com/chart/?symbol={asset_symbol.replace(':', '%3A')}"
    page = None
    try:
        page = context.new_page()
        page.set_viewport_size({"width": 1920, "height": 1080})
        
        log(f"Processing Price Data for {asset_name}...")
        page.goto(url, wait_until="networkidle", timeout=90000)
        time.sleep(3)
        
        try:
            page.get_by_role("button", name="Accept all").click(timeout=7000)
            log("Cookie banner accepted.")
        except TimeoutError:
            log("No cookie banner found or it timed out.")

        chart_area = page.locator("div.chart-gui-wrapper")
        chart_box = chart_area.bounding_box()
        if chart_box:
            chart_area.click(position={'x': chart_box['width'] * 0.9, 'y': chart_box['height'] * 0.5})
        
        try:
            page.get_by_role("button", name="Got it!").click(timeout=7000)
            log("'Got it!' popup closed.")
        except TimeoutError:
            log("No 'Got it!' popup found or it timed out.")

        def get_ohlc_values():
            try:
                o_text = page.locator('div[data-name="legend-series-item-open"] > div > div:nth-child(2)').inner_text(timeout=500)
                h_text = page.locator('div[data-name="legend-series-item-high"] > div > div:nth-child(2)').inner_text(timeout=500)
                l_text = page.locator('div[data-name="legend-series-item-low"] > div > div:nth-child(2)').inner_text(timeout=500)
                c_text = page.locator('div[data-name="legend-series-item-close"] > div > div:nth-child(2)').inner_text(timeout=500)
                ohlc = {"open": float(o_text.replace(",", "")), "high": float(h_text.replace(",", "")), "low": float(l_text.replace(",", "")), "close": float(c_text.replace(",", ""))}
                log(f"   -> OHLC values detected: {ohlc}")
                return ohlc
            except Exception as e:
                log(f"   -> Could not detect OHLC values on this candle. Error: {e}")
                return None

        log("Finding the most recent historical candle...")
        chart_area.press('Home'); page.wait_for_timeout(500)

        last_known_close = 0.0
        for i in range(250):
            chart_area.press('ArrowRight'); page.wait_for_timeout(50)
            current_ohlc = get_ohlc_values()
            if current_ohlc:
                current_close = current_ohlc['close']
                if i > 10 and current_close == last_known_close:
                    log("   -> Price stopped changing. End of chart found."); chart_area.press('ArrowLeft'); page.wait_for_timeout(500); break
                last_known_close = current_close
        else:
            log("   -> WARNING: Loop finished without finding a stable end-of-chart.")

        chart_area.press('ArrowLeft'); page.wait_for_timeout(500)
        
        log("Retrieving final OHLC values for yesterday...")
        final_ohlc = get_ohlc_values()
        if not final_ohlc: raise Exception("Failed to retrieve final OHLC values for yesterday after hunting.")

        log(f"Final OHLC: {final_ohlc}")
        day_range = final_ohlc['high'] - final_ohlc['low']
        return {"asset_name": asset_name, "symbol": asset_symbol, "status": "Success", "data": {"day_range": f"{day_range:,.3f}", "open": f"{final_ohlc['open']:,.3f}", "close": f"{final_ohlc['close']:,.3f}", "high": f"{final_ohlc['high']:,.3f}", "low": f"{final_ohlc['low']:,.3f}"}}
    except Exception as e:
        log(f"!!! ERROR processing {asset_name}: {e}")
        return {"asset_name": asset_name, "symbol": asset_symbol, "status": "Failed", "error": str(e)}
    finally:
        if page: page.close()

def fetch_and_scrape_news(context: BrowserContext, search_queries, max_to_save=3):
    """An efficient news scraper that reuses the main browser context."""
    try:
        google_news = GNews(language='en', country='US', period='1d', max_results=30)
        raw_articles = []
        for i, query in enumerate(search_queries):
            log(f"   News Hunt Tier {i+1}: {query}")
            results = google_news.get_news(query)
            if results: raw_articles = results; break

        unique_articles = {article['url']: article for article in raw_articles}.values()
        successfully_scraped_articles = []

        for article_info in unique_articles:
            if len(successfully_scraped_articles) >= max_to_save: break
            log(f"   Attempting to process article: {article_info['title']}")
            
            final_url = resolve_google_redirect(article_info['url'], context)
            if not final_url: continue

            page = None
            try:
                page = context.new_page()
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
                if page: page.close()
        
        return successfully_scraped_articles
    except Exception as e:
        log(f"!!! ERROR fetching news for query '{search_queries[0]}': {e}"); return []

def generate_market_summary(scraped_articles, asset_name, api_key, model):
    if not api_key: return "ERROR: OpenRouter API key not found."
    if not scraped_articles: return "**No relevant news articles with clean content were found.**"
    
    log(f"Aggregating {len(scraped_articles)} articles for AI summary...")
    dossier = "".join([f"--- ARTICLE {i+1}: {a['title']} ---\n{a['body']}\n\n" for i, a in enumerate(scraped_articles)])
    prompt = f"Analyze the following news articles regarding {asset_name}. Provide a 3-4 sentence holistic market summary. Following the summary, on a new line, provide the overall market sentiment. The sentiment must be one of: Positive, Neutral, Negative, or Mixed.\n\nHere is the required format:\nSUMMARY: [Your summary here]\nSENTIMENT: [Your sentiment here]\n\nArticles Dossier: ###\n{dossier}\n###"
    try:
        response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers={"Authorization": f"Bearer {api_key}"}, json={"model": model, "messages": [{"role": "user", "content": prompt}]})
        response.raise_for_status()
        raw_text = response.json()['choices'][0]['message']['content'].strip()
        summary = re.search(r"SUMMARY:\s*(.*)", raw_text, re.DOTALL|re.I).group(1).strip()
        sentiment = re.search(r"SENTIMENT:\s*(.*)", raw_text, re.I).group(1).strip()
        return f"{summary}\n\n**Overall Market Sentiment:** {sentiment}"
    except Exception as e: return f"ERROR: AI summary request failed: {e}"

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
            f.write(f"## {snap['asset_name']} ({snap['symbol']})\n\n### Market Snapshot\n*   **Yesterday's Close:** {snap['data']['close']}\n*   **Day's Range:** {snap['data']['day_range']}\n*   **Open:** {snap['data']['open']}\n*   **High:** {snap['data']['high']}\n*   **Low:** {snap['data']['low']}\n\n### AI Market Summary\n{snap.get('market_summary', 'N/A')}\n\n### Source Articles\n")
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
            
            user_agent_string = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36"
            main_context = browser.new_context(user_agent=user_agent_string)
            
            for asset in assets_to_scrape:
                asset_start_time = time.time()
                log(f"--- Starting asset: {asset['name']} ---")
                
                snapshot = fetch_tradingview_yesterday_data(main_context, asset['name'], asset['symbol'])
                
                if snapshot and snapshot['status'] == 'Success':
                    news_start_time = time.time()
                    search_queries = asset.get('search_queries', [])
                    related_news = fetch_and_scrape_news(main_context, search_queries)
                    log(f"News fetching took {time.time() - news_start_time:.2f} seconds.")
                    
                    ai_start_time = time.time()
                    market_summary = generate_market_summary(related_news, asset['name'], OPENROUTER_API_KEY, ai_model)
                    log(f"AI summarization took {time.time() - ai_start_time:.2f} seconds.")

                    snapshot['market_summary'] = market_summary
                    snapshot['source_articles'] = related_news
                
                if snapshot: all_snapshots.append(snapshot)
                log(f"--- Finished asset: {asset['name']}. Total time: {time.time() - asset_start_time:.2f} seconds. ---")

            main_context.close()
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