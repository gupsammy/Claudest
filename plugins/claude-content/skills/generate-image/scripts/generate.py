#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "google-genai>=1.0.0",
#     "pillow>=10.0.0",
# ]
# ///
"""
Generate and edit images using Nano Banana (Gemini 3.1 Flash Image) and Nano Banana Pro (Gemini 3 Pro Image).

Supports three modes:
  - t2i (text-to-image): Generate from prompt only
  - i2i (image-to-image): Edit a single image with a prompt
  - Multi-reference: Compose from multiple images

Usage:
    # Text-to-image (Nano Banana default)
    uv run generate.py --prompt "A cat in space" --output cat.png

    # Use Nano Banana Pro model
    uv run generate.py --prompt "A cat in space" --output cat.png --model pro

    # Image editing
    uv run generate.py --prompt "Make it blue" --input photo.png --output edited.png

    # Multi-reference composition
    uv run generate.py --prompt "Combine cat from first with background from second" \\
        --input cat.png --input background.png --output composite.png

    # Batch generation (up to 4 images, async parallel)
    uv run generate.py --prompt "A cat in space" --output cat.png --batch 4

    # JSON output for agent consumption
    uv run generate.py --prompt "A cat in space" --output cat.png --json

Options:
    --prompt, -p          Image description or edit instruction (required)
    --output, -o          Output file path (required)
    --input, -i           Input image path for editing (can be repeated up to 14 times)
    --model, -m           Model: nano-banana (default) or pro
    --aspect, -a          Aspect ratio (1:1, 16:9, 9:16, etc.)
    --resolution, -r      Resolution: 0.5K, 1K, 2K, 4K (default: auto-detect or 1K)
    --grounding, -g       Enable Google Search web grounding
    --image-grounding     Enable image search grounding (Nano Banana only)
    --thinking, -t        Thinking level: minimal, low, medium, high (Nano Banana only)
    --quality, -q         Output compression quality 1-100 (JPEG only)
    --format, -f          Output format: png (default) or jpeg
    --batch, -b           Generate multiple variations (1-4, default: 1)
    --json                Output results as JSON for agent consumption
    --quiet               Suppress progress output (MEDIA lines still printed)

Environment:
    GEMINI_API_KEY - Required API key

Exit codes:
    0 - Success
    1 - Generation or validation error
    2 - Environment error (missing API key)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from io import BytesIO
from pathlib import Path


# --- Model registry ---

MODELS = {
    "nano-banana": "gemini-3.1-flash-image",
    "pro": "gemini-3-pro-image",
}
MODEL_DISPLAY = {
    "nano-banana": "Nano Banana",
    "pro": "Nano Banana Pro",
}
DEFAULT_MODEL = "nano-banana"

PRO_RATIOS = ["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"]
NB_RATIOS = PRO_RATIOS + ["1:4", "4:1", "1:8", "8:1"]
PRO_RESOLUTIONS = ["1K", "2K", "4K"]
NB_RESOLUTIONS = ["0.5K"] + PRO_RESOLUTIONS


# --- Aspect ratio auto-detection ---

SUPPORTED_RATIOS = [
    ("1:1", 1.0),
    ("1:4", 1/4),
    ("1:8", 1/8),
    ("2:3", 2/3),
    ("3:2", 3/2),
    ("3:4", 3/4),
    ("4:1", 4/1),
    ("4:3", 4/3),
    ("4:5", 4/5),
    ("5:4", 5/4),
    ("8:1", 8/1),
    ("9:16", 9/16),
    ("16:9", 16/9),
    ("21:9", 21/9),
]


def get_closest_aspect_ratio(width: int, height: int, model: str = "nano-banana") -> str:
    """Find closest supported aspect ratio for given dimensions and model."""
    valid_ratios = NB_RATIOS if model == "nano-banana" else PRO_RATIOS
    candidates = [(name, val) for name, val in SUPPORTED_RATIOS if name in valid_ratios]
    actual_ratio = width / height
    closest = min(candidates, key=lambda x: abs(x[1] - actual_ratio))
    return closest[0]


# --- Resolution auto-detection ---

def detect_resolution(images: list) -> str:
    """Auto-detect appropriate resolution from input image dimensions."""
    if not images:
        return "1K"

    max_dim = 0
    for img in images:
        width, height = img.size
        max_dim = max(max_dim, width, height)

    if max_dim >= 3000:
        return "4K"
    elif max_dim >= 1500:
        return "2K"
    return "1K"


# --- Validation ---

def validate_model_params(model: str, aspect: str | None, resolution: str | None,
                          thinking: str | None, image_grounding: bool) -> None:
    """Validate parameters against model capabilities. Exits on invalid combinations."""
    valid_ratios = NB_RATIOS if model == "nano-banana" else PRO_RATIOS
    valid_resolutions = NB_RESOLUTIONS if model == "nano-banana" else PRO_RESOLUTIONS

    if aspect and aspect not in valid_ratios:
        print(f"Error: Aspect ratio '{aspect}' not supported by {model} model. "
              f"Valid: {', '.join(valid_ratios)}", file=sys.stderr)
        sys.exit(1)

    if resolution and resolution not in valid_resolutions:
        print(f"Error: Resolution '{resolution}' not supported by {model} model. "
              f"Valid: {', '.join(valid_resolutions)}", file=sys.stderr)
        sys.exit(1)

    if thinking and model != "nano-banana":
        print("Error: --thinking is only supported with Nano Banana model", file=sys.stderr)
        sys.exit(1)

    if image_grounding and model != "nano-banana":
        print("Error: --image-grounding is only supported with Nano Banana model", file=sys.stderr)
        sys.exit(1)


# --- Image optimization ---

MAX_DIMENSION = 2048


def optimize_image(img, max_dim=MAX_DIMENSION):
    """Resize if larger than max_dim, preserving aspect ratio."""
    from PIL import Image
    width, height = img.size
    if max(width, height) <= max_dim:
        return img

    scale = max_dim / max(width, height)
    new_size = (round(width * scale), round(height * scale))
    return img.resize(new_size, Image.Resampling.LANCZOS)


# --- Prompt logging ---

def save_prompt_log(
    log_path: Path,
    prompt: str,
    output_images: list[Path],
    source_images: list[str] | None = None,
    model: str | None = None,
):
    """Save the prompt used to generate images as a single .md file."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    content = "# Image Generation Log\n\n"
    content += f"**Generated**: {timestamp}\n\n"

    if model:
        content += f"**Model**: {model}\n\n"

    if len(output_images) == 1:
        content += f"**Output**: `{output_images[0].name}`\n\n"
    else:
        content += "**Outputs**:\n"
        for img in output_images:
            content += f"- `{img.name}`\n"
        content += "\n"

    if source_images:
        content += "**Source Images**:\n"
        for src in source_images:
            content += f"- `{src}`\n"
        content += "\n"

    content += f"## Prompt\n\n```\n{prompt}\n```\n"

    log_path.write_text(content)


