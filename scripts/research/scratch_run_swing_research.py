import json
import logging
import os
from datetime import datetime

from src import config
from src.clients.news_client import get_ticker_news
from src.data.tv_scraper import TVScraper
from src.logic.deterministic_cascade import DeterministicCascade

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] (SwingResearch) %(message)s"
)


def run_swing_pipeline():
    logger.info("=" * 60)
    logger.info("STARTING AUTOMATED SWING RESEARCH PIPELINE")
    logger.info("=" * 60)

    # 1. Run Deterministic Cascade to filter down to survivors
    logger.info("\n--- PHASE 1: DETERMINISTIC CASCADE ---")
    cascade = DeterministicCascade()
    survivors = cascade.run()

    if not survivors:
        logger.info("No survivors found today. Pipeline finished.")
        return

    logger.info(f"Pipeline advancing with {len(survivors)} survivor(s).")

    # Initialize TV Scraper
    scraper = TVScraper()

    # 2. Process each survivor
    today_str = datetime.now().strftime("%Y-%m-%d")
    out_dir = config.BASE_DIR / "data" / "raw" / today_str

    for survivor in survivors:
        # The key might be "Ticker" or "Symbol" based on sheet headers
        ticker = survivor.get("Ticker") or survivor.get("Symbol") or survivor.get("ticker", "")
        if not ticker:
            logger.warning(f"Survivor dict has no Ticker key: {survivor}")
            continue

        ticker = ticker.strip().upper()
        # For TradingView, it often needs the exchange prefix, but we will pass what we have
        # (assuming the sheet has something like 'NASDAQ:NVDA' or just 'NVDA')
        # If it's just 'NVDA', TV usually handles it, but let's assume it has the exchange prefix.

        safe_ticker = ticker.replace(":", "_")
        thesis_path = out_dir / f"{safe_ticker}_thesis.md"

        if thesis_path.exists():
            logger.info(f"Skipping {ticker} - thesis already exists (resume mode)")
            continue

        logger.info(f"\n--- PHASE 2: PROCESSING {ticker} ---")

        # A. Fetch News & 9B Sentiment Distillation
        logger.info(f"Fetching 14-day Alpaca News for {ticker}...")
        news_data = get_ticker_news(ticker.split(":")[-1] if ":" in ticker else ticker, days=14)

        # B. Scrape Data Window JSON and Chart Screenshot
        logger.info(f"Scraping TradingView for {ticker}...")
        try:
            scraper.capture_ticker(ticker)
        except Exception as e:
            logger.error(f"Scraper failed for {ticker}: {e}")

        # C. Load the scraped Data Window JSON
        safe_ticker = ticker.replace(":", "_")
        json_path = out_dir / f"{safe_ticker}_datawindow.json"
        data_window = {}
        if json_path.exists():
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    data_window = json.load(f)
            except Exception as e:
                logger.error(f"Failed to read JSON for {ticker}: {e}")

        # D. Compile Final Thesis Markdown
        thesis_path = out_dir / f"{safe_ticker}_thesis.md"

        # Determine Trade Direction
        side = str(
            survivor.get("_raw", {}).get("side", survivor.get("Direction", "UNKNOWN"))
        ).upper()

        # F. Build Local LLM Input JSON
        def safe_float(val, default=0.0):
            try:
                s = str(val)
                for p in ["C", "O", "H", "L"]:
                    s = s.replace(p, "")
                s = s.replace(",", "").replace("%", "").strip()
                if not s:
                    return default
                return float(s)
            except:
                return default

        # E. Calculate RR
        rr_from_current = 0.0
        try:
            current_price = safe_float(data_window.get("C"))

            if side == "LONG":
                target = safe_float(data_window.get("Long Target"))
                stop = safe_float(data_window.get("Long Stop Loss"))
                if current_price - stop > 0:
                    rr_from_current = (target - current_price) / (current_price - stop)
            elif side == "SHORT":
                target = safe_float(data_window.get("Short Target"))
                stop = safe_float(data_window.get("Short Stop Loss"))
                if stop - current_price > 0:
                    rr_from_current = (current_price - target) / (stop - current_price)
        except Exception as e:
            logger.warning(f"Failed to calculate RR for {ticker}: {e}")

        zone_bot = safe_float(data_window.get("Long Entry Zone Bot"))
        zone_top = safe_float(data_window.get("Long Entry Zone Top"))
        zone_state = "in_zone"
        if current_price > zone_top:
            zone_state = "above_zone"
        elif current_price < zone_bot:
            zone_state = "below_zone"

        buy_score = safe_float(data_window.get("Buy Score"))
        sell_score = safe_float(data_window.get("Sell Score"))

        raw_news = news_data.pop("raw_news", "No raw news found.")
        headlines = []
        if raw_news:
            for block in raw_news.split("\n\n"):
                if block.strip():
                    headlines.append(block.split("\n")[0])  # Just the headline

        llm_input = {
            "ticker": ticker,
            "price": current_price,
            "buy": buy_score,
            "sell": sell_score,
            "dir_prob": safe_float(data_window.get("Dir Prob % (>50 bull)")),
            "stage": int(safe_float(data_window.get("Stage (1=Base,2=Up,3=Top,4=Dn)"))),
            "regime": int(safe_float(data_window.get("Regime (0Hlt1Ext2Clmx3Dist4Dn5Ign6Sqz)"))),
            "ext_pct": safe_float(data_window.get("Ext% (vs MA200)")),
            "exhaustion": safe_float(data_window.get("Exhaustion Gradient 0-1")),
            "exp_move_pct": safe_float(data_window.get("Exp Move % (21b)")),
            "ignition_long": 1
            if "Ign" in str(data_window.get("Regime (0Hlt1Ext2Clmx3Dist4Dn5Ign6Sqz)"))
            else 0,  # Approximation, usually requires specific column
            "rev_zone_l": safe_float(data_window.get("Long Rev Zone")),
            "rev_zone_s": safe_float(data_window.get("Short Rev Zone")),
            "long_zone": [zone_bot, zone_top],
            "long_target": safe_float(data_window.get("Long Target")),
            "ma200": safe_float(data_window.get("MA 200")),
            "avwap_res": safe_float(data_window.get("AVWAP Resistance")),
            "avwap_sup": safe_float(data_window.get("AVWAP Support")),
            "golden_cross": safe_float(data_window.get("Golden Cross")),
            "death_cross": safe_float(data_window.get("Death Cross")),
            "dominant_side": side.lower(),
            "opposite_score": sell_score if side == "LONG" else buy_score,
            "zone_state": zone_state,
            "rr_from_current": rr_from_current,
            "earnings_days": -1,  # To be fetched by AlphaVantage if send_for_deep_research=true
            "earnings_gate": "CAUTION",
            "headlines": headlines[:5],  # Top 5 recent headlines
        }

        # G. Query Local LLM
        logger.info(f"Querying Local LLM for {ticker} triage...")
        from src.clients.llm_client import query_local_llm

        prompt_path = config.BASE_DIR / "gems" / "revanth-gem-local.md"
        system_prompt = "You are a financial analyst."
        if prompt_path.exists():
            with open(prompt_path, "r", encoding="utf-8") as f:
                system_prompt = f.read()

        llm_response = query_local_llm(
            system_prompt=system_prompt,
            user_prompt=json.dumps(llm_input, indent=2),
            json_mode=True,
            max_tokens=getattr(config, "LLM_MAX_TOKENS", 4096),
            disable_thinking=True,  # thinking burns the output budget and truncates JSON to empty content
        )

        llm_json = {}
        if llm_response:
            # Try to parse the response, stripping <think> tags if any
            try:
                if "</think>" in llm_response:
                    json_str = llm_response.split("</think>")[-1].strip()
                else:
                    json_str = llm_response.strip()
                # Sometimes models wrap json in ```json ... ```
                if json_str.startswith("```json"):
                    json_str = json_str.split("```json")[1].split("```")[0].strip()
                elif json_str.startswith("```"):
                    json_str = json_str.split("```")[1].split("```")[0].strip()

                llm_json = json.loads(json_str)
                logger.info(
                    f"LLM Triage Verdict: {llm_json.get('triage')} (Conviction {llm_json.get('conviction')}/10)"
                )
            except Exception as e:
                logger.error(f"Failed to parse LLM JSON output: {e}\nRaw Output: {llm_response}")

        # H. Alpha Vantage Fetch (If Send to Gemini)
        av_sentiment = {}
        av_earnings = {}
        if llm_json.get("send_for_deep_research") is True:
            logger.info(
                f"send_for_deep_research is TRUE. Fetching Alpha Vantage data for {ticker}..."
            )
            av_key = os.getenv("ALPHAVANTAGE_API_KEY")
            if av_key:
                try:
                    import urllib.request

                    # Sentiment
                    s_url = f"https://www.alphavantage.co/query?function=NEWS_SENTIMENT&tickers={ticker}&apikey={av_key}"
                    s_res = json.loads(urllib.request.urlopen(s_url).read())
                    rate_limited = False
                    if "Information" in s_res and "limit" in s_res["Information"].lower():
                        logger.warning(
                            f"Alpha Vantage rate limit hit! Queueing {ticker} for later."
                        )
                        rate_limited = True

                    if not rate_limited and s_res.get("feed"):
                        av_sentiment = s_res["feed"][0]
                        logger.info("Successfully fetched AV Sentiment")

                    # Earnings
                    if not rate_limited:
                        e_url = f"https://www.alphavantage.co/query?function=EARNINGS&symbol={ticker}&apikey={av_key}"
                        e_res = json.loads(urllib.request.urlopen(e_url).read())
                        if "Information" in e_res and "limit" in e_res["Information"].lower():
                            logger.warning(
                                f"Alpha Vantage rate limit hit on Earnings! Queueing {ticker} for later."
                            )
                            rate_limited = True
                        else:
                            if e_res.get("upcomingEarnings"):
                                av_earnings = e_res["upcomingEarnings"][0]
                            elif e_res.get("quarterlyEarnings"):
                                av_earnings = e_res["quarterlyEarnings"][0]
                            if av_earnings:
                                logger.info("Successfully fetched AV Earnings")

                    if rate_limited:
                        # Append to queue
                        queue_path = config.BASE_DIR / "data" / "av_queue.json"
                        queue_data = []
                        if queue_path.exists():
                            with open(queue_path, "r") as qf:
                                queue_data = json.load(qf)
                        queue_data.append(
                            {
                                "ticker": ticker,
                                "row_index": survivor.get("_row_index"),
                                "date": today_str,
                            }
                        )
                        with open(queue_path, "w") as qf:
                            json.dump(queue_data, qf, indent=2)

                except Exception as e:
                    logger.error(f"Failed to fetch Alpha Vantage data: {e}")
            else:
                logger.warning("ALPHAVANTAGE_API_KEY not found in .env")

        md_content = f"# SWING RESEARCH THESIS: {ticker}\n"
        md_content += f"**Date:** {today_str}\n"
        md_content += f"**Direction:** {side}\n\n"

        md_content += "## 1. Local LLM Triage Verdict\n"
        if not llm_json and llm_response:
            md_content += "### âš ï¸ JSON PARSING FAILED. RAW OUTPUT BELOW:\n"
            md_content += "```text\n"
            md_content += llm_response + "\n"
            md_content += "```\n\n"
        else:
            md_content += "```json\n"
            md_content += json.dumps(llm_json, indent=2) + "\n"
            md_content += "```\n\n"

        md_content += "## 2. LLM Input State\n"
        md_content += "```json\n"
        md_content += json.dumps(llm_input, indent=2) + "\n"
        md_content += "```\n\n"

        md_content += "## 3. Original Alert Data (Sheet Row)\n"
        # Filter out private internal keys
        clean_row = {k: v for k, v in survivor.items() if not k.startswith("_")}
        for k, v in clean_row.items():
            md_content += f"- **{k}:** {v}\n"

        if av_sentiment or av_earnings:
            md_content += "\n## 4. Alpha Vantage Data\n"
            if av_sentiment:
                md_content += "### News Sentiment\n"
                md_content += "```json\n"
                md_content += json.dumps(av_sentiment, indent=2) + "\n"
                md_content += "```\n"
            if av_earnings:
                md_content += "### Earnings Catalyst\n"
                md_content += "```json\n"
                md_content += json.dumps(av_earnings, indent=2) + "\n"
                md_content += "```\n"

        md_content += "\n## 5. Visual Context\n"
        md_content += f"See attached screenshot: `{safe_ticker}_chart.png`\n"

        with open(thesis_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        logger.info(f"âœ… Saved Final Thesis for {ticker} to {thesis_path}")

        # I. Update Google Sheets
        row_index = survivor.get("_row_index")
        if row_index:
            try:
                # We need access to the sheets_tracker instance
                # The script creates one in DeterministicCascade but here we can instantiate or use a singleton
                from src.tracking.sheets_tracker import SheetsTracker

                tracker = SheetsTracker()
                tracker.update_swing_research(
                    date_str=today_str,
                    row_index=row_index,
                    llm_data=llm_json,
                    av_sentiment=av_sentiment,
                    av_earnings=av_earnings,
                )
            except Exception as e:
                logger.error(f"Failed to push updates back to Sheets for {ticker}: {e}")
        else:
            logger.warning(f"No _row_index found for {ticker}, skipping Sheets update.")


if __name__ == "__main__":
    run_swing_pipeline()
