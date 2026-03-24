"""FastAPI backend for Resume Screener."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import warnings
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Suppress LangChain Pydantic V1 warning
warnings.filterwarnings("ignore", message="Core Pydantic V1 functionality isn't compatible with Python 3.14 or greater.")

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Ensure backend dir is on sys.path for local imports
import sys
_backend_dir = str(Path(__file__).parent)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from config import ScreenerConfig, get_config, set_config
from watcher import ResumeQueue, ResumeFile, FileWatcher
from screener import ResumeScreener, ScreeningResult, JDStore

# Global state
queue: Optional[ResumeQueue] = None
watcher: Optional[FileWatcher] = None
screener: Optional[ResumeScreener] = None
jd_store: Optional[JDStore] = None

# WebSocket connections
websocket_connections: List[WebSocket] = []


class StatusResponse(BaseModel):
    """API status response."""
    status: str
    queue_size: int
    processing: Optional[str]
    uptime_seconds: float


class ConfigUpdate(BaseModel):
    """Configuration update request."""
    incoming_dir: Optional[str] = None
    jds_file: Optional[str] = None
    poll_interval: Optional[float] = None


class PositionInfo(BaseModel):
    """Position information."""
    id: str
    title: str
    department: str
    description: str


class ConclusionResponse(BaseModel):
    """Screening conclusion for frontend display."""
    verdict: str  # "invite", "waitlist", "reject"
    verdict_display: str
    verdict_color: str
    candidate_name: Optional[str]
    position_title: str
    summary: str
    confidence: str
    strengths: List[str]
    gaps: List[str]
    experience_years: Optional[float]
    ai_competency: Optional[Dict[str, Any]]
    reasoning: str
    processing_time_seconds: float
    evaluated_at: str
    id: str  # evaluation id


def _get_conclusion_response(result: ScreeningResult) -> ConclusionResponse:
    """Convert ScreeningResult to frontend-friendly ConclusionResponse."""
    verdict_display = {
        "invite": "邀请面试 / Invite",
        "waitlist": "待定 / Waitlist",
        "reject": "不匹配 / Not a Match",
    }.get(result.verdict, "Pending Review")
    
    verdict_color = {
        "invite": "success",
        "waitlist": "warning", 
        "reject": "danger",
    }.get(result.verdict, "neutral")
    
    return ConclusionResponse(
        id=result.id,
        verdict=result.verdict,
        verdict_display=verdict_display,
        verdict_color=verdict_color,
        candidate_name=result.candidate_name,
        position_title=jd_store.get_position(result.position_id).title if jd_store else "Unknown",
        summary=result.summary,
        confidence=result.confidence,
        strengths=result.strengths[:5],  # Top 5
        gaps=result.gaps[:3],  # Top 3
        experience_years=result.experience_years,
        ai_competency=result.ai_competency,
        reasoning=result.reasoning,
        processing_time_seconds=result.processing_time_seconds,
        evaluated_at=result.evaluated_at,
    )


async def _broadcast_event(event_type: str, data: dict) -> None:
    """Broadcast event to all WebSocket connections."""
    logger.info(f"Broadcasting event '{event_type}' to {len(websocket_connections)} clients")
    message = json.dumps({"type": event_type, "data": data, "timestamp": datetime.now().isoformat()})
    disconnected = []
    
    for ws in websocket_connections:
        try:
            await ws.send_text(message)
            logger.debug(f"Sent to WebSocket client")
        except Exception as e:
            logger.warning(f"WebSocket send failed: {e}")
            disconnected.append(ws)
    
    # Remove disconnected clients
    for ws in disconnected:
        if ws in websocket_connections:
            websocket_connections.remove(ws)
    
    if disconnected:
        logger.info(f"Removed {len(disconnected)} disconnected clients")


def _on_queue_event(resume: ResumeFile, event_type: str) -> None:
    """Callback for queue events."""
    logger.info(f"Queue event callback: {event_type} for {resume.original_name}")
    # Don't broadcast 'completed' or 'error' here - they're handled in _process_loop
    # with the full evaluation result, not just resume metadata
    if event_type not in ("completed", "error"):
        asyncio.create_task(_broadcast_event(event_type, resume.to_dict()))
    else:
        logger.info(f"Skipping broadcast for {event_type} - handled by _process_loop")


async def _process_loop() -> None:
    """Background task to process pending resumes."""
    while True:
        try:
            # Get next pending resume
            resume = await queue.get_pending()
            
            if resume:
                logger.info(f"[MAIN] Processing resume: {resume.original_name} (id: {resume.id}, status: {resume.status})")
                await queue.mark_processing(resume.id)
                logger.info(f"[MAIN] After mark_processing, broadcasting 'processing' event")
                await _broadcast_event("processing", resume.to_dict())
                
                try:
                    # Run screening
                    logger.info(f"Starting screening for {resume.original_name}")
                    result = await screener.screen(
                        resume_id=resume.id,
                        resume_path=resume.file_path,
                    )
                    logger.info(f"Screening completed for {resume.original_name}, result id: {result.id}")
                    
                    # Mark as completed
                    await queue.mark_completed(resume.id, result.id)
                    logger.info(f"Marked as completed in queue")
                    
                    # Prepare and broadcast result
                    try:
                        conclusion_response = _get_conclusion_response(result)
                        logger.info(f"Got conclusion response")
                        conclusion_data = conclusion_response.model_dump()
                        logger.info(f"Prepared conclusion data with keys: {list(conclusion_data.keys())}")
                        await _broadcast_event("completed", conclusion_data)
                        logger.info(f"[MAIN] Broadcast completed successfully")
                    except Exception as broadcast_err:
                        logger.error(f"[MAIN] Failed to broadcast result: {broadcast_err}", exc_info=True)
                        raise
                    
                    logger.info(f"[MAIN] === Finished processing {resume.original_name} ===")
                    
                except Exception as e:
                    logger.error(f"[MAIN] Screening failed for {resume.original_name}: {e}", exc_info=True)
                    try:
                        await queue.mark_error(resume.id, str(e))
                        await _broadcast_event("error", {"resume_id": resume.id, "error": str(e)})
                    except Exception as inner_e:
                        logger.error(f"Failed to handle error: {inner_e}")
            
            await asyncio.sleep(0.5)  # Small delay between checks
            
        except Exception as e:
            logger.error(f"Process loop error: {e}")
            await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    global queue, watcher, screener, jd_store
    
    # Startup
    logger.info("Starting Resume Screener...")
    
    queue = ResumeQueue()
    queue.add_callback(_on_queue_event)
    
    watcher = FileWatcher(queue)
    await watcher.start()
    
    screener = ResumeScreener()
    jd_store = JDStore()
    
    # Start processing loop
    process_task = asyncio.create_task(_process_loop())
    
    yield
    
    # Shutdown
    logger.info("Shutting down Resume Screener...")
    process_task.cancel()
    await watcher.stop()


app = FastAPI(title="Resume Screener API", version="1.0.0", lifespan=lifespan)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files
frontend_path = Path(__file__).parent.parent / "frontend"
if frontend_path.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_path)), name="static")


@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the main frontend."""
    index_file = frontend_path / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return HTMLResponse("<h1>Resume Screener API</h1><p>Frontend not found. Please check installation.</p>")


