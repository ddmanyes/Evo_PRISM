"""Secure, deterministic result delivery for remote MCP clients.

The analysis registry stores server-local paths.  Those paths are useful to the
server, but they are not attachments and are normally meaningless on a remote
client.  This module resolves registered artifacts inside ``BIO_DB_ROOT`` and
optionally builds a deterministic ZIP that can be exposed as a ``delivery://``
MCP resource.

Only registered artifact IDs, analysis IDs, and validated figure-cache entries
are accepted.  Callers cannot supply arbitrary filesystem paths.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import shutil
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from config.settings import (
    BIO_DB_ROOT,
    DELIVERY_BUNDLE_ROOT,
    DELIVERY_MAX_ITEMS,
    DELIVERY_MAX_TOTAL_MB,
    resolve_artifact_path,
)

DELIVERY_URI_SCHEME = "delivery://"

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_BUNDLE_ID_RE = re.compile(r"^[0-9a-f]{64}$")


class ResultDeliveryError(ValueError):
    """A selector, registered path, or bundle violates the delivery contract."""


@dataclass(frozen=True)
class DeliveryArtifact:
    artifact_id: str
    analysis_id: str
    artifact_type: str
    artifact_subtype: str | None
    label: str
    path: Path
    mime_type: str
    size_bytes: int
    sha256: str

    @property
    def is_image(self) -> bool:
        return self.mime_type.startswith("image/")

    @property
    def resource_uri(self) -> str:
        return f"artifact://{self.artifact_id}"


@dataclass(frozen=True)
class DeliveryFigure:
    figure_id: str
    filename: str
    mime_type: str
    data: bytes


@dataclass(frozen=True)
class DeliveryBundle:
    bundle_id: str
    path: Path
    size_bytes: int
    sha256: str
    manifest: dict[str, Any]

    @property
    def resource_uri(self) -> str:
        return f"{DELIVERY_URI_SCHEME}{self.bundle_id}"


def _validate_uuid(value: str, field: str) -> str:
    normalized = str(value or "").strip()
    if not _UUID_RE.fullmatch(normalized):
        raise ResultDeliveryError(f"{field} 格式錯誤：{value!r}")
    return normalized.lower()


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_registered_file(file_path: str) -> Path:
    if not file_path:
        raise ResultDeliveryError("artifact 沒有 file_path，無法交付")

    raw_path = resolve_artifact_path(file_path)
    if raw_path.is_symlink():
        raise ResultDeliveryError(f"拒絕交付符號連結：{file_path!r}")

    resolved = raw_path.resolve()
    root = BIO_DB_ROOT.resolve()
    if root not in resolved.parents:
        raise ResultDeliveryError(f"artifact 路徑越界，拒絕交付：{file_path!r}")
    if not resolved.is_file():
        raise ResultDeliveryError(f"artifact 檔案不存在或不是一般檔案：{file_path!r}")
    return resolved


def _row_to_artifact(row: Sequence[Any]) -> DeliveryArtifact:
    (
        artifact_id,
        analysis_id,
        artifact_type,
        artifact_subtype,
        label,
        file_path,
        _size_kb,
        mime_type,
    ) = row
    path = _resolve_registered_file(str(file_path or ""))
    resolved_mime = str(
        mime_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    )
    return DeliveryArtifact(
        artifact_id=str(artifact_id),
        analysis_id=str(analysis_id),
        artifact_type=str(artifact_type or "artifact"),
        artifact_subtype=str(artifact_subtype) if artifact_subtype else None,
        label=str(label or path.name),
        path=path,
        mime_type=resolved_mime,
        size_bytes=path.stat().st_size,
        sha256=_sha256_path(path),
    )


_ARTIFACT_SELECT = """
    SELECT artifact_id::VARCHAR, analysis_id::VARCHAR, artifact_type,
           artifact_subtype, label, file_path, file_size_kb, mime_type
    FROM analysis_artifacts