# --- Image extraction ---

def extract_and_save_image(response, output_path: Path,
                           output_format: str = "png",
                           quality: int | None = None) -> str | None:
    """Extract image from response and save in requested format.

    The API returns images in its preferred format (usually JPEG). We use PIL
    to convert to the user's requested format and handle RGBA-to-RGB conversion.
    """
    from PIL import Image as PILImage

    parts = response.parts if hasattr(response, 'parts') else response.candidates[0].content.parts

    text_response = None
    image_saved = False

    for part in parts:
        # Skip thought parts (intermediate reasoning images)
        if getattr(part, 'thought', None):
            continue
        if part.text is not None:
            text_response = part.text
        elif part.inline_data is not None:
            img = PILImage.open(BytesIO(part.inline_data.data))

            # Convert RGBA to RGB (compositing alpha onto white)
            if img.mode == 'RGBA':
                rgb = PILImage.new('RGB', img.size, (255, 255, 255))
                rgb.paste(img, mask=img.split()[3])
                img = rgb
            elif img.mode != 'RGB':
                img = img.convert('RGB')

            # Save in requested format
            if output_format == "jpeg":
                save_kwargs = {"format": "JPEG"}
                if quality is not None:
                    save_kwargs["quality"] = quality
                img.save(str(output_path), **save_kwargs)
            else:
                img.save(str(output_path), "PNG")
            image_saved = True

    if not image_saved:
        raise RuntimeError("No image was generated. Check your prompt and try again.")

    return text_response


