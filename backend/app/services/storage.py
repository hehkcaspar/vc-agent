import os
import aiofiles
from abc import ABC, abstractmethod
from pathlib import Path
from app.config import settings


class StorageAdapter(ABC):
    """Abstract storage adapter interface."""
    
    @abstractmethod
    async def write_file(self, relative_path: str, content: bytes) -> str:
        """Write file and return the full path."""
        pass
    
    @abstractmethod
    async def read_file(self, relative_path: str) -> bytes:
        """Read file content."""
        pass
    
    @abstractmethod
    async def copy_file(self, source: str, dest: str) -> None:
        """Copy file from source to dest (both relative paths)."""
        pass
    
    @abstractmethod
    async def delete_file(self, relative_path: str) -> None:
        """Delete a file."""
        pass
    
    @abstractmethod
    async def ensure_dir(self, relative_path: str) -> None:
        """Ensure directory exists."""
        pass
    
    @abstractmethod
    async def exists(self, relative_path: str) -> bool:
        """Check if path exists."""
        pass
    
    @abstractmethod
    async def delete_recursive(self, relative_path: str) -> None:
        """Delete a directory and all its contents recursively."""
        pass
    
    @abstractmethod
    def get_full_path(self, relative_path: str) -> Path:
        """Get full filesystem path from relative path."""
        pass


class LocalFilesystemAdapter(StorageAdapter):
    """Local filesystem implementation of StorageAdapter."""
    
    def __init__(self, root_path: Path):
        self.root_path = Path(root_path)
        self.root_path.mkdir(parents=True, exist_ok=True)
    
    def get_full_path(self, relative_path: str) -> Path:
        """Get full filesystem path from relative path."""
        # Prevent directory traversal attacks
        full_path = (self.root_path / relative_path).resolve()
        if not str(full_path).startswith(str(self.root_path.resolve())):
            raise ValueError(f"Invalid path: {relative_path}")
        return full_path
    
    async def write_file(self, relative_path: str, content: bytes) -> str:
        full_path = self.get_full_path(relative_path)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(full_path, 'wb') as f:
            await f.write(content)
        return str(full_path)
    
    async def read_file(self, relative_path: str) -> bytes:
        full_path = self.get_full_path(relative_path)
        async with aiofiles.open(full_path, 'rb') as f:
            return await f.read()
    
    async def copy_file(self, source: str, dest: str) -> None:
        source_path = self.get_full_path(source)
        dest_path = self.get_full_path(dest)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        
        async with aiofiles.open(source_path, 'rb') as src:
            content = await src.read()
        async with aiofiles.open(dest_path, 'wb') as dst:
            await dst.write(content)
    
    async def delete_file(self, relative_path: str) -> None:
        full_path = self.get_full_path(relative_path)
        if full_path.exists():
            os.remove(full_path)
    
    async def ensure_dir(self, relative_path: str) -> None:
        full_path = self.get_full_path(relative_path)
        full_path.mkdir(parents=True, exist_ok=True)
    
    async def exists(self, relative_path: str) -> bool:
        full_path = self.get_full_path(relative_path)
        return full_path.exists()
    
    async def delete_recursive(self, relative_path: str) -> None:
        import shutil
        full_path = self.get_full_path(relative_path)
        if full_path.exists():
            shutil.rmtree(full_path, ignore_errors=True)

    def read_file_sync(self, relative_path: str) -> bytes:
        full_path = self.get_full_path(relative_path)
        return full_path.read_bytes()

    def write_file_sync(self, relative_path: str, content: bytes) -> str:
        full_path = self.get_full_path(relative_path)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(content)
        return str(full_path)

    def ensure_dir_sync(self, relative_path: str) -> None:
        full_path = self.get_full_path(relative_path)
        full_path.mkdir(parents=True, exist_ok=True)


# Global storage instance
storage = LocalFilesystemAdapter(settings.DATA_ROOT)
