from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any


SOURCE_SCHEMA = "t8-bundled-case-previews/v1"
CHANNEL_SCHEMA = "t8-remote-preview-channel/v1"
CATALOG_ID = "t8-unofficial-case-library-v2"
DEFAULT_REPOSITORY = "T8mars/comfyui-minimax-h3-prompt-enhancer-T8-assets"
MAX_GIF_BYTES = 4 * 1024 * 1024
MAX_SHARD_BYTES = 32 * 1024 * 1024
FIXED_ZIP_TIME = (2020, 1, 1, 0, 0, 0)


class PreviewBuildError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_digest(rows: list[dict[str, str]]) -> str:
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def safe_filename(value: Any) -> str:
    filename = Path(str(value or ""))
    if filename.name != str(filename) or filename.suffix.casefold() != ".gif":
        raise PreviewBuildError(f"Unsafe GIF filename: {value!r}")
    return filename.name


def load_source(manifest_path: Path, source_dir: Path) -> list[dict[str, Any]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest.get("previews") if isinstance(manifest, dict) else None
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != SOURCE_SCHEMA
        or manifest.get("catalog_id") != CATALOG_ID
        or not isinstance(entries, list)
        or manifest.get("preview_count") != len(entries)
    ):
        raise PreviewBuildError("Unsupported source preview manifest")

    records: list[dict[str, Any]] = []
    identities: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise PreviewBuildError("Source preview entry is not an object")
        case_id = str(entry.get("case_id") or "").strip()
        filename = safe_filename(entry.get("file"))
        file_path = (source_dir / filename).resolve()
        if not case_id or case_id in identities:
            raise PreviewBuildError(f"Missing or duplicate case identity: {case_id!r}")
        if entry.get("human_preview_only") is not True:
            raise PreviewBuildError(f"Preview is not marked human-only: {case_id}")
        if not file_path.is_file():
            raise PreviewBuildError(f"Preview GIF is missing: {case_id}")
        size = file_path.stat().st_size
        if size != int(entry.get("bytes", -1)) or size > MAX_GIF_BYTES:
            raise PreviewBuildError(f"Preview GIF size is invalid: {case_id}")
        with file_path.open("rb") as handle:
            if handle.read(6) not in {b"GIF87a", b"GIF89a"}:
                raise PreviewBuildError(f"Preview is not a GIF: {case_id}")
        file_digest = sha256(file_path)
        if file_digest != str(entry.get("sha256") or ""):
            raise PreviewBuildError(f"Preview SHA-256 mismatch: {case_id}")
        source_digest = str(entry.get("source_sha256") or "")
        if len(source_digest) != 64:
            raise PreviewBuildError(f"Source SHA-256 is missing: {case_id}")
        identities.add(case_id)
        records.append({
            "case_id": case_id,
            "source_sha256": source_digest,
            "file_sha256": file_digest,
            "bytes": size,
            "path": file_path,
            "file": f"{file_digest}.gif",
            "shard": f"shard-{file_digest[0]}",
            "human_preview_only": True,
        })
    return sorted(records, key=lambda item: item["case_id"])


def zip_member(name: str, payload: bytes) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_STORED
    info.external_attr = 0o100644 << 16
    return info


def build_shards(
    records: list[dict[str, Any]],
    *,
    output: Path,
    repository: str,
    release_tag: str,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[record["shard"]].append(record)

    shards: list[dict[str, Any]] = []
    for shard_id in sorted(grouped):
        members = grouped[shard_id]
        asset_name = f"preview-{shard_id}.zip"
        asset_path = output / asset_name
        unique_files: dict[str, Path] = {}
        for item in members:
            unique_files.setdefault(item["file"], item["path"])
        shard_manifest = {
            "schema_version": "t8-preview-shard/v1",
            "shard_id": shard_id,
            "files": [
                {
                    "file": filename,
                    "sha256": filename[:-4],
                    "bytes": path.stat().st_size,
                }
                for filename, path in sorted(unique_files.items())
            ],
        }
        with zipfile.ZipFile(asset_path, "w", allowZip64=True) as archive:
            encoded = (json.dumps(shard_manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
            archive.writestr(zip_member("shard.json", encoded), encoded)
            for filename, path in sorted(unique_files.items()):
                payload = path.read_bytes()
                archive.writestr(zip_member(filename, payload), payload)
        size = asset_path.stat().st_size
        if size > MAX_SHARD_BYTES:
            raise PreviewBuildError(f"Preview shard exceeds {MAX_SHARD_BYTES} bytes: {shard_id}")
        shards.append({
            "id": shard_id,
            "asset": asset_name,
            "url": f"https://github.com/{repository}/releases/download/{release_tag}/{asset_name}",
            "sha256": sha256(asset_path),
            "bytes": size,
            "file_count": len(unique_files),
        })
    return shards


def build_release(args: argparse.Namespace) -> dict[str, Any]:
    output = args.output.resolve()
    repository_root = Path(__file__).resolve().parents[1]
    dist_root = (repository_root / "dist").resolve()
    try:
        output.relative_to(dist_root)
    except ValueError as exc:
        raise PreviewBuildError("Output must be a child of this repository's dist directory") from exc
    if output == dist_root:
        raise PreviewBuildError("Output must name a version directory inside dist")
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    records = load_source(args.source_manifest.resolve(), args.source_dir.resolve())
    release_tag = args.release_tag or f"preview-assets-v{args.version}"
    shards = build_shards(
        records,
        output=output,
        repository=args.repository,
        release_tag=release_tag,
    )
    catalog_rows = [
        {"case_id": item["case_id"], "source_sha256": item["source_sha256"]}
        for item in records
    ]
    channel = {
        "schema_version": CHANNEL_SCHEMA,
        "channel_version": args.version,
        "catalog_id": CATALOG_ID,
        "catalog_digest": canonical_digest(catalog_rows),
        "repository": args.repository,
        "release_tag": release_tag,
        "preview_count": len(records),
        "human_preview_only": True,
        "policy": "Human UI previews only; never send or connect them to a model or LLM.",
        "shards": shards,
        "previews": [
            {key: item[key] for key in (
                "case_id", "source_sha256", "file_sha256", "bytes", "file", "shard", "human_preview_only"
            )}
            for item in records
        ],
    }
    channel_path = output / "channel.json"
    channel_path.write_text(json.dumps(channel, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "channel": str(channel_path),
        "version": args.version,
        "release_tag": release_tag,
        "catalog_digest": channel["catalog_digest"],
        "preview_count": len(records),
        "shard_count": len(shards),
        "release_bytes": sum(int(item["bytes"]) for item in shards),
    }, ensure_ascii=False))
    return channel


def main() -> int:
    parser = argparse.ArgumentParser(description="Build deterministic T8 preview release shards.")
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY)
    parser.add_argument("--release-tag", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build_release(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