# --- JSON output ---

def format_json_output(results: list[dict], errors: list[dict], total: int) -> str:
    """Format generation results as JSON for agent consumption."""
    return json.dumps({
        "success": len(errors) == 0,
        "generated": len(results),
        "total_requested": total,
        "images": results,
        "errors": errors,
    }, indent=2)


# --- Image copying for thread safety ---

def copy_images(images: list) -> list | None:
    """Create deep copies of PIL Images to avoid thread-safety issues."""
    if not images:
        return None
    return [img.copy() for img in images]


# --- Async generation pipeline ---

async def generate_image_async(
    client,
    model: str,
    prompt: str,
    output_path: Path,
    input_images: list | None = None,
    aspect_ratio: str | None = None,
    resolution: str | None = None,
    grounding: bool = False,
    image_grounding: bool = False,
    thinking_level: str | None = None,
    output_format: str = "png",
    quality: int | None = None,
) -> str | None:
    """Generate or edit an image asynchronously."""
    from google.genai import types

    # Auto-detect resolution if not specified
    if resolution is None:
        resolution = detect_resolution(input_images or [])

    # Build contents: images first (if any), then prompt
    if input_images:
        contents = input_images + [prompt]
    else:
        contents = [prompt]

    # Build config
    config_kwargs = {"response_modalities": ["TEXT", "IMAGE"]}

    # Image config
    image_config_kwargs = {}
    if aspect_ratio:
        image_config_kwargs["aspect_ratio"] = aspect_ratio
    if resolution:
        image_config_kwargs["image_size"] = resolution
    if image_config_kwargs:
        config_kwargs["image_config"] = types.ImageConfig(**image_config_kwargs)

    # Grounding tools
    if grounding or image_grounding:
        search_types_kwargs = {}
        if grounding:
            search_types_kwargs["web_search"] = types.WebSearch()
        if image_grounding:
            search_types_kwargs["image_search"] = types.ImageSearch()
        config_kwargs["tools"] = [types.Tool(
            google_search=types.GoogleSearch(
                search_types=types.SearchTypes(**search_types_kwargs)
            )
        )]

    # Thinking config (Nano Banana only)
    if thinking_level and model == "nano-banana":
        level_map = {
            "minimal": types.ThinkingLevel.MINIMAL,
            "low": types.ThinkingLevel.LOW,
            "medium": types.ThinkingLevel.MEDIUM,
            "high": types.ThinkingLevel.HIGH,
        }
        config_kwargs["thinking_config"] = types.ThinkingConfig(
            thinking_level=level_map[thinking_level]
        )

    config = types.GenerateContentConfig(**config_kwargs)

    # Use async API
    response = await client.aio.models.generate_content(
        model=MODELS[model],
        contents=contents,
        config=config,
    )

    text_response = extract_and_save_image(response, output_path, output_format, quality)
    return text_response


async def generate_single(
    client,
    model: str,
    idx: int,
    total: int,
    out_path: Path,
    prompt: str,
    input_images: list | None,
    aspect_ratio: str | None,
    resolution: str | None,
    grounding: bool,
    image_grounding: bool,
    thinking_level: str | None,
    output_format: str,
    quality: int | None,
) -> tuple[int, Path, str | None, Exception | None]:
    """Generate a single image, return (index, path, text, error)."""
    try:
        task_images = copy_images(input_images)
        text = await generate_image_async(
            client=client,
            model=model,
            prompt=prompt,
            output_path=out_path,
            input_images=task_images,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            grounding=grounding,
            image_grounding=image_grounding,
            thinking_level=thinking_level,
            output_format=output_format,
            quality=quality,
        )
        return (idx, out_path, text, None)
    except Exception as e:
        return (idx, out_path, None, e)


