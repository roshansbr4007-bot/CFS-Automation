"""Minimal, safe checks for task attachments.

Phase 3 reuses the approved evidence limits (decision L12) as the conservative baseline:
PDF, PNG, JPG, XLSX or EML, at most 10 MB. The type is confirmed from the file's own bytes;
the browser-supplied content type is never trusted. Files are stored under MEDIA_ROOT and are
only ever served through a permission-checked API view (MEDIA_URL is not routed).
"""

import email
import hashlib
import io
import zipfile
from dataclasses import dataclass
from pathlib import PurePath

from apps.core.errors import FieldValidationError

MAX_BYTES = 10 * 1024 * 1024
ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".xlsx", ".eml"}


@dataclass(frozen=True)
class CheckedUpload:
    original_filename: str
    size_bytes: int
    sha256: str


def _reject(message: str):
    raise FieldValidationError(fields={"file": [message]})


def _signature_ok(extension: str, head: bytes, data_reader) -> bool:
    if extension == ".pdf":
        return head.startswith(b"%PDF-")
    if extension == ".png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if extension in (".jpg", ".jpeg"):
        return head.startswith(b"\xff\xd8\xff")
    if extension == ".xlsx":
        try:
            with zipfile.ZipFile(io.BytesIO(data_reader())) as archive:
                return "xl/workbook.xml" in archive.namelist()
        except zipfile.BadZipFile:
            return False
    # .eml: must parse as an RFC 5322 message with at least one header
    message = email.message_from_bytes(data_reader())
    return len(message.keys()) > 0


def inspect_upload(upload) -> CheckedUpload:
    name = PurePath(upload.name or "").name
    extension = PurePath(name).suffix.lower()
    if not name or extension not in ALLOWED_EXTENSIONS:
        _reject("Allowed file types: PDF, PNG, JPG, XLSX, EML.")
    if upload.size is None or upload.size > MAX_BYTES:
        _reject("Files must be 10 MB or smaller.")
    if upload.size == 0:
        _reject("The file is empty.")

    digest = hashlib.sha256()
    for chunk in upload.chunks():
        digest.update(chunk)
    upload.seek(0)
    head = upload.read(16)
    upload.seek(0)

    def read_all() -> bytes:
        data = upload.read()
        upload.seek(0)
        return data

    if not _signature_ok(extension, head, read_all):
        _reject("The file content does not match its type.")
    return CheckedUpload(
        original_filename=name[:255], size_bytes=upload.size, sha256=digest.hexdigest()
    )
