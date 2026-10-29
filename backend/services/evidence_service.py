def get_evidence_upload_folder():
    import sys
    g_app = sys.modules.get("grievai_app")
    if g_app and hasattr(g_app, "EVIDENCE_UPLOAD_FOLDER"):
        return g_app.EVIDENCE_UPLOAD_FOLDER
    import backend.config.settings as s
    return getattr(s, "EVIDENCE_UPLOAD_FOLDER", os.path.join(s.BASE_DIR, "uploads", "evidence"))

import os
import io
import time
import uuid
import requests
from datetime import datetime
from werkzeug.utils import secure_filename
from backend.config.settings import (
    EVIDENCE_UPLOAD_FOLDER,
    STORAGE_BACKEND,
    SUPABASE_URL,
    SUPABASE_SERVICE_ROLE_KEY,
    SUPABASE_STORAGE_BUCKET,
    logger
)
from backend.models.complaint import ComplaintEvidence

ALLOWED_EVIDENCE_EXTENSIONS = {
    ".jpg": ("photo", "image/jpeg"),
    ".jpeg": ("photo", "image/jpeg"),
    ".png": ("photo", "image/png"),
    ".webp": ("photo", "image/webp"),
    ".mp3": ("audio", "audio/mpeg"),
    ".wav": ("audio", "audio/wav"),
    ".ogg": ("audio", "audio/ogg"),
    ".m4a": ("audio", "audio/mp4"),
    ".webm": ("audio", "audio/webm"),
    ".mp4": ("video", "video/mp4"),
    ".pdf": ("document", "application/pdf"),
}

# Alias for backwards compatibility with tests
ALLOWED_EXTENSIONS = set(ALLOWED_EVIDENCE_EXTENSIONS.keys())


def format_file_size(num_bytes):
    if not num_bytes:
        return "0 B"
    size = float(num_bytes)
    for unit in ["B", "KB", "MB", "GB"]:
        if abs(size) < 1024.0:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024.0
    return f"{size:.1f} TB"


def inspect_file_signature(ext, header, is_voice=False):
    """
    Verifies magic-byte header against claimed extension.
    Returns (category, mime_type) if verified, or (None, None).
    """
    ext = ext.lower().strip()
    if ext in [".jpg", ".jpeg"]:
        if header.startswith(b"\xff\xd8\xff"):
            return "photo", "image/jpeg"
    elif ext == ".png":
        if header.startswith(b"\x89PNG\r\n\x1a\n"):
            return "photo", "image/png"
    elif ext == ".webp":
        if len(header) >= 12 and header.startswith(b"RIFF") and header[8:12] == b"WEBP":
            return "photo", "image/webp"
    elif ext == ".pdf":
        if header.startswith(b"%PDF"):
            return "document", "application/pdf"
    elif ext == ".webm":
        if header.startswith(b"\x1a\x45\xdf\xa3"):
            cat = "audio" if is_voice else "video"
            mime = "audio/webm" if is_voice else "video/webm"
            return cat, mime
    elif ext == ".mp3":
        if header.startswith(b"ID3") or header.startswith(b"\xff\xfb") or header.startswith(b"\xff\xf3") or header.startswith(b"\xff\xf2"):
            return "audio", "audio/mpeg"
    elif ext == ".wav":
        if len(header) >= 12 and header.startswith(b"RIFF") and header[8:12] == b"WAVE":
            return "audio", "audio/wav"
    elif ext == ".ogg":
        if header.startswith(b"OggS"):
            return "audio", "audio/ogg"
    elif ext in [".mp4", ".m4a"]:
        if len(header) >= 8 and (header[4:8] == b"ftyp" or header[4:8] == b"moov" or header[0:4] in (b"\x00\x00\x00\x18", b"\x00\x00\x00\x20")):
            cat = "audio" if ext == ".m4a" else "video"
            mime = "audio/mp4" if ext == ".m4a" else "video/mp4"
            return cat, mime
    return None, None


