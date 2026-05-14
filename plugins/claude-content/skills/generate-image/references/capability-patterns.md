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

### Semantic Negative Prompts

Describe what to exclude using natural language rather than trying to specify only what you want.

```
"A professional headshot on a neutral gray backdrop.
 No distracting background elements, no visible logos or text,
 no harsh shadows on the face."
```

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

Use the reference block to label the image's role. Do not describe the garment's color, cut, or texture in the directive — those come from the reference image.

```
Image 1: Base scene
Image 2: Reference shirt

Replace only the shirt on the mannequin with the blouse from Image 2. Do not change anything else.
```

### Texture and Fabric

For premium fabric rendering, name the texture type without describing the color: "authentic linen texture with natural slub weave and organic drape." This gives the model rendering instructions while letting the reference image control color fidelity.

### Multi-Step Fashion Edits

When changing outfit plus accessories or garment plus signage, split into passes (see editing-guide.md "Multi-Pass Editing"). Common two-step patterns:
- Garment swap first, then sign/easel text edit
- Outfit replacement first, then accessory adjustment
- Subject compositing first, then pose refinement

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
