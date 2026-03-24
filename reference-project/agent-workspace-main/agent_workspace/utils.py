"""Utility functions: retry logic, progress callbacks, and helpers."""

from __future__ import annotations

import logging
import random
import time
from functools import wraps
from typing import Any, Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def with_retry(
    max_attempts: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 10.0,
    retryable_exceptions: Optional[tuple] = None,
    retryable_status_codes: Optional[tuple] = None,
):
    """Decorator for retry logic with exponential backoff and jitter.

    Args:
        max_attempts: Maximum number of retry attempts
        initial_delay: Initial delay between retries in seconds
        max_delay: Maximum delay between retries in seconds
        retryable_exceptions: Tuple of exception types to retry on
        retryable_status_codes: HTTP status codes to retry on (429, 5xx)

    Example:
        @with_retry(max_attempts=3, initial_delay=1.0)
        def call_llm(prompt: str) -> str:
            # ... API call that might fail
            pass
    """
    if retryable_exceptions is None:
        retryable_exceptions = (Exception,)
    
    if retryable_status_codes is None:
        # Retry on rate limits (429) and server errors (5xx)
        retryable_status_codes = (429, 502, 503, 504)

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            last_exception: Optional[Exception] = None
            
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    
                    # Check if this is a retryable error
                    should_retry = False
                    
                    # Check by exception type
                    if isinstance(e, retryable_exceptions):
                        should_retry = True
                    
                    # Check for HTTP status code in exception
                    if hasattr(e, "status_code"):
                        status_code = getattr(e, "status_code")
                        if status_code in retryable_status_codes:
                            should_retry = True
                        else:
                            should_retry = False
                    elif hasattr(e, "response") and hasattr(e.response, "status_code"):
                        status_code = getattr(e.response, "status_code")
                        if status_code in retryable_status_codes:
                            should_retry = True
                        else:
                            should_retry = False
                    
                    # Don't retry on last attempt
                    if attempt == max_attempts - 1 or not should_retry:
                        logger.error(
                            f"Max retries exceeded for {func.__name__}: {e}"
                        )
                        raise
                    
                    # Calculate delay with exponential backoff and jitter
                    delay = min(initial_delay * (2 ** attempt), max_delay)
                    delay += random.uniform(0, 0.5)  # Add jitter
                    
                    logger.warning(
                        f"Attempt {attempt + 1}/{max_attempts} failed for {func.__name__}: {e}. "
                        f"Retrying in {delay:.1f}s..."
                    )
                    print(f"[retry] Attempt {attempt + 1} failed, waiting {delay:.1f}s...")
                    time.sleep(delay)
            
            # Should never reach here, but just in case
            if last_exception:
                raise last_exception
            raise RuntimeError(f"Max retries exceeded for {func.__name__}")
        
        return wrapper
    return decorator


class ProgressCallback:
    """Callback handler for printing agent progress as it works."""
    
    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self.step_count = 0
    
    def on_tool_start(self, tool_name: str, tool_input: dict) -> None:
        """Called when a tool is about to be invoked."""
        if not self.verbose:
            return
        self.step_count += 1
        
        # Format tool input for display
        input_summary = self._format_tool_input(tool_name, tool_input)
        print(f"[step {self.step_count}] Tool: {tool_name}")
        if input_summary:
            print(f"           Input: {input_summary}")
    
    def on_tool_end(self, tool_name: str, output: str) -> None:
        """Called when a tool finishes."""
        if not self.verbose:
            return
        
        # Truncate long outputs
        display_output = output
        if len(output) > 150:
            display_output = output[:150] + "..."
        display_output = display_output.replace("\n", " ")
        
        print(f"           Result: {display_output}")
        print()
    
    def on_llm_start(self, message: str) -> None:
        """Called when LLM is invoked."""
        if not self.verbose:
            return
        print(f"[llm] Thinking...")
    
    def on_llm_end(self, response: str) -> None:
        """Called when LLM responds."""
        if not self.verbose:
            return
        # Don't print full LLM response here (will be at end)
        pass
    
    def _format_tool_input(self, tool_name: str, tool_input: dict) -> str:
        """Format tool input for progress display."""
        if not tool_input:
            return ""
        
        # Tool-specific formatting
        if tool_name == "scan_resources":
            return f"workspace: {tool_input.get('workspace_root', '...')}"
        
        elif tool_name == "extract_content":
            files = tool_input.get("file_paths", [])
            if isinstance(files, list):
                if len(files) == 1:
                    return f"file: {files[0]}"
                else:
                    return f"files: {', '.join(files[:2])}" + (f" (+{len(files)-2} more)" if len(files) > 2 else "")
            return ""
        
        elif tool_name == "write_artifact":
            artifact_type = tool_input.get("artifact_type", "unknown")
            name = tool_input.get("name", "unknown")
            content = tool_input.get("content", "")
            content_preview = content[:50].replace("\n", " ") if content else ""
            return f"{artifact_type}/{name} ({len(content)} chars)"
        
        elif tool_name == "read_artifact":
            return f"path: {tool_input.get('artifact_path', '...')}"
        
        elif tool_name == "search_resources":
            return f"query: '{tool_input.get('query', '...')}'"
        
        # Default: show first key-value pair
        else:
            first_key = next(iter(tool_input.keys())) if tool_input else ""
            first_val = str(tool_input.get(first_key, ""))[:40]
            return f"{first_key}={first_val}"


def format_file_size(size_bytes: int) -> str:
    """Format file size in human-readable format."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def truncate_text(text: str, max_length: int = 1000, suffix: str = "...") -> str:
    """Truncate text to max length, preserving word boundaries."""
    if len(text) <= max_length:
        return text
    
    # Try to break at word boundary
    truncated = text[:max_length - len(suffix)]
    last_space = truncated.rfind(" ")
    if last_space > max_length * 0.8:  # Only if we don't lose too much
        truncated = truncated[:last_space]
    
    return truncated + suffix