def upload_to_supabase_storage(file_bytes: bytes, storage_filename: str, mime_type: str) -> bool:
    """
    Uploads file content to a private Supabase Storage bucket via the backend REST API.
    Never exposes public URLs or service-role keys.
    """
    if not (SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY):
        return False
    try:
        url = f"{SUPABASE_URL.rstrip('/')}/storage/v1/object/{SUPABASE_STORAGE_BUCKET}/{storage_filename}"
        headers = {
            "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
            "Content-Type": mime_type,
            "x-upsert": "true"
        }
        resp = requests.post(url, headers=headers, data=file_bytes, timeout=15)
        if resp.status_code in (200, 201):
            logger.info("Uploaded evidence %s to Supabase Storage bucket '%s'.", storage_filename, SUPABASE_STORAGE_BUCKET)
            return True
        else:
            logger.error("Supabase Storage upload failed (status %s): %s", resp.status_code, resp.text)
            return False
    except Exception as e:
        logger.error("Supabase Storage upload exception: %s", str(e))
        return False


def fetch_evidence_file_stream(storage_filename: str):
    """
    Retrieves evidence file content for streaming.
    Checks local filesystem first, then falls back to Supabase Storage if configured.
    Returns (file_like_object, bool_is_memory) or (None, False) if not found.
    """
    safe_name = os.path.basename(storage_filename)
    local_path = os.path.join(get_evidence_upload_folder(), safe_name)

    if os.path.exists(local_path):
        return open(local_path, "rb"), False

    # Fallback to Supabase Storage
    if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
        try:
            url = f"{SUPABASE_URL.rstrip('/')}/storage/v1/object/authenticated/{SUPABASE_STORAGE_BUCKET}/{safe_name}"
            headers = {"Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}"}
            resp = requests.get(url, headers=headers, timeout=15, stream=True)
            if resp.status_code == 200:
                return io.BytesIO(resp.content), True
            else:
                logger.warning("Failed to fetch from Supabase Storage (status %s) for %s", resp.status_code, safe_name)
        except Exception as e:
            logger.error("Error fetching evidence from Supabase Storage: %s", str(e))

    return None, False


def save_uploaded_evidence(file_obj, complaint_id, is_voice=False):
    """
    Validates, randomizes filename, and securely saves an uploaded evidence file.
    Supports both local filesystem and Supabase Storage backend.
    Returns (ComplaintEvidence, saved_file_path) on success, or raises ValueError.
    """
    if not file_obj or not file_obj.filename:
        return None, None

    orig_name = secure_filename(file_obj.filename)
    if not orig_name:
        orig_name = f"voice_statement_{int(time.time())}.webm" if is_voice else f"evidence_{int(time.time())}.bin"

    ext = os.path.splitext(orig_name)[1].lower()
    if not ext and is_voice:
        ext = ".webm"
        orig_name += ext

    if ext not in ALLOWED_EVIDENCE_EXTENSIONS:
        raise ValueError(f"File extension '{ext}' is not permitted for evidence attachments.")

    header = file_obj.read(512)
    file_obj.seek(0)

    category, mime_type = inspect_file_signature(ext, header, is_voice=is_voice)
    if not category:
        raise ValueError(f"File '{orig_name}' does not match its expected file signature or format.")

    storage_name = f"{uuid.uuid4().hex}{ext}"
    dest_path = os.path.join(get_evidence_upload_folder(), storage_name)

    # Save to local filesystem
    file_obj.save(dest_path)
    file_size = os.path.getsize(dest_path)

    # If Supabase Storage is enabled, upload copy to private bucket
    if STORAGE_BACKEND == "supabase" and SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
        with open(dest_path, "rb") as f:
            file_bytes = f.read()
        upload_to_supabase_storage(file_bytes, storage_name, mime_type)

    evidence_record = ComplaintEvidence(
        complaint_id=complaint_id,
        file_type=category,
        original_filename=orig_name,
        storage_filename=storage_name,
        mime_type=mime_type,
        file_size_bytes=file_size,
        uploaded_at=datetime.utcnow()
    )
    return evidence_record, dest_path