@app.get("/api/status", response_model=StatusResponse)
async def get_status():
    """Get current system status."""
    processing = queue.get_current()
    return StatusResponse(
        status="running",
        queue_size=len([r for r in queue.list_all() if r.status == "pending"]),
        processing=processing.id if processing else None,
        uptime_seconds=0,  # Could track actual uptime if needed
    )


@app.get("/api/positions", response_model=List[PositionInfo])
async def get_positions():
    """Get all available positions."""
    positions = jd_store.list_positions()
    return [
        PositionInfo(
            id=p.id,
            title=p.title,
            department=p.department,
            description=p.description,
        )
        for p in positions
    ]


@app.get("/api/current")
async def get_current():
    """Get the currently processing resume."""
    current = queue.get_current()
    if not current:
        return {"status": "idle"}
    
    return {
        "status": "processing",
        "resume": current.to_dict(),
    }


@app.get("/api/evaluation/{evaluation_id}", response_model=ConclusionResponse)
async def get_evaluation(evaluation_id: str):
    """Get a specific evaluation result."""
    result = screener.get_evaluation(evaluation_id)
    if not result:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    
    return _get_conclusion_response(result)


@app.get("/api/preview/{resume_id}")
async def get_preview(resume_id: str):
    """Get the file content for previewing."""
    resume = queue.get_resume(resume_id)
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")
    
    file_path = Path(resume.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    
    # Check if it's a supported preview type
    ext = file_path.suffix.lower()
    if ext not in (".pdf", ".png", ".jpg", ".jpeg", ".webp"):
        raise HTTPException(status_code=400, detail="Preview not supported for this file type")
    
    media_type_map = {
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }
    media_type = media_type_map.get(ext, "application/octet-stream")
    
    return FileResponse(str(file_path), media_type=media_type)



@app.get("/api/config")
async def get_config_endpoint():
    """Return current configuration values (poll interval etc.)."""
    config = get_config()
    return {
        "poll_interval": config.poll_interval,
        "incoming_dir": config.incoming_dir,
        "jds_file": config.jds_file,
    }

@app.post("/api/config")
async def update_config(config_update: ConfigUpdate):
    """Update configuration."""
    config = get_config()
    
    if config_update.incoming_dir:
        config.incoming_dir = config_update.incoming_dir
        Path(config.incoming_dir).mkdir(parents=True, exist_ok=True)
    
    if config_update.jds_file:
        config.jds_file = config_update.jds_file
        jd_store.reload()
    
    if config_update.poll_interval:
        # enforce minimum 3 seconds
        config.poll_interval = max(3.0, config_update.poll_interval)
    
    set_config(config)
    return {"status": "updated"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates."""
    await websocket.accept()
    websocket_connections.append(websocket)
    logger.info(f"WebSocket client connected. Total: {len(websocket_connections)}")
    
    try:
        # Send initial status
        await websocket.send_text(json.dumps({
            "type": "connected",
            "data": {"message": "Connected to Resume Screener"},
            "timestamp": datetime.now().isoformat(),
        }))
        
        while True:
            # Keep connection alive and handle client messages
            try:
                message = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                data = json.loads(message)
                
                # Handle ping
                if data.get("type") == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
                    
            except asyncio.TimeoutError:
                # Send keepalive
                try:
                    await websocket.send_text(json.dumps({"type": "keepalive"}))
                except Exception:
                    break
                    
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        if websocket in websocket_connections:
            websocket_connections.remove(websocket)


if __name__ == "__main__":
    import uvicorn
    import socket

    # Allow overriding port with env var `PORT` or `RESUME_SCREENER_PORT`
    try:
        default_port = int(os.getenv("PORT") or os.getenv("RESUME_SCREENER_PORT") or 8000)
    except ValueError:
        default_port = 8000

    def _is_port_free(p: int, host: str = "0.0.0.0") -> bool:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, p))
            s.close()
            return True
        except OSError:
            return False

    port = default_port
    max_tries = 10
    for i in range(max_tries):
        if _is_port_free(port):
            break
        port += 1

    if port != default_port:
        logger.warning(f"Port {default_port} in use. Falling back to port {port}.")

    try:
        uvicorn.run(app, host="0.0.0.0", port=port)
    except OSError as e:
        logger.error(f"Failed to start server on port {port}: {e}")
        raise
