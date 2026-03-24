"""File watching and queue management for incoming resumes."""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set
import logging
import sys

# Ensure backend dir is on sys.path for local imports
_backend_dir = str(Path(__file__).parent)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from config import get_config

logger = logging.getLogger(__name__)


@dataclass
class ResumeFile:
    """Represents a resume file in the system."""
    id: str
    original_name: str
    file_path: Path
    file_size: int
    created_at: datetime
    status: str = "pending"  # pending, processing, completed, error
    evaluation_id: Optional[str] = None
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "original_name": self.original_name,
            "file_path": str(self.file_path),
            "file_size": self.file_size,
            "created_at": self.created_at.isoformat(),
            "status": self.status,
            "evaluation_id": self.evaluation_id,
        }


class ResumeQueue:
    """Manages the queue of resumes to be processed."""
    
    def __init__(self):
        self.config = get_config()
        self._resumes: Dict[str, ResumeFile] = {}
        self._processing: Optional[str] = None
        self._callbacks: List[Callable[[ResumeFile, str], None]] = []
        self._lock = asyncio.Lock()
        
        # Ensure directories exist
        Path(self.config.incoming_dir).mkdir(parents=True, exist_ok=True)
        Path(self.config.processed_dir).mkdir(parents=True, exist_ok=True)
        Path(self.config.evaluations_dir).mkdir(parents=True, exist_ok=True)
    
    def add_callback(self, callback: Callable[[ResumeFile, str], None]) -> None:
        """Add a callback for status changes. Callback receives (resume, event_type)."""
        self._callbacks.append(callback)
    
    def _notify(self, resume: ResumeFile, event_type: str) -> None:
        """Notify all callbacks of a status change."""
        for callback in self._callbacks:
            try:
                callback(resume, event_type)
            except Exception as e:
                logger.error(f"Callback error: {e}")
    
    def _generate_id(self, file_path: Path) -> str:
        """Generate unique ID for a file."""
        content = f"{file_path.name}_{file_path.stat().st_mtime}_{file_path.stat().st_size}"
        return hashlib.md5(content.encode()).hexdigest()[:12]
    
    async def scan(self) -> List[ResumeFile]:
        """Scan incoming directory for new files."""
        incoming = Path(self.config.incoming_dir)
        logger.info(f"[SCAN] Scanning directory: {incoming}")
        if not incoming.exists():
            logger.warning(f"[SCAN] Directory does not exist: {incoming}")
            return []
        
        # List all files found
        all_files = list(incoming.iterdir())
        logger.info(f"[SCAN] Found {len(all_files)} items in {incoming}")
        for f in all_files:
            logger.info(f"[SCAN]   - {f.name} (is_file: {f.is_file()})")
        
        new_resumes = []
        seen_ids = set()
        
        for file_path in incoming.iterdir():
            if not file_path.is_file():
                continue
            
            if file_path.suffix.lower() not in self.config.supported_extensions:
                continue
            
            # Skip hidden files and temp files
            if file_path.name.startswith(".") or file_path.name.startswith("~"):
                continue
            
            file_id = self._generate_id(file_path)
            seen_ids.add(file_id)
            
            async with self._lock:
                if file_id not in self._resumes:
                    resume = ResumeFile(
                        id=file_id,
                        original_name=file_path.name,
                        file_path=file_path,
                        file_size=file_path.stat().st_size,
                        created_at=datetime.fromtimestamp(file_path.stat().st_mtime),
                    )
                    self._resumes[file_id] = resume
                    new_resumes.append(resume)
                    logger.info(f"[SCAN] New resume detected: {file_path.name} (id: {file_id}, path: {file_path})")
                    self._notify(resume, "detected")
                else:
                    # Log existing resume status for debugging
                    existing = self._resumes[file_id]
                    logger.debug(f"[SCAN] Existing resume: {file_path.name} (status: {existing.status})")
        
        return new_resumes
    
    async def get_pending(self) -> Optional[ResumeFile]:
        """Get the next pending resume."""
        async with self._lock:
            for resume in self._resumes.values():
                if resume.status == "pending":
                    logger.info(f"[QUEUE] Found pending resume: {resume.original_name} (id: {resume.id}, path: {resume.file_path})")
                    return resume
            return None
    
    async def mark_processing(self, resume_id: str) -> None:
        """Mark a resume as being processed."""
        async with self._lock:
            if resume_id in self._resumes:
                resume = self._resumes[resume_id]
                logger.info(f"[WATCHER] mark_processing called for {resume.original_name} (current status: {resume.status})")
                resume.status = "processing"
                self._processing = resume_id
                self._notify(resume, "processing")
    
    async def mark_completed(self, resume_id: str, evaluation_id: str) -> None:
        """Mark a resume as completed and move to processed folder."""
        async with self._lock:
            if resume_id in self._resumes:
                resume = self._resumes[resume_id]
                resume.status = "completed"
                resume.evaluation_id = evaluation_id
                
                # Move file to processed folder
                processed_path = Path(self.config.processed_dir) / f"{resume.id}_{resume.original_name}"
                try:
                    shutil.move(str(resume.file_path), str(processed_path))
                    resume.file_path = processed_path
                except Exception as e:
                    logger.error(f"Failed to move file: {e}")
                
                self._processing = None
                self._notify(resume, "completed")
    
    async def mark_error(self, resume_id: str, error: str) -> None:
        """Mark a resume as failed."""
        async with self._lock:
            if resume_id in self._resumes:
                self._resumes[resume_id].status = f"error: {error}"
                self._processing = None
                self._notify(self._resumes[resume_id], "error")
    
    def get_resume(self, resume_id: str) -> Optional[ResumeFile]:
        """Get a resume by ID."""
        return self._resumes.get(resume_id)
    
    def get_current(self) -> Optional[ResumeFile]:
        """Get the currently processing resume."""
        if self._processing:
            return self._resumes.get(self._processing)
        return None
    
    def list_all(self) -> List[ResumeFile]:
        """List all resumes."""
        return list(self._resumes.values())


class FileWatcher:
    """Watches for new files and triggers processing."""
    
    def __init__(self, queue: ResumeQueue):
        self.config = get_config()
        self.queue = queue
        self._running = False
        self._task: Optional[asyncio.Task] = None
    
    async def start(self) -> None:
        """Start watching for files."""
        self._running = True
        self._task = asyncio.create_task(self._watch_loop())
        logger.info(f"File watcher started (interval: {self.config.poll_interval}s)")
    
    async def stop(self) -> None:
        """Stop watching."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("File watcher stopped")
    
    async def _watch_loop(self) -> None:
        """Main watch loop."""
        while self._running:
            try:
                await self.queue.scan()
            except Exception as e:
                logger.error(f"Scan error: {e}")
            
            try:
                await asyncio.sleep(self.config.poll_interval)
            except asyncio.CancelledError:
                break