"""


def select_delivery_artifacts(
    con,
    *,
    analysis_id: str | None = None,
    artifact_ids: Iterable[str] | None = None,
    max_items: int = DELIVERY_MAX_ITEMS,
) -> list[DeliveryArtifact]:
    """Resolve a bounded, de-duplicated artifact selection from the registry."""
    normalized_ids: list[str] = []
    seen_requested: set[str] = set()
    for raw_id in artifact_ids or []:
        artifact_id = _validate_uuid(raw_id, "artifact_id")
        if artifact_id not in seen_requested:
            seen_requested.add(artifact_id)
            normalized_ids.append(artifact_id)

    normalized_analysis_id = None
    if analysis_id:
        normalized_analysis_id = _validate_uuid(analysis_id, "analysis_id")

    if not normalized_analysis_id and not normalized_ids:
        return []
    if len(normalized_ids) > max_items:
        raise ResultDeliveryError(f"artifact_ids 超過上限 {max_items} 筆")

    rows: list[Sequence[Any]] = []
    if normalized_analysis_id:
        rows.extend(
            con.execute(
                _ARTIFACT_SELECT
                + " WHERE analysis_id = ? ORDER BY created_at, artifact_id LIMIT ?",
                [normalized_analysis_id, max_items + 1],
            ).fetchall()
        )
        if len(rows) > max_items:
            raise ResultDeliveryError(
                f"analysis_id={normalized_analysis_id!r} 的 artifact 超過上限 {max_items} 筆，"
                "請改用 artifact_ids 明確選取"
            )

    for artifact_id in normalized_ids:
        row = con.execute(
            _ARTIFACT_SELECT + " WHERE artifact_id = ?",
            [artifact_id],
        ).fetchone()
        if row is None:
            raise ResultDeliveryError(f"artifact_id={artifact_id!r} 不存在")
        rows.append(row)

    artifacts: list[DeliveryArtifact] = []
    seen: set[str] = set()
    for row in rows:
        artifact_id = str(row[0])
        if artifact_id in seen:
            continue
        seen.add(artifact_id)
        artifacts.append(_row_to_artifact(row))

    if len(artifacts) > max_items:
        raise ResultDeliveryError(f"去重後 artifact 仍超過上限 {max_items} 筆")
    return artifacts


def _safe_filename(filename: str, fallback: str) -> str:
    name = Path(str(filename or "")).name.strip()
    if not name or name in {".", ".."}:
        name = fallback
    name = re.sub(r"[\\/\x00-\x1f]", "_", name)
    return name[:180]


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    return info


def _bundle_manifest(
    artifacts: Sequence[DeliveryArtifact], figures: Sequence[DeliveryFigure]
) -> tuple[dict[str, Any], list[tuple[str, Path | bytes]]]:
    entries: list[dict[str, Any]] = []
    sources: list[tuple[str, Path | bytes]] = []

    for item in sorted(artifacts, key=lambda it: (it.analysis_id, it.artifact_id)):
        filename = _safe_filename(item.path.name, f"artifact-{item.artifact_id}")
        archive_path = f"artifacts/{item.artifact_id[:8]}_{filename}"
        entries.append(
            {
                "kind": "artifact",
                "id": item.artifact_id,
                "analysis_id": item.analysis_id,
                "label": item.label,
                "filename": filename,
                "archive_path": archive_path,
                "mime_type": item.mime_type,
                "size_bytes": item.size_bytes,
                "sha256": item.sha256,
            }
        )
        sources.append((archive_path, item.path))

    for item in sorted(figures, key=lambda it: it.figure_id):
        filename = _safe_filename(item.filename, f"figure-{item.figure_id}")
        archive_path = f"figures/{item.figure_id}_{filename}"
        checksum = hashlib.sha256(item.data).hexdigest()
        entries.append(
            {
                "kind": "figure",
                "id": item.figure_id,
                "analysis_id": None,
                "label": filename,
                "filename": filename,
                "archive_path": archive_path,
                "mime_type": item.mime_type,
                "size_bytes": len(item.data),
                "sha256": checksum,
            }
        )
        sources.append((archive_path, item.data))

    return {"schema_version": 1, "entries": entries}, sources


def create_delivery_bundle(
    artifacts: Sequence[DeliveryArtifact],
    figures: Sequence[DeliveryFigure] = (),
    *,
    max_items: int = DELIVERY_MAX_ITEMS,
    max_total_mb: float = DELIVERY_MAX_TOTAL_MB,
) -> DeliveryBundle:
    """Create an idempotent ZIP plus manifest for explicitly selected results."""
    if not artifacts and not figures:
        raise ResultDeliveryError("沒有可打包的圖片或數據")
    if len(artifacts) + len(figures) > max_items:
        raise ResultDeliveryError(f"打包項目超過上限 {max_items} 筆")

    manifest, sources = _bundle_manifest(artifacts, figures)
    total_bytes = sum(int(entry["size_bytes"]) for entry in manifest["entries"])
    max_bytes = int(max_total_mb * 1_048_576)
    if total_bytes > max_bytes:
        raise ResultDeliveryError(
            f"打包來源共 {total_bytes / 1_048_576:.1f} MB，超過上限 {max_total_mb:g} MB"
        )

    canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    bundle_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    bundle_root = DELIVERY_BUNDLE_ROOT.resolve()
    bundle_root.mkdir(parents=True, exist_ok=True)
    output_path = bundle_root / f"{bundle_id}.zip"

    if not output_path.exists():
        temp_path = bundle_root / f".{bundle_id}.{uuid.uuid4().hex}.tmp"
        try:
            with zipfile.ZipFile(temp_path, "w") as archive:
                archive.writestr(
                    _zip_info("manifest.json"),
                    json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode(
                        "utf-8"
                    ),
                )
                for archive_path, source in sources:
                    info = _zip_info(archive_path)
                    if isinstance(source, Path):
                        with source.open("rb") as src, archive.open(info, "w") as dst:
                            shutil.copyfileobj(src, dst, length=1024 * 1024)
                    else:
                        archive.writestr(info, source)
            temp_path.replace(output_path)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    if output_path.is_symlink() or not output_path.is_file():
        raise ResultDeliveryError("delivery bundle 不是安全的一般檔案")
    return DeliveryBundle(
        bundle_id=bundle_id,
        path=output_path,
        size_bytes=output_path.stat().st_size,
        sha256=_sha256_path(output_path),
        manifest=manifest,
    )


def parse_delivery_uri(uri: str) -> str:
    value = str(uri or "").strip()
    if not value.startswith(DELIVERY_URI_SCHEME):
        raise ResultDeliveryError(f"非 delivery URI：{uri!r}")
    bundle_id = value[len(DELIVERY_URI_SCHEME) :].strip("/")
    if not _BUNDLE_ID_RE.fullmatch(bundle_id):
        raise ResultDeliveryError(f"delivery bundle id 格式錯誤：{bundle_id!r}")
    return bundle_id


def read_delivery_resource(uri: str) -> tuple[bytes, str, Path]:
    """Read a previously generated bundle after re-validating its sandbox path."""
    bundle_id = parse_delivery_uri(uri)
    root = DELIVERY_BUNDLE_ROOT.resolve()
    path = root / f"{bundle_id}.zip"
    if path.is_symlink():
        raise ResultDeliveryError("拒絕讀取符號連結 delivery bundle")
    resolved = path.resolve()
    if root not in resolved.parents or not resolved.is_file():
        raise ResultDeliveryError(f"delivery bundle={bundle_id!r} 不存在")
    max_bytes = int(DELIVERY_MAX_TOTAL_MB * 1_048_576)
    if resolved.stat().st_size > max_bytes:
        raise ResultDeliveryError(f"delivery bundle 超過 {DELIVERY_MAX_TOTAL_MB:g} MB 讀取上限")
    return resolved.read_bytes(), "application/zip", resolved
