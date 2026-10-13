"""
Frontend entry point view router.
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from src.ui.state import WEB_DIR

router = APIRouter(tags=["views"])


def _serve_page(filename: str):
    file_path = WEB_DIR / filename
    if file_path.exists():
        return FileResponse(str(file_path))
    index_file = WEB_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    raise HTTPException(status_code=404, detail=f"{filename} not found")


@router.get("/", response_class=FileResponse)
@router.get("/today", response_class=FileResponse)
def today_page():
    """Serve dedicated Today desk page."""
    return _serve_page("today.html")


@router.get("/radar", response_class=FileResponse)
@router.get("/swing", response_class=FileResponse)
def radar_page():
    """Serve dedicated Radar & Swing research page."""
    return _serve_page("radar.html")


@router.get("/alerts", response_class=FileResponse)
@router.get("/screener", response_class=FileResponse)
def alerts_page():
    """Serve dedicated Alerts & Screener page."""
    return _serve_page("alerts.html")


@router.get("/trades", response_class=FileResponse)
def trades_page():
    """Serve dedicated Suggested Trades page."""
    return _serve_page("trades.html")


@router.get("/portfolio", response_class=FileResponse)
def portfolio_page():
    """Serve dedicated Portfolio & Positions page."""
    return _serve_page("portfolio.html")


@router.get("/intraday", response_class=FileResponse)
def intraday_page():
    """Serve dedicated 0DTE Intraday desk page."""
    return _serve_page("intraday.html")


@router.get("/watchlist", response_class=FileResponse)
def watchlist_page():
    """Serve dedicated Target Watchlist page."""
    return _serve_page("watchlist.html")


@router.get("/journal", response_class=FileResponse)
@router.get("/record", response_class=FileResponse)
def journal_page():
    """Serve dedicated Ledger & Record page."""
    return _serve_page("journal.html")


@router.get("/logs", response_class=FileResponse)
@router.get("/ops", response_class=FileResponse)
def logs_page():
    """Serve dedicated Operations & Logs page."""
    return _serve_page("logs.html")


@router.get("/cockpit", response_class=FileResponse)
def unified_cockpit_page():
    """Serve unified multi-desk cockpit page."""
    return _serve_page("index.html")



@router.get("/research/{ticker}", response_class=FileResponse)
def research_ticker_page(ticker: str, date: str = None, view: str = "standalone"):
    """
    Serve research dossier view for a ticker.
    - If view == 'cockpit': serves the cockpit UI (web/index.html) which auto-opens the modal.
    - Else (default): serves the dedicated standalone research dossier page (web/research.html).
    """
    if view == "cockpit":
        index_file = WEB_DIR / "index.html"
        if not index_file.exists():
            raise HTTPException(status_code=404, detail="web/index.html not found")
        return FileResponse(str(index_file))

    research_file = WEB_DIR / "research.html"
    if research_file.exists():
        return FileResponse(str(research_file))

    # Fallback to index.html if research.html is not found
    index_file = WEB_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    raise HTTPException(status_code=404, detail="Research view not found")


@router.get("/research/{ticker}/trades", response_class=FileResponse)
def research_ticker_trades_page(ticker: str):
    """Serve the research dossier view for a ticker with trades tab focused."""
    research_file = WEB_DIR / "research.html"
    if research_file.exists():
        return FileResponse(str(research_file))
    index_file = WEB_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    raise HTTPException(status_code=404, detail="Research view not found")


@router.get("/research/{ticker}/{date}", response_class=FileResponse)
def research_ticker_date_page(ticker: str, date: str, view: str = "standalone"):
    """Serve research dossier view for a specific ticker and date."""
    return research_ticker_page(ticker=ticker, date=date, view=view)


@router.get("/research", response_class=FileResponse)
def research_hub_page():
    """Serve the research hub / standalone dossier viewer."""
    research_file = WEB_DIR / "research.html"
    if research_file.exists():
        return FileResponse(str(research_file))
    index_file = WEB_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    raise HTTPException(status_code=404, detail="Research page not found")
