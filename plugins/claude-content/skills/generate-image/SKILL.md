---
name: generate-image
description: >
  This skill should be used when the user asks to "generate an image",
  "create a picture", "make me a logo", "edit this image", "combine
  these images", "make a sticker", "product mockup", "use nano banana",
  or any image creation/manipulation request. Covers t2i, i2i, and
  multi-reference composition. Not for HTML/CSS mockups, data
  visualizations, diagrams, or coded UI components. Make sure to use
  this skill whenever the user mentions images, pictures, logos, mockups,
  illustrations, or visual content — even if they don't explicitly say
  "generate" or "generate-image".
allowed-tools:
  - Bash(uv:*)
  - Read
  - AskUserQuestion
---

Requires `GEMINI_API_KEY` environment variable and `uv` package manager.

## Workflow

1. **Understand** — Determine mode (t2i, i2i, multi-reference), gather parameters (model, aspect ratio, resolution, output path). Exit: mode and parameters are clear.
2. **Craft prompt** — Apply the prompting principles below to write the prompt. For t2i, use narrative prose. For i2i/multi-reference, use directive grammar with reference blocks. Exit: prompt is written and follows the relevant checklist.
3. **Confirm** — Show the user the exact prompt, input images (if any), model, resolution, and aspect ratio. Ask for confirmation. Exit: user approves.
4. **Generate** — Run the script with confirmed parameters. Exit: images are saved and displayed.
5. **Iterate** — Present results. Offer refinements (prompt tweaks, parameter changes, follow-up edits). Exit: user is satisfied or moves on.

## Default Output & Logging

When the user doesn't specify a location, save images to:
```
~/Documents/generated images/
```

Every generated image gets a companion `.md` file with the prompt and model used (e.g., `logo.png` → `logo.md`).

When gathering parameters (aspect ratio, resolution), offer the option to specify a custom output location.

---

## Core Prompting Principle

Describe scenes narratively, not as keyword lists. Gemini's language model parses prose with full semantic understanding — narrative prompts encode spatial relationships, mood, and intent that comma-separated tags cannot express. Tag-style prompts lose compositional meaning and produce generic results.

```
Bad:  "cat, wizard hat, magical, fantasy, 4k, detailed"

Good: "A fluffy orange tabby sits regally on a velvet cushion, wearing an ornate
       purple wizard hat embroidered with silver stars. Soft candlelight illuminates
       the scene from the left. The mood is whimsical yet dignified."
```

A useful formula: `[Subject] doing [Action] in [Context]. [Camera/Composition]. [Lighting]. [Style]. [Constraint].` Not every prompt needs every element — match detail to intent. If the user has a specific vision, be prescriptive (exact descriptions); if exploring, be open (general direction, let the model decide details). Ask if unclear.

### Advanced Prompting Techniques

**Hyper-specificity**: Be precise about quantities, positions, and attributes. "Three red apples arranged in a triangle on a wooden table" outperforms "some apples on a table." Every vague word is a degree of freedom the model fills arbitrarily.

**Context and intent**: State the purpose. "A hero image for a coffee brand landing page" produces different results than "a photo of coffee" even if the visual subject is the same, because intent shapes composition, mood, and framing.

**Step-by-step instructions**: For complex scenes, break the prompt into sequential directives. "Start with a wide desert landscape. Place a lone figure walking left-to-right in the lower third. Behind them, a massive sandstorm approaches from the right."

**Semantic negative prompts**: State what to avoid using natural language. "No text overlays, no watermarks, no humans in the background" is more effective than trying to describe only what you want when exclusions matter.

