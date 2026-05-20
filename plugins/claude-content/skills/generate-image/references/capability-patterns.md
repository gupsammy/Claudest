# Capability Patterns

Mode-specific prompting tips. Load the relevant section during prompt crafting (workflow step 2).

---

## Photorealistic Scenes

Think like a photographer: describe lens, light, moment.

- Specify camera (85mm portrait, 24mm wide), aperture (f/1.8 bokeh, f/11 sharp throughout)
- Describe lighting direction and quality (golden hour from camera-left, three-point softbox)
- Include mood and format (serene, vertical portrait)

## Product Photography

- Isolation: Clean white backdrop, soft even lighting, e-commerce ready
- Lifestyle: Product in use context, natural setting, aspirational but authentic
- Hero shots: Cinematic framing, dramatic lighting, space for text overlay

## Logos & Text

- Put text in quotes: `'Morning Brew Coffee Co'`
- Describe typography: "clean bold sans-serif with generous letter-spacing"
- Specify color scheme, shape constraints, design intent
- Iterate with follow-up edits for refinement

## Stylized Illustration

- Name the style: "kawaii-style sticker", "anime-influenced", "vintage travel poster"
- Describe design language: "bold outlines, flat colors, cel-shading"
- Include format constraints: "white background", "die-cut sticker format"

## Text Rendering

Nano Banana has advanced text rendering capabilities. For best results:
- Put all text in single quotes within the prompt
- Describe font characteristics: weight, style, size relative to the image
- Specify text placement: "centered at the top," "bottom-right corner"
- For multiple text elements, describe each separately with position
- Use `--thinking high` for complex multi-line text or precise typography

## Google Search Grounding

Enable with `--grounding` flag when real-time data helps (weather visualizations, current events infographics, real-world data charts).

**Image search grounding** (Nano Banana only): Add `--image-grounding` alongside `--grounding` to enable image search results as additional visual context. Useful when the model needs to reference real-world visuals (product designs, architectural styles, specific locations).

---

## Best Practices

### Hyper-Specificity

Vague prompts produce generic results. Every unspecified attribute becomes a random variable.

```
Vague:    "A woman in a park"
Specific: "A 30-year-old woman with shoulder-length auburn hair sits cross-legged
           on a green wool blanket in a sun-dappled oak grove, reading a hardcover
           book. Late afternoon golden hour, shallow depth of field at f/2.0."
```

Quantities, colors, materials, spatial positions, and named objects all reduce variance.

### Context & Intent

State what the image is for. Purpose shapes composition, mood, and framing decisions.

```
Generic:     "A flat white coffee on a marble counter"
With intent: "A hero image for an artisan coffee brand's homepage — a flat white
              in a handmade ceramic cup on a marble counter, steam rising, soft
              morning light from the left, negative space on the right for text overlay"
```

### Step-by-Step Instructions

Complex scenes benefit from sequential directives rather than a single compound sentence.

```
"Start with a wide establishing shot of a misty fjord at dawn.
 In the foreground, place a wooden dock extending from the lower left.
 A small red sailboat is moored at the dock's end.
 Mountains fill the background, their peaks just catching the first golden light.
 The water is perfectly still, creating mirror reflections."
```

### Positive Framing for Exclusions

Naming a concept under negation ("no X", "not X") biases the output toward X — diffusion models condition on tokens regardless of polarity. To exclude something, name a positive alternative that fills the same role, or scope the scene so the unwanted element is physically not there.

```
Bad:   "A professional headshot on a neutral gray backdrop.
        No distracting background elements, no visible logos or text,
        no harsh shadows on the face."

Good:  "A professional headshot on a clean seamless gray backdrop,
        even soft frontal fill light that flatters the face, the
        wall-to-floor falloff smooth and uncluttered."
```

The Good version states what's there, not what isn't. "Clean seamless" implies absence of distraction. "Even soft frontal fill" implies absence of harsh shadows. The model never has to suppress a named concept.

### Camera Control

Photographic terms give precise control over framing and perspective.

- **Shot types**: extreme close-up, close-up, medium shot, full shot, wide shot, extreme wide shot
- **Angles**: eye level, low angle (heroic), high angle (diminishing), bird's eye, worm's eye, Dutch angle
- **Lenses**: fisheye (distortion), wide-angle (expansive), normal 50mm (natural), telephoto (compression), macro (tiny subjects)
- **Movement metaphors**: "tracking shot following the subject," "slow dolly-in," "crane shot rising above"

---

## Fashion & Garment Editing

Garment swaps and fashion compositing require specific techniques beyond generic i2i editing.

### Base Image Selection

The base image matters as much as the prompt. Choose bases where:
- The garment being replaced is a **contrasting color** to the target (white base → olive swap, not olive → olive)
- The model/mannequin has **minimal accessories** (no bags, berets, sunglasses that bleed into output)
- The composition already has the **target framing** (Gemini cannot re-frame — see editing-guide.md)

### Garment Swap Prompts

Use the reference block to label the image's role. Let the reference image carry color, cut, and texture — naming those attributes in the directive creates competing signals against the reference pixels.

```
Image 1: Base scene
Image 2: Reference shirt

Replace only the shirt on the mannequin with the blouse from Image 2.
```

No stop clause — `Replace` already scopes the edit in place. See editing-guide.md "Minimal Directive Pattern".

### Texture and Fabric

For premium fabric rendering, name the texture type without describing the color: "authentic linen texture with natural slub weave and organic drape." This gives the model rendering instructions while letting the reference image control color fidelity.

### Multi-Step Fashion Edits

