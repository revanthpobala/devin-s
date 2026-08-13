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
    """Parallel.ai Search API. Provides LLM-optimized, citation-backed excerpts."""
    key = os.getenv("PARALLEL_API_KEY")
    if not key:
        return []
    try:
        from parallel import Parallel
        client = Parallel(api_key=key)
        
        # Parallel Search API requires search_queries list
        response = client.search(
            objective=query,
            search_queries=[query]
        )
        
        out = []
        for item in getattr(response, "results", []):
            # Parallel returns `excerpts` as a list of strings
            body = "\n".join(item.excerpts) if getattr(item, "excerpts", None) else ""
            out.append(
                {
                    "title": getattr(item, "title", "") or "",
                    "href": getattr(item, "url", "") or "",
                    "body": body,
                }
            )
        return out
    except Exception as e:
        logger.warning(f"Parallel search failed for '{query}': {e}")
        return []


def _search_brave(query: str, max_results: int = 3) -> list:
    """Brave Search API (api.search.brave.com). Used as the PRIMARY live
    search backend when BRAVE_SEARCH_API_KEY is configured; falls back to DDGS
    on any failure. Returns the same {title,href,body} shape as DDGS."""
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
        logger.warning(f"Brave search failed for '{query}': {e}")
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
    Live web search. PRIMARY = Parallel.ai (when PARALLEL_API_KEY is set);
    SECONDARY = Brave Search API; FALLBACK = keyless DuckDuckGo (ddgs).
    backend options: "auto" (Parallel -> Brave -> DDGS), "parallel", "brave", "ddgs", "all"
    """
    aggregated_results = []
    seen_urls = set()

    def add_results(raw_results):
        filtered = _filter_search_results(raw_results)
        for r in filtered:
            if r["href"] not in seen_urls:
                seen_urls.add(r["href"])
                aggregated_results.append(r)

    # 1. Parallel
    if backend in ("auto", "parallel", "all"):
        try:
            parallel_res = _search_parallel(query, max_results)
            if parallel_res:
                add_results(parallel_res)
                if backend == "auto":
                    return aggregated_results[:max_results]
        except Exception as e:
            logger.warning(f"Parallel search errored for '{query}': {e}")
        if backend == "parallel":
            return aggregated_results[:max_results]

    # 2. Brave
    if backend in ("auto", "brave", "all"):
        try:
            brave = _search_brave(query, max_results)
            if brave:
                add_results(brave)
                if backend == "auto":
                    return aggregated_results[:max_results]
        except Exception as e:
            logger.warning(f"Brave search errored for '{query}': {e}")
        if backend == "brave":
            return aggregated_results[:max_results]

    # 3. DuckDuckGo
    if backend in ("auto", "ddgs", "all"):
        try:
            ddgs_results = []
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=max_results * 2):
                    ddgs_results.append(
                        {
                            "title": r.get("title", ""),
                            "href": r.get("href", ""),
                            "body": r.get("body", ""),
                        }
                    )
            if ddgs_results:
                add_results(ddgs_results)
        except Exception as e:
            logger.error(f"DDGS fallback search failed for query '{query}': {e}")

    # Return up to max_results (or more if backend="all" we might want to return max_results PER engine, 
    # but the user requested all options, so let's return max_results * 3 for "all")
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
