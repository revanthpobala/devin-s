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
    Live web search. PRIMARY = Brave Search API (when BRAVE_SEARCH_API_KEY is
    set); FALLBACK = keyless DuckDuckGo (ddgs). Used by deep-research fresh
    news, the LLM search_web tool, and the deep-research context battery —
    so qualified candidates + deep research both get the richer Brave index when
    available, with DDGS as a free safety net.
    backend options: "auto" (Brave -> DDGS), "brave" (Brave only), "ddgs" (DDGS only)
    """
    if backend in ("auto", "brave"):
        try:
            brave = _search_brave(query, max_results)
            if brave:
                return _filter_search_results(brave)[:max_results]
        except Exception as e:
            logger.warning(f"Brave search errored for '{query}': {e}")
        if backend == "brave":
            return []

    try:
        results = []
        with DDGS() as ddgs:
            # Request extra results in case some are filtered out
            for r in ddgs.text(query, max_results=max_results * 2):
                results.append(
                    {
                        "title": r.get("title", ""),
                        "href": r.get("href", ""),
                        "body": r.get("body", ""),
                    }
                )
        return _filter_search_results(results)[:max_results]
    except Exception as e:
        logger.error(f"DDGS fallback search failed for query '{query}': {e}")
        return []


def get_deep_research_context(ticker: str) -> str:
    """
    Executes a battery of searches tailored to the Deep Research gem and compiles a context string.
    """
    queries = [
        f"site:finviz.com {ticker}",
        f"{ticker} earnings catalyst history",
        f"{ticker} analyst ratings upgrades downgrades",
        f"{ticker} options max pain implied volatility",
    ]

    context_blocks = []

    def run_query(q):
        results = search_web(q, max_results=2)
        if not results:
            return ""
        block = f"Search Query: {q}\n"
        for r in results:
            block += f"- [{r['title']}] {r['body']}\n"
        return block

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(run_query, q): q for q in queries}
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
