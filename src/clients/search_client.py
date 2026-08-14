import json
import logging
import os
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from ddgs import DDGS
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


def _search_parallel(query: str, max_results: int = 3) -> list:
    """Parallel.ai Search API via REST. Provides LLM-optimized, citation-backed excerpts."""
    key = os.getenv("PARALLEL_API_KEY")
    if not key:
        return []
    try:
        url = "https://api.parallel.ai/v1/search"
        payload = json.dumps({"objective": query, "search_queries": [query]}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "User-Agent": "stock-research/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        out = []
        for item in data.get("results", []):
            excerpts = item.get("excerpts", [])
            body = "\n".join(excerpts) if isinstance(excerpts, list) else str(excerpts)
            out.append(
                {
                    "title": item.get("title", ""),
                    "href": item.get("url", ""),
                    "body": body,
                }
            )
        return out
    except Exception as e:
        logger.debug(f"Parallel search failed for '{query}': {e}")
        return []


def _search_ddgs(query: str, max_results: int = 3) -> list:
    """DuckDuckGo Search (ddgs). Keyless multi-domain live web search."""
    try:
        out = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results * 2):
                out.append(
                    {
                        "title": r.get("title", ""),
                        "href": r.get("href", ""),
                        "body": r.get("body", ""),
                    }
                )
        return out
    except Exception as e:
        logger.debug(f"DDGS search failed for '{query}': {e}")
        return []


def _search_brave(query: str, max_results: int = 3) -> list:
    """Brave Search API (api.search.brave.com). Fast privacy-focused web index."""
    key = os.getenv("BRAVE_SEARCH_API_KEY")
    if not key:
        return []
    params = urllib.parse.urlencode(
        {
            "q": query,
            "count": min(max_results, 20),
            "freshness": "pw",  # past week — keeps catalyst/macro context current
            "text_decorations": "false",
        }
    )
    url = f"{BRAVE_ENDPOINT}?{params}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "X-Subscription-Token": key,
            "User-Agent": "stock-research/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        out = []
        for item in data.get("web", {}).get("results", []):
            out.append(
                {
                    "title": item.get("title", ""),
                    "href": item.get("url", ""),
                    "body": item.get("description", ""),
                }
            )
        return out
    except Exception as e:
        logger.debug(f"Brave search failed for '{query}': {e}")
        return []


# Suppress noisy HTTP logs from fallback scraping in ddgs/urllib3/primp
for _log in ("ddgs", "duckduckgo_search", "primp", "curl_cffi", "urllib3"):
    logging.getLogger(_log).setLevel(logging.WARNING)

EXCLUDED_DOMAINS = (
    "wikipedia.org",
    "grokipedia.com",
    "wikihow.com",
    "wiktionary.org",
    "wikimedia.org",
    "fandom.com",
    "answers.com",
    "quora.com",
    "mojeek.com",
    "pinterest.com",
    "reddit.com",
    "medium.com",
    "quizlet.com",
)


def is_junk_url(url: str) -> bool:
    """Reusable single-URL check so other clients (grounding, etc.) share this list."""
    u = (url or "").lower()
    return any(domain in u for domain in EXCLUDED_DOMAINS)


def _filter_search_results(results: list) -> list:
    """Filter out useless encyclopedia, wiki, and crowd-sourced domains."""
    filtered = []
    for r in results:
        href = r.get("href", "").lower()
        title = r.get("title", "").lower()
        if is_junk_url(href):
            continue
        if any(domain.split(".")[0] in title for domain in EXCLUDED_DOMAINS):
            continue
        filtered.append(r)
    return filtered


def search_web(query: str, max_results: int = 3, backend: str = "auto") -> list:
    """
    Live concurrent multi-source web search.
    Executes Brave, DDGS, and Parallel.ai simultaneously in parallel threads,
    filters junk domains, deduplicates URLs, and returns ranked results.
    """
    aggregated_results = []
    seen_urls = set()

    def add_results(raw_results):
        filtered = _filter_search_results(raw_results)
        for r in filtered:
            href = r.get("href", "").lower()
            if href and href not in seen_urls:
                seen_urls.add(href)
                aggregated_results.append(r)

    # Concurrently query available search backends in parallel
    with ThreadPoolExecutor(max_workers=3) as executor:
        future_to_engine = {}
        if backend in ("auto", "brave", "all"):
            future_to_engine[executor.submit(_search_brave, query, max_results)] = "Brave"
        if backend in ("auto", "ddgs", "all"):
            future_to_engine[executor.submit(_search_ddgs, query, max_results)] = "DDGS"
        if backend in ("auto", "parallel", "all") and os.getenv("PARALLEL_API_KEY"):
            future_to_engine[executor.submit(_search_parallel, query, max_results)] = "Parallel"

        for future in as_completed(future_to_engine):
            engine = future_to_engine[future]
            try:
                res = future.result()
                if res:
                    add_results(res)
            except Exception as e:
                logger.debug(f"Search engine {engine} error on '{query}': {e}")

    if backend == "all":
        return aggregated_results[: max_results * 3]
    return aggregated_results[:max_results]


def get_deep_research_context(ticker: str) -> str:
    """
    Executes a battery of searches tailored to the Deep Research gem and compiles a context string.
    Aggregates Parallel, Brave, DDGS, Finnhub, and Alpaca news.
    """
    queries = [
        f"site:finviz.com {ticker}",
        f"{ticker} earnings catalyst history",
        f"{ticker} analyst ratings upgrades downgrades",
        f"{ticker} options max pain implied volatility",
    ]

    context_blocks = []

    def run_query(q):
        results = search_web(q, max_results=2, backend="all")
        if not results:
            return ""
        block = f"Search Query: {q}\n"
        for r in results:
            block += f"- [{r['title']}] {r['body']}\n"
        return block

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(run_query, q): q for q in queries}
        
        # Also spin up Finnhub and Alpaca in the same executor pool
        try:
            from src.clients.news_client import _fetch_finnhub_news, get_ticker_news
            finnhub_future = executor.submit(_fetch_finnhub_news, ticker, 3)
            alpaca_future = executor.submit(get_ticker_news, ticker, 3)
            
            for future in as_completed(futures):
                res = future.result()
                if res:
                    context_blocks.append(res)
                    
            finnhub_res = finnhub_future.result()
            if finnhub_res:
                context_blocks.append(f"Finnhub News Aggregation for {ticker}:\n{finnhub_res}\n")
                
            alpaca_res = alpaca_future.result()
            if alpaca_res and isinstance(alpaca_res, dict) and "raw_news" in alpaca_res:
                context_blocks.append(f"Alpaca News Aggregation for {ticker}:\n{alpaca_res['raw_news']}\n")
                
        except Exception as e:
            logger.warning(f"Failed to fetch aggregated news for {ticker}: {e}")
            for future in as_completed(futures):
                res = future.result()
                if res:
                    context_blocks.append(res)

    if not context_blocks:
        return "No real-time search context could be retrieved."

    return "\n".join(context_blocks)


if __name__ == "__main__":
    # Test
    logging.basicConfig(level=logging.INFO)
    print(get_deep_research_context("AMZN"))