When changing outfit plus accessories or garment plus signage, split into passes (see editing-guide.md "Multi-Pass Editing"). Common two-step patterns:
- Garment swap first, then sign/easel text edit
- Outfit replacement first, then accessory adjustment
- Subject compositing first, then pose refinement

### Multi-Reference Fashion Directive

The fashion instance of Per-Reference Role Assignment (editing-guide.md). When composing a full look from a face reference, separate garment references, and a lighting/backdrop plate, assign each reference its contribution in one positive sentence:

```
Perfectly replicate the exact features of the man's face from Image 2; the exact
shirt, trouser, belt, and shoe construction and color from Image 3; the cuff and
collar construction from Images 4 and 5; the cotton weave and sheen from Image 6;
and the lighting, backdrop, and photographic finish from Image 1.
```

Each image gets a positively-named job, attributes are enumerated per image (not "the outfit"), and replication verbs ("perfectly replicate", "exact") carry positive intensity with zero negation. For the structural breakdown, see editing-guide.md "Per-Reference Role Assignment".

### Detail Shots (fashion instance of Single-Reference Collapse)

This is the fashion application of Single-Reference Collapse (editing-guide.md). Once a hero image is locked for a campaign, every follow-up detail shot collapses to two-input form: the hero PNG as Image 1 (in this campaign the hero front shot was the bundle-source — it encoded the model's identity, the studio lighting, color science, backdrop, and wardrobe color), plus the single raw construction reference for the detail being shown (cuff macro, collar close-up, weave swatch). The character sheet and the separate lighting reference get dropped — the hero already carries what they contributed.

```
Inputs (order matters — base first):
  Image 1: final/<colorway>/front.png        ← bundle-source for everything locked this campaign
  Image 2: raw/<colorway>/Cuff.jpg           ← scoped to the construction detail being shown

Settings: --model nano-banana --thinking high --resolution 2K --batch 3 --aspect 3:4
Expected yield: 2/4 keepers (detail crops are simpler than full-body)
```

Which reference is the bundle-source is a per-campaign choice, not a fixed rule — here it was the hero front shot. This two-input form outperformed the original six-reference setup for detail shots because there were fewer competing signals. Anchor the detail shot to the bundle-source with a continuity assertion (editing-guide.md) and lock the construction with geometry enumeration (below).

### Geometry Lock for Detail Shots

The fashion instance of Geometry Enumeration (editing-guide.md). Generic "match Image 2 exactly" does not preserve specific geometric attributes — the model interprets "exactly" aspirationally and width, edge shape, button count, and point spread all drift seed-to-seed. To lock garment geometry, enumerate each attribute explicitly in a dedicated `CRITICAL — Geometry match` section, named positively.

Canonical attributes by garment region:

| Region | Attributes to enumerate |
|---|---|
| **Cuff** | Width relative to wrist (e.g., 1.4–1.5x wrist circumference), edge shape (horizontal straight perpendicular to sleeve, sharp 90° corners), button count and placement (one at wrist edge, one on gauntlet placket above), topstitching gauge |
| **Collar** | Type (point / spread / cutaway / band / mandarin — name the one you want), point length (short / moderate / long), spread angle in degrees, stand height, top button position (visible at base of stand when fastened), placket type |
| **Placket** | Type (clean front / button-band / hidden), topstitching style (single-needle / double-needle), button count and spacing |
| **Sleeve** | Length (at the wrist / quarter-inch above / above the watch), drape (relaxed natural fold / pressed flat) |

Phrase the attributes in positive form. "Sharp 90° corners" not "not rounded". "Single button at the wrist edge" not "no double cuff". The rule from the Core Prompting Principle applies: every concept named under negation biases toward that concept.

### Reference Orientation Lock

The fashion instance of scope completeness (editing-guide.md "Reference Block"). Scoping a reference to "construction only" can strip too much — Gemini drops the arm rotation, body angle, or camera direction that came baked into the reference's framing. The fix is to scope the reference to multiple positive attributes: construction AND orientation. Example for a back-of-wrist cuff shot:

```
Image 2 — Cuff construction and orientation reference. Silently inventory:
- The exact cuff geometry: [enumerated attributes above]
- The arm orientation: the model's torso is rotated so the back of the arm,
  the back of the wrist, and the back of the hand face the camera. The cuff
  button visible to camera sits on the outside of the wrist.
```

Two positively-named contributions from one reference. The directive sentence that follows must echo both: "The cuff construction and the back-of-wrist orientation come from Image 2."

---

## Working from Video References

When using reference videos as starting points for image generation (e.g., adapting an existing ad concept):

### Frame Extraction

Use a two-pass approach with ffmpeg:

1. **Scene detection** — Find transition timestamps:
```bash
ffmpeg -i input.mp4 -vf "select='gt(scene,THRESHOLD)',showinfo" -vsync vfr -f null - 2>&1 | grep "pts_time"
```

2. **Targeted extraction** — Extract a single frame at a specific timestamp:
```bash
ffmpeg -y -ss <TIMESTAMP> -i input.mp4 -frames:v 1 -update 1 output.png
```

Start with threshold 0.3 and lower to 0.15 if too few frames are detected. Fashion videos with smooth transitions (car wipes, camera pans) typically need the lower end.

### Key Considerations

- Scene detection fires on visual composition changes, not semantic content changes. In videos where transitions are masked by passing objects, scene detection catches the transition itself, not the clean reveal after it. A second probe pass between detected timestamps is necessary.
- Always check `ffprobe` metadata first (`ffprobe -v quiet -print_format json -show_format -show_streams`) to understand resolution, fps, and duration.
- Name extracted frames descriptively (e.g., `outfit_1_blue_denim.png`) rather than by frame number — self-documenting folders save time during editing.
