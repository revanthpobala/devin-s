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
