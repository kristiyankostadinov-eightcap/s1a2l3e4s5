This project is a financial data scraper.

It fetches OHLC (Open, High, Low, Close) data from TradingView and related news articles for a list of financial assets defined in `config.json`.

To use it:
1. Install the required Python libraries: `pip install -r requirements.txt`
2. Run the main script: `python main.py`

The script will create a JSON file in the `snapshots` directory containing the scraped data.

The `config.json` file defines the assets to be scraped and the news sources to be used.