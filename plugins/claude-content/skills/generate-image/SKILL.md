---
name: generate-image
description: >
  Use for any image creation or editing request — logo, sticker, product
  mockup, nano banana, t2i, i2i, multi-reference compositing via generate.py.
  Not for HTML/CSS mockups, diagrams, or coded UI.
allowed-tools:
  - Bash(uv:*)
  - Read
  - AskUserQuestion
---

Requires `GEMINI_API_KEY` environment variable and `uv` package manager.

## Workflow

1. **Understand** — Determine mode (t2i, i2i, multi-reference), gather parameters (model, aspect ratio, resolution, output path). If the prompt requires precise execution (specific pose, asymmetric framing, exact crop), default to `--batch 3` or `--batch 4` and surface this to the user — image generation is stochastic and precise directives hit ~50% per seed. Exit: mode, parameters, and batch size are clear.
2. **Craft prompt** — Default to the minimal prompt that can carry the intent: t2i uses narrative prose; i2i/multi-reference uses a reference block plus the minimal directive. Apply the **Core** checklist (always, for the matching mode). Reach into the **Escalation toolkit** only on a known-hard signature — a detail/geometry-fidelity shot — or after a batch shows drift; then add only the specific lock for the attribute that is drifting, not the whole kit. Over-constraining a simple edit degrades it as surely as under-specifying a complex one. Exit: prompt written, Core items satisfied, escalation tools added only where a signature or observed drift justifies them.
3. **Confirm** — Show the user the exact prompt, input images (if any), model, resolution, aspect ratio, and batch size. Ask for confirmation. Exit: user approves.
4. **Generate** — Run the script with confirmed parameters. Exit: images are saved and displayed.
5. **Iterate** — Present results and evaluate against intent before offering refinements. Evaluation order by mode: **t2i** — subject correctness, composition, style fidelity. **i2i edit** — the changed element looks right, nothing else changed. **Multi-reference composition** — the primary transferred attribute matches its source reference FIRST (for a detail shot, the construction geometry — width, edge shape, count, angle), secondary consistency (identity, environment) holds SECOND, staging (lighting, composition, framing) THIRD. Decide what's primary per task. Cherry-pick the winning frame from the batch rather than re-prompting for consistency past ~75%. Exit: user is satisfied or moves on.

## Default Output & Logging

When the user doesn't specify a location, save images to:
```
~/Documents/generated images/
```

Every generated image gets a companion `.md` file with the prompt and model used (e.g., `logo.png` → `logo.md`).

When gathering parameters (aspect ratio, resolution), offer the option to specify a custom output location.

---

## Core Prompting Principle

**Describe scenes narratively, not as keyword lists.** Gemini's language model parses prose with full semantic understanding — narrative prompts encode spatial relationships, mood, and intent that comma-separated tags cannot express. Tag-style prompts lose compositional meaning and produce generic results.

```
Bad:  "cat, wizard hat, magical, fantasy, 4k, detailed"

Good: "A fluffy orange tabby sits regally on a velvet cushion, wearing an ornate
       purple wizard hat embroidered with silver stars. Soft candlelight illuminates
       the scene from the left. The mood is whimsical yet dignified."
```

**Describe positively, never via negation.** Every concept named in a prompt biases the output toward that concept — even when preceded by "not", "no", or "do not". Diffusion models condition on tokens regardless of polarity. To exclude X, either (a) name a positive alternative that fills the same role, or (b) scope the prompt so X has no place to land.

```
Bad:   "A clean studio backdrop. No warm tones, no cream, no beige, no tan."
Good:  "A clean cool-neutral gray studio backdrop with subtle blue undertones."

Bad:   "A headshot with no harsh shadows on the face, no distracting background."
Good:  "A headshot on a clean neutral gray backdrop, even soft frontal fill light
        that flatters the face."
```

This rule applies everywhere in the skill — t2i prompts, i2i directives, reference role descriptions, and framing instructions.

**Name sources explicitly — leave no ambiguity in references.** Every element in the prompt should trace to a specific source: "the man from Image 2" not "this man"; "the shirt from Image 3" not "the shirt". Ambiguous references bind to whichever source the model weights most, which is never reliably the right one. This isn't about over-describing — don't re-describe what the reference already shows. It's about making each reference point to exactly one source.

A useful formula: `[Subject] doing [Action] in [Context]. [Camera/Composition]. [Lighting]. [Style]. [Constraint].` Not every prompt needs every element — match detail to intent. If the user has a specific vision, be prescriptive (exact descriptions); if exploring, be open (general direction, let the model decide details). Ask if unclear.

### Advanced Prompting Techniques

**Hyper-specificity**: Be precise about quantities, positions, and attributes. "Three red apples arranged in a triangle on a wooden table" outperforms "some apples on a table." Every vague word is a degree of freedom the model fills arbitrarily.

**Context and intent**: State the purpose. "A hero image for a coffee brand landing page" produces different results than "a photo of coffee" even if the visual subject is the same, because intent shapes composition, mood, and framing.

