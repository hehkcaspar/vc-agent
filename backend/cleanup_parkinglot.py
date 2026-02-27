import asyncio
import shutil
from pathlib import Path
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models import IngestItem
from app.config import settings


async def check_parkinglot():
    """List all parking lot items."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(IngestItem).order_by(IngestItem.created_at.desc())
        )
        items = result.scalars().all()
        
        print(f"\nTotal parking lot items: {len(items)}\n")
        print(f"{'ID':<36} | {'Status':<20} | {'Name':<30} | Created")
        print("-" * 110)
        
        for item in items:
            name = item.entity_hint_name or "Unnamed"
            if len(name) > 28:
                name = name[:25] + "..."
            print(f"{item.ingest_id} | {item.status:<20} | {name:<30} | {item.created_at.strftime('%Y-%m-%d %H:%M')}")
        
        return items


async def clean_materialized(delete_files: bool = False):
    """Clean up materialized items from parking lot."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(IngestItem).where(IngestItem.status == "materialized")
        )
        items = result.scalars().all()
        
        print(f"\nFound {len(items)} materialized items to clean up.\n")
        
        cleaned = 0
        for item in items:
            parking_path = settings.DATA_ROOT / item.parkinglot_path
            
            if delete_files:
                # Delete from filesystem
                if parking_path.exists():
                    shutil.rmtree(parking_path, ignore_errors=True)
                    print(f"Deleted: {item.ingest_id} ({item.entity_hint_name or 'Unnamed'})")
                
                # Delete from database
                await db.delete(item)
                cleaned += 1
            else:
                print(f"Would delete: {item.ingest_id} ({item.entity_hint_name or 'Unnamed'})")
                print(f"  Path: {parking_path}")
        
        if delete_files and cleaned > 0:
            await db.commit()
            print(f"\nDeleted {cleaned} items.")
        elif not delete_files:
            print(f"\nRun with --delete flag to actually delete these items.")


async def clean_all(delete_files: bool = False):
    """Clean up ALL items from parking lot (use with caution!)."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(IngestItem))
        items = result.scalars().all()
        
        print(f"\nFound {len(items)} total items in parking lot.\n")
        
        if not items:
            print("Parking lot is already empty.")
            return
        
        cleaned = 0
        for item in items:
            parking_path = settings.DATA_ROOT / item.parkinglot_path
            
            if delete_files:
                if parking_path.exists():
                    shutil.rmtree(parking_path, ignore_errors=True)
                await db.delete(item)
                cleaned += 1
                print(f"Deleted: {item.ingest_id}")
            else:
                print(f"Would delete: {item.ingest_id} ({item.status}) - {item.entity_hint_name or 'Unnamed'}")
        
        if delete_files and cleaned > 0:
            await db.commit()
            print(f"\nDeleted {cleaned} items.")
        elif not delete_files:
            print(f"\nRun with --delete-all flag to actually delete these items.")


async def clean_orphaned_folders():
    """Clean up orphaned filesystem folders (no corresponding DB entry)."""
    parking_root = settings.DATA_ROOT / "00000" / "parkinglot"
    
    if not parking_root.exists():
        print("Parking lot directory doesn't exist.")
        return
    
    async with AsyncSessionLocal() as db:
        # Get all valid ingest_ids from database
        result = await db.execute(select(IngestItem.ingest_id))
        valid_ids = {row[0] for row in result.all()}
        
        # Check filesystem folders
        orphaned = []
        for folder in parking_root.iterdir():
            if folder.is_dir() and folder.name not in valid_ids:
                orphaned.append(folder)
        
        if orphaned:
            print(f"\nFound {len(orphaned)} orphaned folders:")
            for folder in orphaned:
                print(f"  {folder.name}")
                shutil.rmtree(folder, ignore_errors=True)
            print(f"\nDeleted {len(orphaned)} orphaned folders.")
        else:
            print("\nNo orphaned folders found.")


async def clean_materialized_files():
    """Clean up filesystem folders for materialized items (files already copied to entities)."""
    parking_root = settings.DATA_ROOT / "00000" / "parkinglot"
    
    if not parking_root.exists():
        print("Parking lot directory doesn't exist.")
        return
    
    async with AsyncSessionLocal() as db:
        # Get all materialized items
        result = await db.execute(
            select(IngestItem).where(IngestItem.status == "materialized")
        )
        materialized_items = result.scalars().all()
        
        cleaned = 0
        for item in materialized_items:
            folder_path = parking_root / item.ingest_id
            if folder_path.exists():
                shutil.rmtree(folder_path, ignore_errors=True)
                print(f"Deleted materialized folder: {item.ingest_id}")
                cleaned += 1
        
        if cleaned > 0:
            print(f"\nDeleted {cleaned} materialized item folders.")
        else:
            print("\nNo materialized item folders to clean.")


if __name__ == "__main__":
    import sys
    
    args = sys.argv[1:]
    
    if "--help" in args or "-h" in args:
        print("""
Parking Lot Cleanup Utility

Usage:
  python cleanup_parkinglot.py          # List all items
  python cleanup_parkinglot.py --delete # Delete materialized items
  python cleanup_parkinglot.py --delete-all  # Delete ALL items (careful!)
  python cleanup_parkinglot.py --orphans     # Clean orphaned folders
  python cleanup_parkinglot.py --clean-materialized  # Clean materialized file folders

Options:
  --delete                Remove materialized items (both DB and files)
  --delete-all            Remove ALL parking lot items (careful!)
  --orphans               Clean up orphaned filesystem folders
  --clean-materialized    Clean up file folders for materialized items
  -h, --help              Show this help message
        """)
        sys.exit(0)
    
    if "--delete-all" in args:
        print("WARNING: This will delete ALL parking lot items!")
        confirm = input("Type 'yes' to confirm: ")
        if confirm.lower() == "yes":
            asyncio.run(clean_all(delete_files=True))
        else:
            print("Cancelled.")
    elif "--delete" in args:
        asyncio.run(clean_materialized(delete_files=True))
    elif "--orphans" in args:
        asyncio.run(clean_orphaned_folders())
    elif "--clean-materialized" in args:
        asyncio.run(clean_materialized_files())
    else:
        # Default: just list items
        asyncio.run(check_parkinglot())
        print("\n" + "="*60)
        print("Run with --delete to remove materialized items")
        print("Run with --delete-all to remove ALL items")
        print("Run with --orphans to clean orphaned folders")
        print("Run with --clean-materialized to clean materialized file folders")
