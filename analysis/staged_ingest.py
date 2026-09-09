"""Validate user-owned staged datasets and atomically promote them into L3."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from server.identity import Identity, Role
from store.base import RegistryStore

_UPLOAD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SAMPLE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_MANIFEST_BYTES = 2 * 1024 * 1024
_MAX_FILES = 100_000

# Each tuple contains groups of acceptable basenames. Every group must match at
# least one declared file. These are already-processed outputs; raw FASTQ and
# NDPI are deliberately absent from the allowlist.
_SUPPORTED_LAYOUTS: dict[tuple[str, str], tuple[frozenset[str], ...]] = {
    ("bulk_rnaseq", "kallisto"): (frozenset({"abundance.tsv", "abundance.h5"}),),
    ("bulk_rnaseq", "salmon"): (frozenset({"quant.sf"}),),
    ("scrna", "cellranger"): (frozenset({"filtered_feature_bc_matrix.h5"}),),
    ("visium", "10x_visium"): (
        frozenset({"filtered_feature_bc_matrix.h5", "matrix.mtx.gz"}),
        frozenset({"tissue_positions.csv", "tissue_positions_list.csv", "tissue_positions.parquet"}),
    ),
    ("visium_hd", "10x_visium_hd"): (
        frozenset({"filtered_feature_bc_matrix.h5", "matrix.mtx.gz"}),
        frozenset({"tissue_positions.csv", "tissue_positions_list.csv", "tissue_positions.parquet"}),
    ),
    ("proteomics", "maxquant"): (frozenset({"proteinGroups.txt"}),),
}


class StagedIngestError(RuntimeError):
    pass


@dataclass(frozen=True)
class ValidatedUpload:
    source_dir: Path
    target_dir: Path
    manifest: dict
    file_count: int
    total_bytes: int


def actor_storage_id(user_id: str) -> str:
    """Map an authenticated identity to a stable, path-safe staging directory."""
    normalized = re.sub(r"[^a-z0-9._-]+", "-", user_id.lower()).strip(".-_")
    label = normalized[:40] or "user"
    suffix = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:10]
    return f"{label}-{suffix}"


def _safe_upload_dir(staging_root: Path, actor: Identity, upload_id: str) -> Path:
    if not _UPLOAD_ID_RE.fullmatch(upload_id):
        raise StagedIngestError("upload_id must be a safe relative directory name")
    root = Path(staging_root).resolve()
    actor_root = (root / actor_storage_id(actor.user_id)).resolve()
    source = (actor_root / upload_id).resolve()
    if not source.is_relative_to(actor_root):
        raise StagedIngestError("upload path must remain relative to the actor staging root")
    if not source.is_dir():
        raise StagedIngestError("staged upload not found for authenticated user")
    return source


def _manifest_file(source: Path) -> dict:
    path = source / "MANIFEST.json"
    if path.is_symlink():
        raise StagedIngestError("MANIFEST.json must not be a symlink")
    if not path.is_file():
        raise StagedIngestError("MANIFEST.json not found")
    if path.stat().st_size > _MAX_MANIFEST_BYTES:
        raise StagedIngestError("MANIFEST.json exceeds 2 MiB")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StagedIngestError(f"invalid MANIFEST.json: {exc}") from exc
    if not isinstance(value, dict):
        raise StagedIngestError("MANIFEST.json root must be an object")
    return value


def _validated_relative_path(raw: object) -> PurePosixPath:
    if not isinstance(raw, str) or not raw or "\\" in raw:
        raise StagedIngestError("manifest file path must be a POSIX relative path")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise StagedIngestError("manifest file path must be a safe relative path")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_manifest_shape(manifest: dict) -> tuple[str, str, str, list[dict]]:
    if manifest.get("manifest_version") != 1:
        raise StagedIngestError("unsupported manifest_version; expected 1")
    sample_id = manifest.get("sample_id")
    if not isinstance(sample_id, str) or not _SAMPLE_ID_RE.fullmatch(sample_id):
        raise StagedIngestError("sample_id must use lowercase letters, numbers, _ or -")
    data_type = manifest.get("data_type")
    platform = manifest.get("platform")
    if (data_type, platform) not in _SUPPORTED_LAYOUTS:
        raise StagedIngestError(
            f"unsupported analysis-ready data_type/platform: {data_type!r}/{platform!r}"
        )
    files = manifest.get("files")
    if not isinstance(files, list) or not files or len(files) > _MAX_FILES:
        raise StagedIngestError(f"files must contain 1..{_MAX_FILES} entries")
    return sample_id, data_type, platform, files


def validate_staged_upload(
    upload_id: str,
    *,
    actor: Identity,
    store: RegistryStore,
    staging_root: Path,
    ingested_root: Path,
) -> ValidatedUpload:
    if not actor.permits(Role.ANALYST):
        raise StagedIngestError("analyst role required")
    source = _safe_upload_dir(Path(staging_root), actor, upload_id)

    for path in source.rglob("*"):
        if path.is_symlink():
            raise StagedIngestError(f"symlink is not allowed: {path.relative_to(source)}")
        if path.name.startswith("._"):
            raise StagedIngestError(f"AppleDouble file is not allowed: {path.name}")

    manifest = _manifest_file(source)
    sample_id, data_type, platform, entries = _validate_manifest_shape(manifest)
    if store.get_sample(sample_id) is not None:
        raise StagedIngestError(f"sample {sample_id!r} already exists")

    target_root = Path(ingested_root).resolve()
    target = target_root / sample_id
    if target.exists():
        raise StagedIngestError(f"target sample directory {sample_id!r} already exists")

    declared: set[str] = set()
    total_bytes = 0
    basenames: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise StagedIngestError("each files entry must be an object")
        relative = _validated_relative_path(entry.get("path"))
        relative_text = relative.as_posix()
        if relative_text in declared:
            raise StagedIngestError(f"duplicate file declaration: {relative_text}")
        declared.add(relative_text)
        checksum = entry.get("sha256")
        if not isinstance(checksum, str) or not _SHA256_RE.fullmatch(checksum):
            raise StagedIngestError(f"invalid sha256 checksum for {relative_text}")

        candidate = source.joinpath(*relative.parts)
        if candidate.is_symlink() or not candidate.is_file():
            raise StagedIngestError(f"declared file not found: {relative_text}")
        resolved = candidate.resolve()
        if not resolved.is_relative_to(source):
            raise StagedIngestError(f"declared file escapes staging root: {relative_text}")
        actual_checksum = _sha256(candidate)
        if actual_checksum != checksum:
            raise StagedIngestError(f"checksum mismatch for {relative_text}")
        size = candidate.stat().st_size
        if "size_bytes" in entry and entry["size_bytes"] != size:
            raise StagedIngestError(f"size mismatch for {relative_text}")
        total_bytes += size
        basenames.add(candidate.name)

    actual = {
        path.relative_to(source).as_posix()
        for path in source.rglob("*")
        if path.is_file() and path.name != "MANIFEST.json"
    }
    unlisted = actual - declared
    missing = declared - actual
    if unlisted:
        raise StagedIngestError(f"unlisted staged files: {sorted(unlisted)!r}")
    if missing:
        raise StagedIngestError(f"declared files missing: {sorted(missing)!r}")

    for alternatives in _SUPPORTED_LAYOUTS[(data_type, platform)]:
        if basenames.isdisjoint(alternatives):
            expected = ", ".join(sorted(alternatives))
            raise StagedIngestError(f"required analysis-ready file missing; expected one of {expected}")

    return ValidatedUpload(source, target, manifest, len(declared), total_bytes)


def promote_staged_upload(
    upload_id: str,
    *,
    actor: Identity,
    store: RegistryStore,
    staging_root: Path,
    ingested_root: Path,
) -> dict:
    validated = validate_staged_upload(
        upload_id,
        actor=actor,
        store=store,
        staging_root=staging_root,
        ingested_root=ingested_root,
    )
    target_root = validated.target_dir.parent
    target_root.mkdir(parents=True, exist_ok=True)
    if os.stat(validated.source_dir).st_dev != os.stat(target_root).st_dev:
        raise StagedIngestError("staging and L3 roots must be on the same filesystem")

    manifest = validated.manifest
    validated.source_dir.rename(validated.target_dir)
    try:
        notes = str(manifest.get("notes", "")).strip()
        provenance = f"promoted from staged upload {upload_id}"
        notes = f"{notes}; {provenance}" if notes else provenance
        created = store.register_sample(
            manifest["sample_id"],
            str(manifest.get("project", "")),
            manifest["data_type"],
            manifest["platform"],
            str(manifest.get("species", "human")),
            str(manifest.get("tissue", "")),
            str(validated.target_dir),
            actor.user_id,
            notes,
        )
        if created is False:
            raise RuntimeError("sample was registered concurrently")
    except Exception as exc:
        try:
            validated.target_dir.rename(validated.source_dir)
        except Exception as rollback_exc:  # pragma: no cover - requires filesystem failure
            raise StagedIngestError(
                f"registration failed ({exc}); rollback also failed ({rollback_exc})"
            ) from exc
        raise StagedIngestError(f"registration failed; data rolled back: {exc}") from exc

    return {
        "sample_id": manifest["sample_id"],
        "l3_path": str(validated.target_dir),
        "file_count": validated.file_count,
        "total_bytes": validated.total_bytes,
        "added_by": actor.user_id,
    }
