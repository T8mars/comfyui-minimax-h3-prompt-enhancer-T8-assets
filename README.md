# T8 Prompt Enhancer Preview Assets

This repository distributes human-only dynamic previews for
[`T8mars/comfyui-minimax-h3-prompt-enhancer-T8`](https://github.com/T8mars/comfyui-minimax-h3-prompt-enhancer-T8).

The preview media is never used as an image, video, audio, model, or LLM input.
The ComfyUI node downloads only catalog-compatible, SHA-256-pinned GIF shards
and stores them under the active ComfyUI user directory.

## Distribution contract

- `channel.json` is the small update index consumed by the node.
- Release assets are immutable `preview-shard-*.zip` files.
- Every shard and every extracted GIF is SHA-256 verified.
- A channel is accepted only when every preview identity and source hash matches
  the case catalog installed with the node.
- New prompt templates still ship through the versioned Comfy Registry node;
  this repository updates human preview media only.

## Build a release

```powershell
python tools/build_preview_release.py `
  --source-manifest path/to/web/js/assets/t8-case-previews/manifest.json `
  --source-dir path/to/web/js/assets/t8-case-previews `
  --version 2026.08.29.2 `
  --output dist/2026.08.29.2
```

The generated channel is deterministic for the same input, version, repository,
and release tag. `dist/` is intentionally not tracked.
