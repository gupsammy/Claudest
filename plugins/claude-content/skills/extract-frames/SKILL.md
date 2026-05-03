---
name: extract-frames
description: >
  Extracts first and/or last frames of every shot from a video using adaptive scene detection.
  Use when the user says "extract frames", "get shot frames", "pull frames", "shot breakdown",
  "scene detect", "first frame of each shot", "last frame of each shot", "extract shots from video",
  or wants to extract key frames at shot cut points from a video file.
allowed-tools:
  - Bash(ffprobe:*)
  - Bash(ffmpeg:*)
  - Bash(mkdir:*)
  - Bash(wc:*)
  - Bash(cat:*)
  - AskUserQuestion
  - Read
argument-hint: "<video-path> [--last] [--all] [--shots N,N] [--threshold X]"
---

# Extract Frames

## Arguments

Parse the user's request for:
- **video path** (required): path to the video file
- **--last**: extract last frame of each shot instead of first
- **--first --last** or **--all**: extract both first and last frames
- **--shots N,N,N**: only process specific shot numbers (1-indexed)
- **--threshold X**: override auto-detected threshold (skip user confirmation)

Default: extract first frame of every shot.

## Workflow

### Phase 0: Setup

1. Validate the video file exists.
2. Get video metadata via ffprobe:
```bash
ffprobe -v error -show_entries format=duration -show_entries stream=r_frame_rate,width,height,codec_name -of default "$INPUT"
```
3. Parse fps (needed for last-frame calculation) and duration.
4. Create output directory next to the video: `{video_basename}_frames/`. If it already exists, check for `scores.txt` — if present, skip Phase 1.

### Phase 1: Score Dump

Dump per-frame scene scores for the entire video:
```bash
ffmpeg -i "$INPUT" -vf "select='gte(scene,0)',metadata=print:file=$OUTPUT_DIR/scores.txt" -vsync vfr -f null - 2>&1
```

This is the expensive step. The output file `scores.txt` contains blocks like:
```
frame:0    pts:0       pts_time:0.000000
lavfi.scene_score=0.000000
```

### Phase 2: Cut Detection

Parse scores.txt with this awk one-liner to get distribution + all candidate frames:
```bash
awk '
/pts_time/ { split($0, a, "pts_time:"); ts=a[2]+0 }
/scene_score/ { split($0, a, "="); score=a[2]+0;
  if (score < 0.01) b1++;
  else if (score < 0.05) b2++;
  else if (score < 0.10) b3++;
  else if (score < 0.20) b4++;
  else b5++;
  total++;
  if (score > max) max=score;
  if (score > 0.05) printf "  ts=%.3fs  score=%.6f\n", ts, score;
}
END {
  print "\n--- Distribution ---";
  printf "< 0.01:      %d (%.1f%%)\n", b1, b1/total*100;
  printf "0.01 - 0.05: %d (%.1f%%)\n", b2, b2/total*100;
  printf "0.05 - 0.10: %d (%.1f%%)\n", b3, b3/total*100;
  printf "0.10 - 0.20: %d (%.1f%%)\n", b4, b4/total*100;
  printf "0.20+:       %d (%.1f%%)\n", b5, b5/total*100;
  printf "Max score:   %.6f\n", max;
}' "$OUTPUT_DIR/scores.txt"
```

**Step 2a — Startup artifact filter:** Discard any above-threshold frames in the first 0.5s of the video. Fade-ins from black or codec initialization commonly produce score=1.0 spikes at t=0.03-0.08s that are not real cuts. After discarding these, recompute max score from the remaining frames.

**Step 2b — Branch on max score.** If max score (after startup filter) < 0.05, the video has no cuts:
- Report: "No shot boundaries detected — single continuous shot."
- Extract only frame at t=0 (or t=1.0s if t=0 is a black frame — check file size, <50KB indicates black).
- Skip threshold confirmation.

**Step 2c — Run-based dedup.** If max score >= 0.05, identify cut points using run-based dedup:

A "run" is a sequence of consecutive frames where every frame scores above threshold. The principle: an aftershock immediately follows its parent cut (consecutive frames both above threshold), while a real cut always rises from the noise floor (preceded by at least one below-threshold frame).