**Step-by-step instructions**: For complex scenes, break the prompt into sequential directives. "Start with a wide desert landscape. Place a lone figure walking left-to-right in the lower third. Behind them, a massive sandstorm approaches from the right."

**Exclusion via positive constraint**: When something must be absent from the output, do not name it under a negation. Either name a positive alternative ("clean unbranded surface" instead of "no logos") or scope the scene so the unwanted element has no place to land ("a closed laptop on the desk" makes a screen impossible to render). Naming X under "no X" makes X more likely, not less.

**Camera control**: Specify shot type (extreme close-up, medium shot, aerial), lens (fisheye, telephoto), and camera angle (low angle, bird's eye, Dutch angle) to control framing precisely.

Editing with reference images follows different principles — see [references/editing-guide.md](references/editing-guide.md).

---

## Key Editing Principles

Editing prompts direct changes rather than describing scenes. Point to what the model can see; describe only what it cannot. Specify intentionally — every adjective, color word, or preservation clause beyond the minimum competes with the reference image and degrades fidelity. The reliable shape is a reference block plus one Replace directive — the verb's implicit scope handles preservation, no stop clause needed. Details in editing-guide.md.

For multi-reference work (3+ images), use per-reference role assignment: one sentence that assigns each reference its specific contribution ("the facade from Image 2; the car from Image 3; the sky and lighting from Image 1") — see editing-guide.md "Per-Reference Role Assignment".

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

Core items are the floor — apply them to every prompt of the matching mode. The Escalation toolkit is opt-in: skip it entirely for simple t2i and single-element edits. Reach in only on a known-hard signature (a detail/geometry-fidelity shot) or after a batch shows drift — and then add only the lock for the attribute that is actually drifting. Each added constraint costs fidelity on everything else, so escalation scales with how many independent things can drift, not with how ambitious the prompt is.

### Core — t2i (always)
- [ ] Narrative description (not keyword list)?
- [ ] Positive framing throughout — no "no X" / "not X" / "do not X" clauses anywhere in the prompt?
- [ ] Camera/lighting details for photorealism?
- [ ] Text in quotes, font style described? (if the image has text)
- [ ] Aspect ratio appropriate for use case?
- [ ] Model choice appropriate? (Nano Banana default; Nano Banana Pro for max quality)
- [ ] Thinking level set for complex prompts? (Nano Banana only)
- [ ] Batch size matches precision needs? (`--batch 3` or `--batch 4` for precise pose / framing / asymmetric directives)

### Core — i2i / multi-reference (always)
- [ ] Reference block at start of prompt labeling each image's role?
- [ ] Reference roles positive-only — lists what to USE from each ref, never what to ignore? (see editing-guide.md "Reference Block")
- [ ] Minimal directive pattern? (Reference block + one Replace directive — no "do not change anything else" stop clause, no decorative preservation clauses)
- [ ] Positive framing throughout the directive? (no negation anywhere, including locks and composition clauses)
- [ ] Base image first in `--input` (Image 1), and prompt labels match input order? (mislabeled roles cause character drift)
- [ ] Only one change per prompt? (split competing directives into sequential passes)
- [ ] When extracting/transferring elements: explicitly named each element rather than generic "outfit/object from image X"?
- [ ] One reference carrying an element you need to preserve while another reference could compete with it? → add `CRITICAL — [element]` proactively: assign the source reference explicitly ("Image 1 is the sole identity source. Image 2 is scoped to [attribute] only."). Do not wait for drift — role competition defeats text directives silently.
- [ ] No color labels competing with reference image? (color words override visual reference — see editing-guide)
- [ ] Base image has minimal accessories that could contaminate? (bags, hats, sunglasses bleed into output)
- [ ] Reference count within model limits? (Nano Banana: 14, Nano Banana Pro: 11)
- [ ] For 3+ references: per-reference role-assignment sentence? (one sentence assigning each image its contribution — see editing-guide.md "Per-Reference Role Assignment")
- [ ] For photorealistic human shots: skin & finish locked? (the one near-universal CRITICAL section — generative skin drifts to plastic/retouched)

### Escalation toolkit — reach for only on a hard signature or observed drift
- [ ] An attribute (identity, lighting/color, orientation, drape) drifting across the batch? Lock that *specific* attribute in its own `## CRITICAL —` section, positively phrased — without over-constraining the stable ones. (see editing-guide.md "Constraint Locking with CRITICAL Sections")
- [ ] Detail shot whose construction won't hold? Geometry lock via per-attribute enumeration, not generic "match exactly". (see capability-patterns.md "Geometry Lock for Detail Shots")
- [ ] Nano Banana + `--thinking high` with 3+ references and weak adherence? Add the inventory preamble. ("Silently inventory the design-critical details: ...")
- [ ] Follow-up shot from the same set? Continuity assertion. ("from the same set as Image N: same subject, same setting, same light" — see editing-guide.md "Continuity Assertion")
- [ ] Campaign with a locked hero image? Collapse to two-input form — hero as bundle-source + the single new-attribute reference. (see editing-guide.md "Single-Reference Collapse")
