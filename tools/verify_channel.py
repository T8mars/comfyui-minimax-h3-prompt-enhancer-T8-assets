from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit


CHANNEL_SCHEMA = "t8-remote-preview-channel/v1"
ALLOWED_HOST = "github.com"


class ChannelVerificationError(RuntimeError):
    pass


def verify(path: Path) -> dict:
    channel = json.loads(path.read_text(encoding="utf-8"))
    previews = channel.get("previews") if isinstance(channel, dict) else None
    shards = channel.get("shards") if isinstance(channel, dict) else None
    if (
        not isinstance(channel, dict)
        or channel.get("schema_version") != CHANNEL_SCHEMA
        or channel.get("human_preview_only") is not True
        or not isinstance(previews, list)
        or not isinstance(shards, list)
        or channel.get("preview_count") != len(previews)
    ):
        raise ChannelVerificationError("Unsupported preview channel")
    shard_ids = set()
    for shard in shards:
        if not isinstance(shard, dict) or shard.get("id") in shard_ids:
            raise ChannelVerificationError("Missing or duplicate shard")
        if urlsplit(str(shard.get("url") or "")).hostname != ALLOWED_HOST:
            raise ChannelVerificationError("Shard URL is not an approved GitHub release URL")
        if len(str(shard.get("sha256") or "")) != 64 or int(shard.get("bytes") or 0) <= 0:
            raise ChannelVerificationError("Shard integrity metadata is invalid")
        shard_ids.add(shard["id"])
    identities = set()
    catalog_rows = []
    for preview in previews:
        case_id = str(preview.get("case_id") or "") if isinstance(preview, dict) else ""
        if not case_id or case_id in identities or preview.get("shard") not in shard_ids:
            raise ChannelVerificationError("Preview identity or shard mapping is invalid")
        if preview.get("human_preview_only") is not True:
            raise ChannelVerificationError("Preview policy boundary is missing")
        for field in ("source_sha256", "file_sha256"):
            if len(str(preview.get(field) or "")) != 64:
                raise ChannelVerificationError(f"Preview {field} is invalid")
        if preview.get("file") != f"{preview['file_sha256']}.gif":
            raise ChannelVerificationError("Preview content-addressed filename is invalid")
        identities.add(case_id)
        catalog_rows.append({"case_id": case_id, "source_sha256": preview["source_sha256"]})
    payload = json.dumps(sorted(catalog_rows, key=lambda item: item["case_id"]), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    if digest != channel.get("catalog_digest"):
        raise ChannelVerificationError("Catalog digest mismatch")
    return channel


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a T8 preview channel manifest.")
    parser.add_argument("channel", type=Path, nargs="?", default=Path("channel.json"))
    args = parser.parse_args()
    channel = verify(args.channel)
    print(json.dumps({
        "version": channel["channel_version"],
        "preview_count": channel["preview_count"],
        "shard_count": len(channel["shards"]),
        "passed": True,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
