# claude-content ![v0.4.16](https://img.shields.io/badge/v0.4.16-blue?style=flat-square)

Content creation and processing tools for Claude Code. Seven skills covering image generation, video manipulation, social media formatting, audio extraction, and shot frame extraction.

## Why

Most content workflows involve the same small set of operations applied repeatedly: compress this for web, convert to a different format, make it fit Instagram, extract the audio, generate a thumbnail. The individual FFmpeg commands are tedious to remember and easy to get wrong — the right CRF for H.265, the palette generation pipeline for GIF quality, the aspect ratio math for Reels. These skills encode the correct defaults so you don't have to look them up, and handle multi-step operations (trim + resize + convert) as single commands rather than intermediate-file chains.

## Installation

```
/plugin marketplace add gupsammy/claudest
/plugin install claude-content@claudest
```

**Requirements:**
- `ffmpeg` and `ffprobe` — required for all video and audio skills
- `GEMINI_API_KEY` environment variable and `uv` — required for image generation only

## Skills

### generate-image

Generate and edit images using two model tiers: Nano Banana (default, fast generation with extended aspect ratios up to 8:1 panoramic, thinking mode, and Google Search grounding) and Nano Banana Pro (higher quality, up to 4K resolution). Handles three modes: text-to-image (describe what you want), image-to-image (provide a source image and editing instructions), and multi-reference composition (combine elements from up to 14 source images with a prompt). Supports batch generation (up to 4 variations), JPEG output with configurable quality, and automatic resolution and aspect ratio detection from input images. Every generated image gets a companion `.md` log file containing the prompt used. Images save to `~/Documents/generated images/` by default unless a custom path is specified.

The skill includes a structured prompting guide covering i2i editing failure modes (color label drift, same-color swap targets, Gemini camera move limitation, multi-pass editing, base scene contamination), fashion and garment editing patterns (base image selection, garment swap prompts, texture rendering), and frame extraction from video references using ffmpeg scene detection.

Triggers on: "generate an image", "create a picture", "make me a logo", "edit this image", "remove the background", "change the style", "combine these images", "add text to image", "make a sticker", "product mockup".

### compress-video

Compress video using quality-based or size-based encoding. Profiles the source file first, then applies CRF encoding to hit a visual quality target, or 2-pass encoding to hit a specific file size. Selects codec and settings based on the source and target.

Triggers on: "compress this video", "reduce file size", "make this video smaller", "optimize for web", "compress to under X MB", "encode with H.265", "re-encode this video".

### convert-video

General-purpose video manipulation: format conversion, trim, speed adjustment, slow motion, timelapse, resize, rotate, flip, remux. Multi-operation requests are chained into a single ffmpeg invocation — no intermediate files, no quality loss from multiple encode passes.

Triggers on: "convert this video", "change format to mp4", "trim from X to Y", "cut the first X seconds", "speed up this video", "slow motion", "timelapse", "resize video", "rotate video", "flip video".

### make-gif

Convert a video clip to a high-quality animated GIF using the mandatory 2-pass palette workflow. `palettegen` analyzes the clip to build an optimal 256-color palette; `paletteuse` renders with it. Single-pass GIF always produces banding and color artifacts. This doesn't.

Triggers on: "make a GIF", "convert to GIF", "create a GIF from this video", "export as GIF", "turn this clip into a GIF", "gif this".

### share-social

Prepare video for platform-specific upload requirements: correct aspect ratios, resolutions, bitrates, and container settings. Presets for Instagram Reels, YouTube Shorts, TikTok (9:16), square posts (1:1), Twitter, Facebook, and LinkedIn.

Triggers on: "optimize for Instagram", "YouTube Shorts format", "make it 9:16", "TikTok format", "Reels format", "prepare for social media", "encode for Twitter", "crop for portrait".

### extract-audio

Rip the audio track from any video file, with format selection based on use case: FLAC for lossless archival, MP3 VBR for transparent compression at small file sizes, MP3 128k CBR for podcasts and voice, AAC for mobile and streaming compatibility, WAV for DAW editing, or stream copy when the source is already in the target format. Warns before lossy-to-lossy transcoding. Handles multi-stream files by prompting for stream selection.

Triggers on: "extract audio", "get the mp3", "strip audio from video", "rip audio", "save audio from video", "get the soundtrack", "pull the audio track", "export audio".

### extract-frames

Detect shot boundaries in a video using adaptive scene scoring and extract the first and/or last frame of each shot. Dumps per-frame scene scores once, then reuses them across re-runs so threshold iteration is instant. Auto-detects a threshold from gap analysis between noise and signal, presents the distribution and proposed value for confirmation, then applies run-based deduplication to eliminate aftershock spikes while preserving rapid-montage cuts.

Triggers on: "extract frames", "get shot frames", "pull frames", "shot breakdown", "scene detect", "first frame of each shot", "last frame of each shot", "extract shots from video".

## Roadmap

Three FFmpeg skills are planned for a future release:

`video-concat` — join multiple clips end-to-end using the concat demuxer (same codec, instant) or concat filter (handles mixed formats and resolutions with re-encode).

`video-subtitle` — burn SRT or ASS subtitles into video, choosing between soft subs (stream copy) and hard subs (baked in), with font and style override support.

`video-watermark` — overlay a text or image logo using the `drawtext` or `overlay` filter, with full control over position, opacity, and timing.

## License

MIT
