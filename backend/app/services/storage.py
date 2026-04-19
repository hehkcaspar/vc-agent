import os
import aiofiles
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional
from app.config import settings


@dataclass
class UploadPlan:
    """What the client needs to complete a direct-to-storage upload.

    Different deployments use different upload strategies:
      - Local filesystem / any adapter without signed URLs → client falls
        back to the legacy ``POST /workspace/file`` endpoint (set
        ``use_direct_upload=True`` here).
      - GCS / S3 / Azure Blob → client PUTs bytes directly to the object
        store using ``url`` + ``headers``; the backend's request path is
        limited to the small init/commit JSON calls.
    """

    storage_key: str
    method: Literal["PUT", "POST"] = "PUT"
    url: Optional[str] = None
    headers: dict[str, str] = field(default_factory=dict)
    max_bytes: int = 0
    ttl_seconds: int = 1800
    use_direct_upload: bool = False


class StorageAdapter(ABC):
    """Abstract storage adapter interface.

    Carries both blob I/O (read/write/exists/…) and the deployment's
    upload strategy (``mint_upload`` + ``finalize_upload``). Each
    deployment plugs in one adapter at process startup; the rest of the
    service and HTTP layers are adapter-agnostic.
    """

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

    @abstractmethod
    async def file_checksum(self, relative_path: str) -> str:
        """SHA-256 hex digest of file contents."""
        pass

    @abstractmethod
    async def file_size(self, relative_path: str) -> int:
        """Byte count of an already-persisted file."""
        pass

    @abstractmethod
    def mint_upload(
        self,
        storage_key: str,
        *,
        content_type: Optional[str] = None,
        size: Optional[int] = None,
        ttl_seconds: int = 1800,
    ) -> UploadPlan:
        """Return an upload plan for the client.

        Adapters without direct-to-store support return
        ``use_direct_upload=True`` so the client posts bytes through the
        existing backend endpoints. GCS/S3/Azure adapters return a
        pre-signed URL the client can PUT to.
        """
        pass

    @abstractmethod
    async def finalize_upload(self, storage_key: str) -> tuple[int, str]:
        """Validate an uploaded blob and return ``(size, sha256)``.

        Called by ``POST /workspace/upload-commit`` after the client has
        PUT the file. Raises ``FileNotFoundError`` if the blob isn't
        actually in storage.
        """
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

    async def file_checksum(self, relative_path: str) -> str:
        import hashlib
        content = await self.read_file(relative_path)
        return hashlib.sha256(content).hexdigest()

    def file_checksum_sync(self, relative_path: str) -> str:
        import hashlib
        content = self.read_file_sync(relative_path)
        return hashlib.sha256(content).hexdigest()

    async def file_size(self, relative_path: str) -> int:
        full_path = self.get_full_path(relative_path)
        if not full_path.exists():
            raise FileNotFoundError(relative_path)
        return full_path.stat().st_size

    def mint_upload(
        self,
        storage_key: str,
        *,
        content_type: Optional[str] = None,
        size: Optional[int] = None,
        ttl_seconds: int = 1800,
    ) -> UploadPlan:
        """Default: client POSTs through the legacy backend endpoint.

        No signed URL issued. The ``use_direct_upload`` flag signals the
        frontend to fall back to ``POST /workspace/file``. Local dev and
        any deployment that doesn't front its filesystem with an object
        store hit this path.
        """
        return UploadPlan(
            storage_key=storage_key,
            method="POST",
            url=None,
            headers={},
            max_bytes=settings.WORKSPACE_MAX_FILE_BYTES,
            ttl_seconds=ttl_seconds,
            use_direct_upload=True,
        )

    async def finalize_upload(self, storage_key: str) -> tuple[int, str]:
        full_path = self.get_full_path(storage_key)
        if not full_path.exists():
            raise FileNotFoundError(storage_key)
        size = full_path.stat().st_size
        checksum = await self.file_checksum(storage_key)
        return size, checksum


class GcsSignedUrlAdapter(LocalFilesystemAdapter):
    """GCS-backed storage with v4 signed PUT URL support.

    Inherits all read/write methods from ``LocalFilesystemAdapter`` —
    on Cloud Run the bucket is FUSE-mounted at ``DATA_ROOT`` so filesystem
    I/O goes through the same code path. The only difference is
    ``mint_upload``, which returns a pre-signed URL so the browser can
    PUT bytes straight to GCS and bypass Cloud Run's 32 MB request-body
    ceiling.

    Dependencies:
      - ``google-cloud-storage`` Python package (lazy import)
      - Compute SA needs ``roles/iam.serviceAccountTokenCreator`` on
        itself to self-sign credentials (no service-account key file).
    """

    def __init__(self, root_path: Path, bucket_name: str):
        super().__init__(root_path)
        self.bucket_name = bucket_name
        self._gcs_client = None  # lazy

    def _client(self):
        if self._gcs_client is None:
            from google.cloud import storage as gcs_storage  # lazy
            self._gcs_client = gcs_storage.Client()
        return self._gcs_client

    def mint_upload(
        self,
        storage_key: str,
        *,
        content_type: Optional[str] = None,
        size: Optional[int] = None,
        ttl_seconds: int = 1800,
    ) -> UploadPlan:
        from datetime import timedelta

        content_type = content_type or "application/octet-stream"
        try:
            blob = self._client().bucket(self.bucket_name).blob(storage_key)
            url = blob.generate_signed_url(
                version="v4",
                expiration=timedelta(seconds=ttl_seconds),
                method="PUT",
                content_type=content_type,
            )
        except Exception:
            # Missing dep / IAM misconfig: fall back to direct-POST so
            # small uploads keep working while the operator fixes the
            # signed-URL path. Logged upstream by the router.
            return super().mint_upload(
                storage_key,
                content_type=content_type,
                size=size,
                ttl_seconds=ttl_seconds,
            )

        return UploadPlan(
            storage_key=storage_key,
            method="PUT",
            url=url,
            headers={"Content-Type": content_type},
            max_bytes=settings.WORKSPACE_MAX_FILE_BYTES,
            ttl_seconds=ttl_seconds,
            use_direct_upload=False,
        )


# Global storage instance — one adapter per process, chosen at startup.
if settings.GCS_BUCKET:
    storage: StorageAdapter = GcsSignedUrlAdapter(settings.DATA_ROOT, settings.GCS_BUCKET)
else:
    storage = LocalFilesystemAdapter(settings.DATA_ROOT)
