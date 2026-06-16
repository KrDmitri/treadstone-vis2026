"""
File management API endpoints
"""
from fastapi import APIRouter, HTTPException, Query
from typing import List, Optional

from models.schemas import FileMetadata
from services import file_service
from services.client_store import get_client_store

router = APIRouter(prefix="/api", tags=["files"])


@router.get("/files", response_model=List[FileMetadata])
async def list_files(client_id: Optional[str] = Query(None)):
    """
    Get list of uploaded files, filtered by client_id if provided.
    Also merges files registered in the client store.
    """
    files = file_service.list_files(client_id=client_id)
    existing_ids = {f.get("file_id") for f in files}

    if client_id:
        store = get_client_store(client_id)
        for fmeta in store.files.values():
            if fmeta.get("file_id") not in existing_ids:
                files.append(fmeta)
                existing_ids.add(fmeta["file_id"])

    return [FileMetadata(**f) for f in files]


@router.get("/files/{file_id}")
async def get_file(file_id: str):
    """Get file metadata by ID"""
    file_metadata = file_service.get_file(file_id)
    if not file_metadata:
        raise HTTPException(status_code=404, detail="File not found")
    return file_metadata


@router.get("/files/{file_id}/preview")
async def preview_file(file_id: str, rows: int = 5, client_id: Optional[str] = None):
    """Preview uploaded file contents."""
    try:
        preview = file_service.preview_file(file_id, rows)
        return preview
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Preview failed: {str(e)}")


@router.delete("/files/{file_id}/delete")
async def delete_file(file_id: str):
    """Delete a file"""
    success = file_service.delete_file(file_id)
    if not success:
        raise HTTPException(status_code=404, detail="File not found")
    return {"message": "File deleted successfully", "file_id": file_id}
