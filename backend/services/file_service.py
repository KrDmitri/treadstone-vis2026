"""
File management service
Handles file uploads, storage, and metadata management
"""
import os
import uuid
import pandas as pd
import duckdb
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List
from fastapi import UploadFile, HTTPException
from openai import OpenAI

from config import settings
from models.schemas import FileMetadata
from utils.logger import logger


class FileService:
    """Service for managing file operations"""
    
    def __init__(self):
        self.files_db: Dict[str, dict] = {}
        self.openai_client = OpenAI(api_key=settings.OPENAI_API_KEY)
        settings.ensure_upload_dir()
    
    async def upload_file(self, file: UploadFile, client_id: Optional[str] = None) -> FileMetadata:
        """
        Upload and process a file
        
        Args:
            file: Uploaded file
            client_id: Optional client ID for isolation
            
        Returns:
            FileMetadata: Metadata for the uploaded file
            
        Raises:
            HTTPException: If file type is not supported or processing fails
        """
        logger.info(f"Uploading file: {file.filename} for client: {client_id[:20] if client_id else 'N/A'}...")
        
        # Validate file extension
        file_ext = Path(file.filename).suffix.lower()
        if file_ext not in settings.ALLOWED_EXTENSIONS:
            logger.error(f"File type {file_ext} not supported")
            raise HTTPException(
                status_code=400,
                detail=f"File type {file_ext} not supported. Allowed types: {settings.ALLOWED_EXTENSIONS}"
            )
        
        # Generate unique file ID
        file_id = str(uuid.uuid4())
        logger.debug(f"Generated file_id: {file_id}")
        
        # Save file to disk
        file_path = settings.UPLOAD_DIR / f"{file_id}{file_ext}"
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)
        
        logger.info(f"File saved to: {file_path} ({len(content)} bytes)")
        
        # Extract metadata based on file type
        file_type = file_ext.lstrip('.')
        metadata = {
            "file_id": file_id,
            "original_filename": file.filename,
            "file_type": file_type,
            "size": len(content),
            "uploaded_at": datetime.now().isoformat(),
            "file_path": str(file_path),
            "client_id": client_id,  # For client isolation
        }
        
        if file_type == "csv":
            # CSV-specific metadata
            try:
                # Try UTF-8 first, then fall back to latin1 for compatibility
                try:
                    df = pd.read_csv(file_path, encoding='utf-8')
                except UnicodeDecodeError:
                    logger.warning("UTF-8 decoding failed, trying latin1 encoding...")
                    df = pd.read_csv(file_path, encoding='latin1')
                
                metadata.update({
                    "rows": len(df),
                    "columns": len(df.columns),
                    "column_names": df.columns.tolist(),
                })
                logger.info(f"CSV metadata: {len(df)} rows, {len(df.columns)} columns")
                
                # ========== DuckDB Integration ==========
                # Convert CSV to DuckDB for efficient querying
                db_path = settings.UPLOAD_DIR / f"{file_id}.duckdb"
                logger.info(f"Creating DuckDB database: {db_path}")
                
                try:
                    # Create DuckDB connection and import CSV with ignore_errors for problematic files
                    conn = duckdb.connect(str(db_path))
                    
                    # First, read to detect columns with ignore_errors enabled
                    try:
                        temp_df = conn.execute(f"SELECT * FROM read_csv('{file_path}', header=true, auto_detect=true, ignore_errors=true) LIMIT 1").fetchdf()
                    except Exception as e:
                        logger.warning(f"DuckDB with ignore_errors failed, trying all_varchar: {str(e)}")
                        try:
                            temp_df = conn.execute(f"SELECT * FROM read_csv('{file_path}', header=true, auto_detect=true, all_varchar=true, ignore_errors=true) LIMIT 1").fetchdf()
                        except Exception as e2:
                            # If both fail, close connection and delete incomplete db file
                            conn.close()
                            if db_path.exists():
                                db_path.unlink()
                            raise Exception(f"All DuckDB import attempts failed: {str(e2)}")
                    
                    # Get all column names except the first one (which is usually empty or an index)
                    all_columns = temp_df.columns.tolist()
                    if len(all_columns) > 0 and (str(all_columns[0]).strip() == '' or str(all_columns[0]).startswith('column')):
                        # Skip the first column (unnamed index)
                        columns_to_select = all_columns[1:]
                        columns_str = ', '.join([f'"{col}"' for col in columns_to_select])
                        logger.info(f"Skipping first column (index), importing {len(columns_to_select)} data columns")
                    else:
                        # Use all columns
                        columns_str = '*'
                        logger.info(f"Importing all {len(all_columns)} columns")
                    
                    # Create table with selected columns and ignore_errors
                    conn.execute(f"""
                        CREATE TABLE data AS 
                        SELECT {columns_str} FROM read_csv('{file_path}', header=true, auto_detect=true, ignore_errors=true)
                    """)
                    
                    # Verify table creation
                    row_count = conn.execute("SELECT COUNT(*) FROM data").fetchone()[0]
                    conn.close()
                    
                    metadata["db_path"] = str(db_path)
                    metadata["query_engine"] = "duckdb"
                    logger.info(f"DuckDB database created: {row_count} rows imported")
                    
                except Exception as db_error:
                    logger.warning(f"DuckDB conversion failed (falling back to Pandas): {str(db_error)}")
                    metadata["query_engine"] = "pandas"
                    # Ensure no incomplete duckdb file exists
                    if db_path.exists():
                        try:
                            db_path.unlink()
                            logger.info(f"Removed incomplete DuckDB file")
                        except Exception:
                            pass
                    
            except Exception as e:
                logger.warning(f"Failed to read CSV metadata: {str(e)}")
        
        elif file_type == "txt":
            # TXT-specific metadata
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                    metadata["line_count"] = len(lines)
                logger.info(f"TXT metadata: {len(lines)} lines")
            except Exception as e:
                logger.warning(f"Failed to read TXT metadata: {str(e)}")
        
        elif file_ext in settings.IMAGE_EXTENSIONS:
            # Image-specific metadata
            try:
                from PIL import Image
                import base64
                
                with Image.open(file_path) as img:
                    metadata.update({
                        "is_image": True,
                        "width": img.width,
                        "height": img.height,
                        "format": img.format,
                    })
                    logger.info(f"Image metadata: {img.width}x{img.height} {img.format}")
                
                # Generate base64 for Vision API
                with open(file_path, "rb") as img_file:
                    img_base64 = base64.b64encode(img_file.read()).decode('utf-8')
                    # Determine MIME type
                    mime_map = {
                        '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                        '.png': 'image/png', '.gif': 'image/gif', '.webp': 'image/webp'
                    }
                    mime_type = mime_map.get(file_ext, 'image/png')
                    metadata["image_base64_url"] = f"data:{mime_type};base64,{img_base64}"
                    
                    # Also provide a static URL for frontend display
                    metadata["image_url"] = f"/uploads/{file_id}{file_ext}"
                    
            except ImportError:
                logger.warning("PIL not installed, skipping image dimension extraction")
                metadata["is_image"] = True
                # Still generate base64 and URL even without PIL
                try:
                    import base64 as b64_module
                    with open(file_path, "rb") as img_file:
                        img_b64 = b64_module.b64encode(img_file.read()).decode('utf-8')
                        mime_map = {
                            '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                            '.png': 'image/png', '.gif': 'image/gif', '.webp': 'image/webp'
                        }
                        m_type = mime_map.get(file_ext, 'image/png')
                        metadata["image_base64_url"] = f"data:{m_type};base64,{img_b64}"
                        metadata["image_url"] = f"/uploads/{file_id}{file_ext}"
                except Exception as b64_err:
                    logger.warning(f"Failed to generate base64 for image: {b64_err}")
                    metadata["image_url"] = f"/uploads/{file_id}{file_ext}"
            except Exception as e:
                logger.warning(f"Failed to read image metadata: {str(e)}")
                metadata["is_image"] = True
                metadata["image_url"] = f"/uploads/{file_id}{file_ext}"
        
        # Upload to OpenAI File API for files at or under 50MB.
        MAX_OPENAI_UPLOAD_SIZE = 50 * 1024 * 1024  # 50MB
        if len(content) <= MAX_OPENAI_UPLOAD_SIZE:
            try:
                logger.info(f"Uploading to OpenAI File API...")
                with open(file_path, "rb") as f:
                    openai_file = self.openai_client.files.create(
                        file=f,
                        purpose="assistants"
                    )
                    metadata["openai_file_id"] = openai_file.id
                    logger.info(f"Uploaded to OpenAI File API: {openai_file.id}")
            except Exception as e:
                logger.error(f"Failed to upload to OpenAI: {str(e)}")
        else:
            file_size_mb = len(content) / (1024 * 1024)
            logger.info(f"Skipping OpenAI File API upload (file size: {file_size_mb:.1f}MB > 50MB limit)")
            logger.info(f"Using DuckDB for local analysis only")
        
        # Store metadata
        self.files_db[file_id] = metadata
        logger.info(f"File upload complete: {file_id}")
        
        return FileMetadata(**metadata)
    
    def get_file(self, file_id: str) -> Optional[dict]:
        """Get file metadata by ID"""
        return self.files_db.get(file_id)
    
    def list_files(self, client_id: Optional[str] = None) -> List[dict]:
        """
        List uploaded files, optionally filtered by client_id
        
        Args:
            client_id: Optional client ID to filter files
            
        Returns:
            List of file metadata
        """
        if client_id:
            return [f for f in self.files_db.values() if f.get('client_id') == client_id]
        return list(self.files_db.values())
    
    def delete_file(self, file_id: str) -> bool:
        """
        Delete a file from storage
        
        Args:
            file_id: File ID to delete
            
        Returns:
            bool: True if deleted, False if not found
        """
        if file_id not in self.files_db:
            return False
        
        # Get file path
        file_metadata = self.files_db[file_id]
        file_path = Path(file_metadata["file_path"])
        
        # Delete from disk
        if file_path.exists():
            file_path.unlink()
        
        # Delete from OpenAI if exists
        if file_metadata.get("openai_file_id"):
            try:
                self.openai_client.files.delete(file_metadata["openai_file_id"])
                logger.debug(f"Deleted from OpenAI: {file_metadata['openai_file_id']}")
            except Exception as e:
                logger.warning(f"Failed to delete from OpenAI: {str(e)}")
        
        # Delete from database
        del self.files_db[file_id]
        
        return True
    
    def preview_file(self, file_id: str, rows: int = 5) -> dict:
        """
        Preview file contents
        
        Args:
            file_id: File ID
            rows: Number of rows to preview (for CSV/TXT)
            
        Returns:
            dict: Preview data
        """
        if file_id not in self.files_db:
            raise HTTPException(status_code=404, detail="File not found")
        
        file_metadata = self.files_db[file_id]
        file_path = Path(file_metadata["file_path"])
        file_type = file_metadata["file_type"]
        
        logger.info(f"Previewing {file_type} file: {file_path}")
        
        if file_type == "csv":
            try:
                # CSV preview
                import numpy as np
                import json
                
                # Read only first N rows
                logger.info(f"Reading CSV with {rows} rows limit...")
                
                # Try UTF-8 first, fallback to latin1 for encoding issues
                try:
                    df = pd.read_csv(file_path, nrows=rows, encoding='utf-8')
                except UnicodeDecodeError:
                    logger.warning("UTF-8 decoding failed, retrying with latin1 encoding...")
                    df = pd.read_csv(file_path, nrows=rows, encoding='latin1')
                
                logger.info(f"CSV loaded: {len(df)} rows, {len(df.columns)} columns")
                
                # No column limit - show all columns
                columns_truncated = False
                
                # AGGRESSIVE NaN/inf cleaning for JSON serialization
                logger.info("Cleaning NaN/inf values...")
                
                # Step 1: Replace inf/-inf with None
                df = df.replace([np.inf, -np.inf], None)
                
                # Step 2: Replace NaN with None (using where instead of fillna)
                df = df.where(pd.notnull(df), None)
                
                # Step 3: Convert to records
                logger.info("Converting to dict records...")
                data_records = df.to_dict('records')
                
                # Step 4: Python-level NaN cleaning (final safety net)
                # This catches any NaN that survived the pandas operations
                import math
                def clean_value(v):
                    """Clean a single value - convert NaN/inf to None"""
                    if isinstance(v, float):
                        if math.isnan(v) or math.isinf(v):
                            return None
                    return v
                
                # Clean all values in all records
                data_records = [
                    {k: clean_value(v) for k, v in row.items()}
                    for row in data_records
                ]
                logger.info("Python-level NaN cleaning complete")
                
                # Step 5: Clean column names (in case they have NaN)
                clean_columns = [str(col) if not pd.isna(col) else f"col_{i}" for i, col in enumerate(df.columns)]
                
                result = {
                    "columns": clean_columns,
                    "data": data_records,
                    "total_rows": file_metadata.get("rows", 0),
                    "total_columns": len(df.columns)
                }
                
                logger.info(f"Preview prepared: {len(result['data'])} rows, {len(result['columns'])} cols")
                return result
                
            except Exception as e:
                logger.error(f"CSV preview failed: {str(e)}", exc_info=True)
                raise HTTPException(status_code=500, detail=f"CSV preview failed: {str(e)}")
        
        elif file_type == "txt":
            # TXT preview
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = [f.readline().rstrip('\n') for _ in range(rows)]
            
            return {
                "lines": lines,
                "total_lines": file_metadata.get("line_count", 0)
            }
        
        else:
            raise HTTPException(status_code=400, detail=f"Preview not supported for {file_type}")
