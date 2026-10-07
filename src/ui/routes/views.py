"""
Frontend entry point view router.
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from src.ui.state import WEB_DIR

router = APIRouter(tags=["views"])


@router.get("/", response_class=FileResponse)
def index_page():
    """Serve the single-page cockpit UI from web/index.html."""
    index_file = WEB_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="web/index.html not found")
    return FileResponse(str(index_file))


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
