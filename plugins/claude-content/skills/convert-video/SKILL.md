---
name: convert-video
description: >
  This skill should be used when the user asks to "convert this video",
  "change format to mp4", "trim from X to Y", "cut the first X seconds",
  "speed up this video", "slow motion", "timelapse", "resize video",
  "scale down", "rotate video", "flip video", "remux", or any general
  FFmpeg video manipulation not covered by compress-video, make-gif,
  share-social, extract-audio, or extract-frames.
allowed-tools:
  - Bash(ffprobe:*)
  - Bash(ffmpeg:*)
  - AskUserQuestion
---

# Video Convert

Identify the operation type first, then apply the matching pattern below. For multi-operation requests (e.g., trim + resize + convert), chain all filters in a single ffmpeg invocation — avoid intermediate files.

## Process

1. Identify the operation(s) from the user's request.
2. Probe codec when converting formats; probe dimensions when resizing. Speed, rotation, flip, and frame extraction do not require probing.
3. Construct the command using the appropriate pattern.
4. Confirm with the user before running.
5. Run and report output file path and size.

## Operation Patterns

### Format Conversion

Probe codec first — determines whether `-c copy` is safe:
```bash
ffprobe -v quiet -show_streams "$INPUT" | grep codec_name
```

```bash
# Remux: same codec, different container (e.g., H.264 MKV → MP4) — instant, lossless:
ffmpeg -i input.mkv -c copy output.mp4

# Re-encode: different codec required:
ffmpeg -i input.avi -c:v libx264 -crf 23 -c:a aac -b:a 128k output.mp4
```

### Trimming

```bash
# Fast trim without re-encoding (-ss before -i = container-level seek):
ffmpeg -ss 00:01:30 -to 00:03:45 -i "$INPUT" -c copy "$OUTPUT"

# Re-encoding trim (use when -c copy causes A/V sync issues near keyframes):
ffmpeg -ss 00:01:30 -to 00:03:45 -i "$INPUT" -c:v libx264 -crf 23 -c:a copy "$OUTPUT"
```

Always try `-c copy` first. Fall back to re-encoding only if the user reports sync issues or the cut must land on a non-keyframe boundary.

### Speed Change

```bash
# 2x speed:
ffmpeg -i "$INPUT" -filter:v "setpts=0.5*PTS" -filter:a "atempo=2.0" "$OUTPUT"

# 0.5x slow-motion:
ffmpeg -i "$INPUT" -filter:v "setpts=2.0*PTS" -filter:a "atempo=0.5" "$OUTPUT"
```

`setpts` factor = `1 / speed_multiplier`. `atempo` range is 0.5–2.0 — chain for higher multiples:
`atempo=2.0,atempo=2.0` achieves 4x. For silent video, omit `-filter:a` entirely.

### Resize / Scale

Probe dimensions first: `ffprobe -v quiet -show_streams "$INPUT" | grep -E "width|height"`

```bash
# Scale to width, auto height (-2 ensures H.264-compatible even height):
ffmpeg -i "$INPUT" -vf "scale=1280:-2" -c:a copy "$OUTPUT"

# Fit within bounding box without upscaling:
ffmpeg -i "$INPUT" \
  -vf "scale='min(1280,iw)':'min(720,ih)':force_original_aspect_ratio=decrease,pad=ceil(iw/2)*2:ceil(ih/2)*2" \
  -c:a copy "$OUTPUT"
```

### Rotation / Flip

```bash
# 90° clockwise:         ffmpeg -i "$INPUT" -vf "transpose=1" "$OUTPUT"
# 90° counter-clockwise: ffmpeg -i "$INPUT" -vf "transpose=2" "$OUTPUT"
# 180°:                  ffmpeg -i "$INPUT" -vf "transpose=1,transpose=1" "$OUTPUT"
# Horizontal flip:       ffmpeg -i "$INPUT" -vf "hflip" "$OUTPUT"
# Vertical flip:         ffmpeg -i "$INPUT" -vf "vflip" "$OUTPUT"
```

### Frame Extraction

```bash
# Single frame at timestamp:
ffmpeg -ss 00:00:10 -i "$INPUT" -frames:v 1 -q:v 2 output.jpg

# One frame every N seconds:
ffmpeg -i "$INPUT" -vf "fps=1/$N" -q:v 2 frames/frame_%04d.jpg
```

`-q:v 2` is near-maximum JPEG quality. Use `-q:v 1` for the highest quality setting.

## Key Decisions

- Never upscale — add `force_original_aspect_ratio=decrease` when fitting to a bounding box.
- For multi-filter operations, chain with commas in a single `-vf`; but `-filter:v` and `-filter:a` must remain separate flags.
- Use `-c:a copy` in all re-encode operations unless the audio format itself needs to change.
- If ffprobe reveals a `rotate` metadata tag on the stream, use `-vf "transpose=..."` to bake rotation into pixels — `-c copy` preserves the metadata flag without rotating the actual frame data, which confuses many players.
