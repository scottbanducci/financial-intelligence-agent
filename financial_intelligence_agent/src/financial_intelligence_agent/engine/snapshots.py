"""Local publication-manifest primitives for verified, immutable serving artifacts.

Snapshots are write-once. A manifest pins checksums; a pointer is published via
atomic os.replace. This does not implement Databricks Gold export/transactional
publication; it defines verifiable inputs for a future serving adapter.
"""
from __future__ import annotations
import hashlib
import json
import os
import tempfile
from datetime import datetime,timezone
from pathlib import Path
from typing import Mapping

SCHEMA_VERSION="snapshot-manifest/v1"

def checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def _within(root: Path, relative: str) -> Path:
    p=Path(relative)
    if p.is_absolute() or not p.parts or any(x in {"..","."} for x in p.parts):
        raise ValueError("Only safe relative paths accepted")
    result=(root / p).resolve()
    if not result.is_relative_to(root.resolve()):
        raise ValueError("Path escapes snapshot root")
    return result

def manifest(root: str | Path, files: Mapping[str,str], *,
             captured_at: datetime, source_run_id: str) -> dict:
    """Create independent checksums; no invented ingest/publication timestamp."""
    root=Path(root)
    if not source_run_id or len(source_run_id)>128:
        raise ValueError("Valid source-run identifier required")
    if captured_at.tzinfo is None:
        raise ValueError("Captured timestamp must include timezone")
    if not files:
        raise ValueError("At least one source file")
    assets={}
    for name,relative in sorted(files.items()):
        if not name or len(name)>64:
            raise ValueError("Invalid logical asset name")
        resolved=_within(root,relative)
        raw=resolved.read_bytes()
        assets[name]={"path":relative,"sha256":checksum(raw),"size_bytes":len(raw)}
    body={
        "schema_version":SCHEMA_VERSION,
        "captured_at":captured_at.astimezone(timezone.utc).isoformat(),
        "source_run_id":source_run_id,
        "assets":assets,
        "limitations":"Content checksum, not proof of historical release/vintage truth",
    }
    body["manifest_sha256"]=checksum(json.dumps(body,sort_keys=True,separators=(",",":")).encode())
    return body

def verify(root: str | Path, data: Mapping) -> bool:
    if data.get("schema_version")!=SCHEMA_VERSION:
        raise ValueError("Unknown manifest schema version")
    unsigned={k:v for k,v in data.items() if k!="manifest_sha256"}
    expected=checksum(json.dumps(unsigned,sort_keys=True,separators=(",",":")).encode())
    if data.get("manifest_sha256")!=expected:
        raise ValueError("Manifest integrity mismatch")
    for item in data.get("assets",{}).values():
        raw=_within(Path(root),item["path"]).read_bytes()
        if checksum(raw)!=item["sha256"] or len(raw)!=item["size_bytes"]:
            raise ValueError("Snapshot content integrity mismatch")
    return True

def publish_pointer(directory: str | Path, data: Mapping) -> Path:
    """Write verified manifest as an atomic pointer, never partial JSON."""
    path=Path(directory)
    path.mkdir(parents=True,exist_ok=True)
    verify(path,data)  # refuse to publish a corrupt/missing asset pointer
    target=path/"LATEST.json"
    blob=json.dumps(dict(data),sort_keys=True,indent=2)
    fd,tmp=tempfile.mkstemp(prefix=".manifest-",dir=path)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as stream:
            stream.write(blob)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp,target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return target