**Camera control**: Specify shot type (extreme close-up, medium shot, aerial), lens (fisheye, telephoto), and camera angle (low angle, bird's eye, Dutch angle) to control framing precisely.

Editing with reference images follows different principles — see [references/editing-guide.md](references/editing-guide.md).

---

## Key Editing Principles

Editing prompts direct changes rather than describing scenes. Point to what the model can see; describe only what it cannot. Specify intentionally — every adjective, color word, or preservation clause beyond the minimum competes with the reference image and degrades fidelity. The reliable shape is a reference block, one Replace directive, and "Do not change anything else." — details in editing-guide.md.

Base image goes first in `--input` — it becomes Image 1 in the prompt. Gemini numbers images sequentially from input order. Reference block labels must match input order exactly.

Names invoke aesthetics directly — referencing "shot on Kodak Portra 400" produces its characteristic look more reliably than describing warm skin tones and pastel highlights.

---

## References

Load the relevant reference during prompt crafting (workflow step 2):

- [references/capability-patterns.md](references/capability-patterns.md) — mode-specific tips for photorealistic scenes, product photography, logos, stylized illustration, text rendering, and grounding
- [references/editing-guide.md](references/editing-guide.md) — edit grammar, reference blocks, directive structure, image ordering, semantic masking, character consistency
- [references/style-reference.md](references/style-reference.md) — named aesthetics lexicon (film stocks, cameras, studios, artists, movements)

---

## Configuration

### Model Selection

| | Nano Banana (default) | Nano Banana Pro |
|---|---|---|
| Speed | Fast, high-volume | Slower, higher quality |
| Resolutions | 0.5K, 1K, 2K, 4K | 1K, 2K, 4K |
| Extra ratios | 1:4, 4:1, 1:8, 8:1 | — |
| Thinking mode | Yes (minimal/low/medium/high) | No |
| Image search grounding | Yes | No |
| Max references | 14 | 11 (6 objects + 5 characters) |
| Text rendering | Advanced | Standard |

Default to Nano Banana for most requests. Use Nano Banana Pro when the user explicitly asks for maximum quality or when Nano Banana results need refinement.

### Aspect Ratios

**Both models**: 1:1, 2:3, 3:2, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9
**Nano Banana only**: 1:4, 4:1, 1:8, 8:1

### Resolutions
- **0.5K** (~512px) — fast preview (Nano Banana only)
- **1K** (~1024px) — default, fast
- **2K** (~2048px) — high quality
- **4K** (~4096px) — maximum detail

**Defaults**: 1K resolution, batch 1, aspect ratio auto-detected from base image (first input, or 1:1 if no images). Use 0.5K for quick previews and iteration (Nano Banana only). Use 2K for higher quality requests, 4K only when high detail is explicitly needed.

### Thinking Mode (Nano Banana only)

Nano Banana supports controllable thinking levels that improve complex prompt interpretation:
- **minimal** (default) — fastest, suitable for straightforward prompts
- **low/medium** — balanced reasoning for moderately complex scenes
- **high** — maximum reasoning for complex multi-element compositions, precise text rendering, or intricate spatial layouts

Use `--thinking high` when the prompt involves precise spatial relationships, multiple text elements, or detailed composition requirements. For i2i editing, thinking mode also helps with multi-reference composition (3+ images), precise text/sign placement on existing scenes, and complex spatial edits where element positioning matters.

---

## Script Usage

One unified script handles all modes: t2i, i2i, and multi-reference composition. Nano Banana is the default model.

```bash
# Text-to-image (t2i) — uses Nano Banana by default
uv run ${CLAUDE_PLUGIN_ROOT}/skills/generate-image/scripts/generate.py --prompt "A serene mountain lake at dawn" --output landscape.png

# Nano Banana Pro model
uv run ${CLAUDE_PLUGIN_ROOT}/skills/generate-image/scripts/generate.py --prompt "A serene mountain lake at dawn" --output landscape.png --model pro

# Image-to-image editing (i2i)
uv run ${CLAUDE_PLUGIN_ROOT}/skills/generate-image/scripts/generate.py --prompt "Make it sunset colors" --input photo.png --output edited.png

# Multi-reference composition
uv run ${CLAUDE_PLUGIN_ROOT}/skills/generate-image/scripts/generate.py --prompt "Combine the cat from image 1 with the background from image 2" --input cat.png --input background.png --output composite.png

# With options (aspect ratio, resolution, thinking, batch, grounding, format)
uv run ${CLAUDE_PLUGIN_ROOT}/skills/generate-image/scripts/generate.py --prompt "Logo for 'Acme Corp'" --output logo.png --aspect 1:1 --resolution 2K --thinking high
```

### Script Options

| Flag | Short | Description |
|------|-------|-------------|
| `--prompt` | `-p` | Image description or edit instruction (required) |
| `--output` | `-o` | Output file path (required) |
| `--input` | `-i` | Input image(s) for editing/composition (repeatable, up to 14) |
| `--model` | `-m` | Model: nano-banana (default) or pro |
| `--aspect` | `-a` | Aspect ratio (auto-detects from base image / first input, or 1:1) |
| `--resolution` | `-r` | Output resolution: 0.5K, 1K, 2K, or 4K (default: auto-detect or 1K) |
| `--grounding` | `-g` | Enable Google Search web grounding |
| `--image-grounding` | | Enable image search grounding (Nano Banana only, use with --grounding) |
| `--thinking` | `-t` | Thinking level: minimal, low, medium, high (Nano Banana only) |
| `--quality` | `-q` | Output compression quality 1-100 (JPEG only) |
| `--format` | `-f` | Output format: png (default) or jpeg |
| `--batch` | `-b` | Generate multiple variations: 1-4 (default: 1) |
| `--json` | | Output results as JSON for agent consumption |
| `--quiet` | | Suppress progress output (MEDIA lines still printed) |

The script auto-detects resolution and aspect ratio from input images when flags are omitted, and automatically resizes large inputs (>2048px) before sending to the API.

---

## Pre-Generation Checklist

**Before generating (t2i):**
- [ ] Narrative description (not keyword list)?
- [ ] Camera/lighting details for photorealism?
- [ ] Text in quotes, font style described?
- [ ] Aspect ratio appropriate for use case?
- [ ] Model choice appropriate? (Nano Banana default; Nano Banana Pro for max quality)
- [ ] Thinking level set for complex prompts? (Nano Banana only)

**Before editing (i2i / multi-reference):**
- [ ] Reference block at start of prompt labeling each image's role?
- [ ] Prompt directs rather than describes?
- [ ] Minimal directive pattern? (Reference block + one Replace directive + "Do not change anything else." — no decorative preservation clauses)
- [ ] Base image is first in `--input` list (Image 1)?
- [ ] Prompt image labels match input order? (base = Image 1 = first input, references numbered after — mislabeled roles cause character drift)
- [ ] When extracting/transferring elements: explicitly named each element rather than generic "outfit/object from image X"?
- [ ] No color labels competing with reference image? (color words override visual reference — see editing-guide)
- [ ] Base image has minimal accessories that could contaminate? (bags, hats, sunglasses bleed into output)
- [ ] Only one change per prompt? (split competing directives into sequential passes)
- [ ] Reference count within model limits? (Nano Banana: 14, Nano Banana Pro: 11)
