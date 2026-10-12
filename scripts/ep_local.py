"""Single-computer setup and read-only diagnostics. No service or model is started."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from urllib.parse import urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def local_env(data: Path, embedding_url: str = "http://127.0.0.1:8081/v1") -> dict[str, str]:
    """Pin one independent local deployment; never inherit .env or owner credentials."""
    return {
        "PYTHON_DOTENV_DISABLED": "1",
        "PYTHONPATH": str(ROOT),
        "BIO_DB_ROOT": str(data),
        "DUCKDB_PATH": str(data / "bio_memory.duckdb"),
        "L1_CACHE_PATH": str(data / "gold/hermes_cache.duckdb"),
        "ER_DB_BACKEND": "duckdb",
        "DUCKDB_APP_LOCK_PATH": str(data / ".locks/bio_memory.app.lock"),
        "EP_DECISION_STATE_PATH": str(data / ".locks/ep_decision.sqlite3"),
        "MCP_AUTH_REQUIRED": "false",
        "MCP_AUTH_TOKEN": "",
        "MCP_AUTH_TOKENS": "",
        "MCP_ENABLE_DANGEROUS_TOOLS": "false",
        "MPLCONFIGDIR": str(data / ".matplotlib"),
        "EMBEDDING_PROVIDER": "llamacpp",
        "EMBEDDING_MODEL": "bge-m3-Q8_0",
        "EMBEDDING_DIM": "1024",
        "LLAMACPP_BASE_URL": embedding_url,
    }


def process_env(settings: dict[str, str]) -> dict[str, str]:
    env = {k: os.environ[k] for k in ("PATH", "HOME", "TMPDIR", "SYSTEMROOT") if k in os.environ}
    return {**env, **settings}


def package_checks() -> list[dict]:
    checks = []
    for module, package in [
        ("duckdb", "duckdb"),
        ("mcp", "mcp"),
        ("dotenv", "python-dotenv"),
        ("pandas", "pandas"),
        ("sklearn", "scikit-learn"),
        ("matplotlib", "matplotlib"),
    ]:
        present = importlib.util.find_spec(module) is not None
        version = importlib.metadata.version(package) if present else None
        checks.append(
            {
                "name": package,
                "status": "ok" if present else "missing",
                "version": version,
                "required": True,
                "fix": "uv sync --locked --no-install-project" if not present else None,
            }
        )
    if importlib.util.find_spec("dotenv"):
        import inspect
        import dotenv.main

        supported = "PYTHON_DOTENV_DISABLED" in inspect.getsource(dotenv.main)
        checks.append(
            {
                "name": "dotenv-isolation",
                "status": "ok" if supported else "missing",
                "required": True,
                "fix": "uv sync --locked --no-install-project" if not supported else None,
            }
        )
    return checks


def init(
    data: Path, config: Path | None = None, embedding_url: str = "http://127.0.0.1:8081/v1"
) -> dict:
    data = data.resolve()
    checks = package_checks()
    if any(c["status"] != "ok" for c in checks):
        raise RuntimeError(
            "Dependencies missing/outdated; run uv sync --locked --no-install-project"
        )
    settings = local_env(data, embedding_url)
    config = config or data / "mcp.json"
    document = {
        "mcpServers": {
            "ep": {
                "command": sys.executable,
                "args": [str(ROOT / "server/bio_memory_server.py")],
                "env": settings,
            }
        }
    }
    if config.exists() and json.loads(config.read_text()) != document:
        raise RuntimeError(
            f"Existing MCP configuration differs; refusing to overwrite {config}. Choose --config PATH."
        )
    data.mkdir(parents=True, exist_ok=True)
    (data / ".locks").mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    log_path = data / f"setup-{stamp}.log"
    with log_path.open("w") as log:
        for command in [
            ["00_init_db.py", "--no-samples"],
            ["03_init_l1_cache.py"],
            ["run_pending_migrations.py"],
            ["04_migrate_l1_rrf.py"],
        ]:
            log.write(f"\n>>> {command[0]}\n")
            log.flush()
            try:
                subprocess.run(
                    [sys.executable, str(ROOT / "scripts" / command[0]), *command[1:]],
                    cwd=data,
                    env=process_env(settings),
                    stdout=log,
                    stderr=log,
                    check=True,
                    timeout=240,
                )
            except (subprocess.SubprocessError, OSError) as exc:
                raise RuntimeError(f"{command[0]} failed; preserved log: {log_path}") from exc
    config.parent.mkdir(parents=True, exist_ok=True)
    if not config.exists():
        # Exclusive creation prevents a concurrent setup from replacing another config.
        with config.open("x") as stream:
            stream.write(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
    return {
        "status": "ready",
        "data_dir": str(data),
        "mcp_config": str(config),
        "log": str(log_path),
        "next": "Add mcpServers.ep to your client; call bio_sample_list.",
        "optional": "Semantic search needs embeddings; Clef and Web chat are not started.",
    }


def doctor(
    data: Path, check_services: bool = False, embedding_url: str = "http://127.0.0.1:8081/v1"
) -> dict:
    checks = package_checks()
    db = data / "bio_memory.duckdb"
    if importlib.util.find_spec("duckdb"):
        import duckdb

        for name, path, query in [
            ("database", db, "SELECT max(version) FROM schema_migrations"),
            ("L1-cache", data / "gold/hermes_cache.duckdb", "SELECT count(*) FROM memory_recent"),
        ]:
            row = {
                "name": name,
                "required": True,
                "status": "missing",
                "fix": "Run ep_local.py init --data-dir PATH",
            }
            if path.is_file():
                try:
                    with duckdb.connect(str(path), read_only=True) as con:
                        value = con.execute(query).fetchone()[0]
                        if name == "database":
                            for table in (
                                "sample_registry",
                                "analysis_history",
                                "analysis_artifacts",
                                "tools",
                            ):
                                con.execute(f"SELECT 1 FROM {table} LIMIT 0")
                            if value is None or value < 30:
                                raise ValueError("schema outdated")
                        row.update(status="ok", fix=None)
                except Exception as exc:
                    row.update(
                        status="error",
                        detail=type(exc).__name__,
                        fix="Stop another writer if locked; run init for missing/outdated schema. Keep backups.",
                    )
            checks.append(row)
    for module in ("omicverse", "cellpose"):
        checks.append(
            {
                "name": module,
                "required": False,
                "status": "installed" if importlib.util.find_spec(module) else "not-installed",
                "fix": "Optional analysis extras; see SINGLE_USER guide",
            }
        )
    ledger = data / ".locks/ep_decision.sqlite3"
    clef = {
        "name": "Clef",
        "required": False,
        "status": "not-configured",
        "fix": "Optional; bio_decision_status checks live bridge readiness. No inference performed.",
    }
    if ledger.is_file():
        try:
            with sqlite3.connect(ledger.resolve().as_uri() + "?mode=ro", uri=True) as con:
                row = con.execute("SELECT v FROM state WHERE k='config'").fetchone()
                mode = json.loads(row[0])["mode"]
                clef["status"] = mode if mode in {"off", "rules", "shadow"} else "unknown"
        except (sqlite3.Error, ValueError, TypeError, KeyError):
            clef["status"] = "unreadable-ledger"
    checks.append(clef)
    status = "not-probed"
    if check_services:
        parts = urlsplit(embedding_url)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
        ):
            raise ValueError("Use an HTTP(S) embedding URL without credentials, query or fragment")
        try:
            with urlopen(f"{parts.scheme}://{parts.netloc}/health", timeout=3) as response:
                status = "reachable" if response.status == 200 else "unavailable"
        except Exception:
            status = "unreachable"
    checks.append(
        {
            "name": "embedding",
            "required": False,
            "status": status,
            "fix": "Start configured embedding service for semantic search. Health is not an inference test.",
        }
    )
    return {
        "ready": all(c["status"] == "ok" for c in checks if c["required"]),
        "checks": checks,
        "scope": "Local metadata + CPU EDA prerequisites; optional services are reported separately.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "doctor"])
    parser.add_argument("--data-dir", required=True, type=lambda s: Path(s).expanduser().resolve())
    parser.add_argument("--config", type=lambda s: Path(s).expanduser().resolve())
    parser.add_argument("--embedding-url", default="http://127.0.0.1:8081/v1")
    parser.add_argument(
        "--check-services", action="store_true", help="GET embedding /health only; no inference"
    )
    args = parser.parse_args()
    try:
        result = (
            init(args.data_dir, args.config, args.embedding_url)
            if args.command == "init"
            else doctor(args.data_dir, args.check_services, args.embedding_url)
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ready", True) else 1
    except (RuntimeError, ValueError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"status": "error", "detail": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