```bash
awk '
/pts_time/ { split($0, a, "pts_time:"); ts=a[2]+0 }
/scene_score/ { split($0, a, "="); score=a[2]+0;
  if (score > THRESHOLD) {
    if (!in_run) { run_best_ts = ts; run_best_score = score; in_run = 1; }
    else if (score > run_best_score) { run_best_ts = ts; run_best_score = score; }
  } else {
    if (in_run) { printf "%.3f %.6f\n", run_best_ts, run_best_score; in_run = 0; }
  }
}
END { if (in_run) printf "%.3f %.6f\n", run_best_ts, run_best_score }
' "$OUTPUT_DIR/scores.txt"
```

This keeps the peak frame of each run and discards aftershocks within the same run. It correctly handles:
- **Standard cuts**: isolated spikes → each kept (run length 1)
- **Cuts with aftershocks**: 2-3 consecutive high frames → peak kept, echoes discarded
- **Rapid montages**: each cut separated by noise frames → all kept, even at 0.12s intervals

**Step 2d — Gap analysis and threshold.** Find noise ceiling and lowest cut from the deduped list. Place threshold at midpoint of the gap. Present to user via AskUserQuestion:
- Score distribution summary
- Gap analysis (noise ceiling → lowest cut, gap width)
- Proposed threshold and resulting shot count
- Options: Accept proposed (Recommended), Lower threshold, Higher threshold, Custom value

If `--threshold` was provided, skip confirmation. Still apply run-based dedup.

### Phase 3: Frame Extraction

For each detected cut point (plus t=0 for shot 1):

**First frame** (default):
```bash
ffmpeg -y -ss $TIMESTAMP -i "$INPUT" -frames:v 1 -update 1 "$OUTPUT_DIR/shot_${NUM}_${TIME}s.png"
```

**Last frame** (`--last`):
- For shots 1 through N-1: `last_time = next_shot_timestamp - (1/fps)`
- For final shot: `last_time = duration - (2/fps)` (use 2 frames back, not 1 — seeking to `duration - 1/fps` can produce empty files near the end of some videos)
```bash
ffmpeg -y -ss $LAST_TIME -i "$INPUT" -frames:v 1 -update 1 "$OUTPUT_DIR/shot_${NUM}_last_${TIME}s.png"
```
If a last-frame extraction produces an empty file (0 bytes), back off by another frame and retry.

**Both** (`--first --last` or `--all`): extract both per shot.

**Filtered** (`--shots 3,5,7`): only extract for the specified shot numbers.

### Output

- Directory: `{video_basename}_frames/` next to the input video
- First frames: `shot_01_0.00s.png`, `shot_02_1.63s.png`, ...
- Last frames: `shot_01_last_1.60s.png`, `shot_02_last_3.77s.png`, ...
- `scores.txt` always retained for re-runs at different thresholds

Report to user: total shots detected, shot list with timestamps, output directory path.

## Re-run Behavior

If `scores.txt` already exists in the output directory, skip Phase 1 entirely and go straight to Phase 2 analysis. This makes threshold iteration instant — the user can re-run with `--threshold 0.15` without waiting for the score dump again.

## Shell Portability

Use pipe-based loops for frame extraction instead of array syntax (zsh handles `for` over arrays differently than bash):
```bash
echo "0.000 2.480 4.280" | tr ' ' '\n' | awk '{printf "%02d %s\n", NR, $1}' | while read LABEL TS; do
  TS_FMT=$(printf "%.2f" "$TS")
  ffmpeg -y -ss "$TS" -i "$INPUT" -frames:v 1 -update 1 "$OUTPUT_DIR/shot_${LABEL}_${TS_FMT}s.png" 2>/dev/null
done
```

## Edge Cases

- **Single continuous shots:** Handled by step 2b. Common in fashion videos with rack-focus reveals, slow wardrobe progression, or single-take lifestyle shots.
- **Startup artifacts:** Fade-ins from black produce score=1.0 at t=0.03-0.08s. Step 2a discards above-threshold frames in the first 0.5s. If t=0 itself is a black frame (PNG < 50KB), extract at t=1.0s instead.
- **Aftershock spikes:** Consecutive above-threshold frames (a cut + its echo). Run-based dedup in step 2c keeps only the peak of each run — no temporal window needed.
- **Rapid montages:** Videos where shots are 2-4 frames long (0.08-0.16s). Each cut rises from the noise floor with 1-2 noise frames between spikes. Run-based dedup correctly preserves every cut because no two spikes are frame-adjacent. Report montage segments to the user: "Detected rapid montage from Xs-Ys with N shots."
- **Dissolves/fades:** Score gradually ramps over multiple frames — forms a single run. Run-based dedup takes the peak frame as the cut point.
- **Empty file on seek:** If ffmpeg produces a 0-byte PNG (common near video end), back off by one frame interval and retry.