async def run_batch(
    client,
    model: str,
    output_paths: list[Path],
    prompt: str,
    input_images: list | None,
    aspect_ratio: str | None,
    resolution: str | None,
    grounding: bool,
    image_grounding: bool,
    thinking_level: str | None,
    output_format: str,
    quality: int | None,
) -> list[tuple[int, Path, str | None, Exception | None]]:
    """Run batch generation using asyncio.gather for true async parallelism."""
    total = len(output_paths)
    tasks = [
        generate_single(
            client=client,
            model=model,
            idx=i,
            total=total,
            out_path=path,
            prompt=prompt,
            input_images=input_images,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            grounding=grounding,
            image_grounding=image_grounding,
            thinking_level=thinking_level,
            output_format=output_format,
            quality=quality,
        )
        for i, path in enumerate(output_paths, 1)
    ]
    return await asyncio.gather(*tasks)


async def async_main(args, input_images, input_paths, output_paths):
    """Async entry point for image generation."""
    from google import genai

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    if not args.quiet:
        print("Generating...")

    batch_results = await run_batch(
        client=client,
        model=args.model,
        output_paths=output_paths,
        prompt=args.prompt,
        input_images=input_images,
        aspect_ratio=args.aspect,
        resolution=args.resolution,
        grounding=args.grounding,
        image_grounding=args.image_grounding,
        thinking_level=args.thinking,
        output_format=args.format,
        quality=args.quality,
    )

    # Process results
    json_results = []
    json_errors = []
    results = []

    for idx, out_path, text, error in sorted(batch_results, key=lambda x: x[0]):
        if error:
            if not args.quiet:
                print(f"\n[{idx}/{args.batch}] Error: {error}", file=sys.stderr)
            json_errors.append({"index": idx, "error": str(error)})
        else:
            full_path = out_path.resolve()
            if not args.quiet:
                print(f"\n[{idx}/{args.batch}] Image saved: {full_path}")
            # MEDIA line always prints — agents rely on this for image display
            print(f"MEDIA: {full_path}")
            if text and not args.quiet:
                print(f"Model response: {text}")
            results.append(full_path)
            json_results.append({
                "index": idx,
                "path": str(full_path),
                "model_response": text,
            })

    if args.json:
        print(format_json_output(json_results, json_errors, args.batch))

    return results


# --- CLI ---

def main():
    parser = argparse.ArgumentParser(
        description="Generate and edit images using Gemini Image API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        "--prompt", "-p",
        required=True,
        help="Image description or edit instruction"
    )
    parser.add_argument(
        "--output", "-o",
        required=True,
        help="Output file path (e.g., output.png)"
    )
    parser.add_argument(
        "--input", "-i",
        action="append",
        dest="inputs",
        help="Input image path for editing/composition (can be repeated up to 14 times)"
    )
    parser.add_argument(
        "--model", "-m",
        choices=["nano-banana", "pro"],
        default=DEFAULT_MODEL,
        help="Model: nano-banana (default) or pro"
    )
    parser.add_argument(
        "--aspect", "-a",
        help="Aspect ratio (e.g., 1:1, 16:9, 9:16)"
    )
    parser.add_argument(
        "--resolution", "-r",
        help="Output resolution: 0.5K, 1K, 2K, 4K (default: auto-detect or 1K)"
    )
    parser.add_argument(
        "--grounding", "-g",
        action="store_true",
        help="Enable Google Search web grounding"
    )
    parser.add_argument(
        "--image-grounding",
        action="store_true",
        help="Enable image search grounding (Nano Banana only, use with --grounding)"
    )
    parser.add_argument(
        "--thinking", "-t",
        choices=["minimal", "low", "medium", "high"],
        default=None,
        help="Thinking level (Nano Banana only, default: minimal)"
    )
    parser.add_argument(
        "--quality", "-q",
        type=int,
        default=None,
        help="Output compression quality 1-100 (JPEG only)"
    )
    parser.add_argument(
        "--format", "-f",
        choices=["png", "jpeg"],
        default="png",
        help="Output image format (default: png)"
    )
    parser.add_argument(
        "--batch", "-b",
        type=int,
        choices=[1, 2, 3, 4],
        default=1,
        help="Generate multiple variations (1-4, default: 1)"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON (for agent consumption)"
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress output (MEDIA lines still printed)"
    )

    args = parser.parse_args()

    # Check API key early, before any output
    if not os.environ.get("GEMINI_API_KEY"):
        print("Error: GEMINI_API_KEY environment variable not set", file=sys.stderr)
        sys.exit(2)

    # Validate input count
    if args.inputs and len(args.inputs) > 14:
        print("Error: Maximum 14 input images allowed", file=sys.stderr)
        sys.exit(1)

    # Validate quality range
    if args.quality is not None:
        if args.quality < 1 or args.quality > 100:
            print("Error: --quality must be between 1 and 100", file=sys.stderr)
            sys.exit(1)
        if args.format != "jpeg":
            print("Warning: --quality only affects JPEG output, ignored for PNG", file=sys.stderr)

    # Validate model-specific parameters
    validate_model_params(args.model, args.aspect, args.resolution,
                          args.thinking, args.image_grounding)

    # Set up output path with correct extension
    output_path = Path(args.output)
    if args.format == "jpeg" and output_path.suffix.lower() not in (".jpg", ".jpeg"):
        output_path = output_path.with_suffix(".jpg")
    elif args.format == "png" and output_path.suffix.lower() != ".png":
        output_path = output_path.with_suffix(".png")

    if not output_path.parent.exists():
        output_path.parent.mkdir(parents=True, exist_ok=True)

    # Load input images if provided
    input_images = None
    input_paths = []
    if args.inputs:
        from PIL import Image
        input_images = []
        for img_path in args.inputs:
            try:
                img = Image.open(img_path)
                img.load()
                original_size = img.size
                img = optimize_image(img)
                input_images.append(img)
                input_paths.append(img_path)
                if not args.quiet:
                    if img.size != original_size:
                        print(f"Loaded: {img_path} ({original_size[0]}x{original_size[1]} → {img.size[0]}x{img.size[1]})")
                    else:
                        print(f"Loaded: {img_path} ({img.size[0]}x{img.size[1]})")
            except Exception as e:
                print(f"Error loading {img_path}: {e}", file=sys.stderr)
                sys.exit(1)

    # Auto-detect aspect ratio from base image (first input) if not specified
    if args.aspect:
        if not args.quiet:
            print(f"Aspect ratio: {args.aspect}")
    elif input_images:
        base_img = input_images[0]
        args.aspect = get_closest_aspect_ratio(base_img.width, base_img.height, model=args.model)
        if not args.quiet:
            print(f"Auto aspect ratio: {args.aspect} (from base image)")
    else:
        args.aspect = "1:1"
        if not args.quiet:
            print(f"Auto aspect ratio: {args.aspect} (default)")

    # Determine mode for display
    if not input_images:
        mode = "t2i (text-to-image)"
    elif len(input_images) == 1:
        mode = "i2i (image editing)"
    else:
        mode = f"multi-reference ({len(input_images)} images)"

    resolution = args.resolution or detect_resolution(input_images or [])
    model_display = MODEL_DISPLAY[args.model]

    if not args.quiet:
        print(f"Model: {model_display}")
        print(f"Mode: {mode}")
        print(f"Resolution: {resolution}")
        if args.grounding:
            print("Web search grounding: enabled")
        if args.image_grounding:
            print("Image search grounding: enabled")
        if args.thinking:
            print(f"Thinking: {args.thinking}")
        if args.batch > 1:
            print(f"Batch: {args.batch} images (async parallel)")

    # Generate output paths for batch
    if args.batch == 1:
        output_paths = [output_path]
    else:
        stem = output_path.stem
        suffix = output_path.suffix
        parent = output_path.parent
        output_paths = [parent / f"{stem}-{i}{suffix}" for i in range(1, args.batch + 1)]

    # Run async main
    results = asyncio.run(async_main(args, input_images, input_paths, output_paths))

    if not results:
        print("Error: No images were generated", file=sys.stderr)
        sys.exit(1)

    # Save single prompt log for all generated images
    log_path = output_path.with_suffix(".md")
    save_prompt_log(log_path, args.prompt, results,
                    input_paths if input_paths else None,
                    model=MODELS[args.model])
    if not args.quiet:
        print(f"\nPrompt log: {log_path.resolve()}")
        print(f"Generated {len(results)}/{args.batch} images")


if __name__ == "__main__":
    main()
