# Editing & Composition Guide

Load this reference when the user provides input images for editing or multi-reference composition. These principles do not apply to text-to-image generation.

---

## Core Principle

Connect the dots, don't describe. Reference images provide the visual context. The prompt's job is to tell the model what goes where — not re-describe what the model can already see. Over-describing creates competing signals between text and image that degrade output quality.

```
Bad:  "Replace the front figure with a woman who has light fair skin, delicate oval
       face, dark sunglasses, dark brown headscarf, sleeveless beige linen dress..."

Good: "Replace the front figure with the woman from image 3"
```

### The Pointing vs Naming Boundary

Point to reference images for elements that will be used whole (a face, a background, a scene). Explicitly name elements when extracting or transferring parts from a reference to a different context, because generic references like "outfit from image 2" don't tell the model which visual elements to isolate from surrounding context.

```
Bad:  "Replace the clothing with the outfit from image 2"
Good: "Replace the clothing with the white linen button-up shirt and camel
       brown wide-leg trousers"
```

---

## The Edit Grammar

### Reference Block

Start multi-image edit prompts with a reference block that explicitly labels what each image represents. This disambiguates image roles before the directive.

```
Image 1: [base — canvas being edited]
Image 2: [reference role/description]
Image 3: [reference role/description]

[Main directive]
```

Example:
```
Image 1: Base scene - woman on street to preserve
Image 2: Reference outfit - white linen shirt and camel trousers

Replace only the clothing with the white linen shirt and camel trousers.
Keep her exact pose, facial features, and the street background unchanged.
```

### Three-Sentence Directive Structure

Each sentence has one job. Compounding directives into a single sentence dilutes each one's weight.

1. **Replace** (scope) — what changes
2. **Match** (constraint) — how new elements behave
3. **Keep** (preservation) — what stays

### Language Patterns

Use directive verbs ("replace", "match", "keep") not passive phrasing ("should be", "could be"). Directives produce stronger adherence.

"Replace" anchors to the base scene (model edits in-place). "Change" allows full recomposition (model may discard the scene). Always prefer "replace" for in-place edits.

"Only" after the verb constrains scope: "replace only the front figure" is tighter than "replace the front figure."

Sentence order affects spatial placement. The element mentioned last in a spatial assignment tends to get placed in the more prominent position.

---

## Image Ordering & Numbering

Base image (the canvas being edited) goes **first** in the `--input` list — it becomes Image 1 in the prompt, the most natural labeling. Reference images follow in order:

```
--input base.jpg     -> Image 1 in prompt
--input ref_a.jpg    -> Image 2 in prompt
--input ref_b.jpg    -> Image 3 in prompt
```

Aspect-ratio auto-detection reads from the first input (the base), so the output matches your canvas without needing `--aspect`. Label your reference block to match input order exactly — mismatched labels cause role confusion and character drift even when the directive is otherwise correct.

---

## Multi-Image Composition

Reference images provide visual context — the prompt connects them. Point to images by number, assign elements to positions, and describe only what the model cannot infer from the images themselves. Nano Banana supports up to 14 reference images; Nano Banana Pro supports up to 11 (6 objects + 5 character references).

---

## Character Consistency

- Use follow-up edits for multiple views of the same character
- Reference distinctive features explicitly in follow-ups
- Include "exact same character" or "maintain all design details"
- Save successful designs as reference for future prompts

---

## Semantic Masking

No manual masking needed. Language creates the edit boundary — name the element to define the mask, specify the replacement, constrain the scope:

```
"Using the provided image of a living room, change only the blue sofa
to be a vintage, brown leather chesterfield sofa. Keep the rest of the room,
including the pillows on the sofa and the lighting, unchanged."
```

"Only" defines scope. The element name ("the blue sofa") defines the mask region. The preservation clause ("keep the rest...unchanged") protects everything else.

---

## Editing Failure Modes

Common ways i2i edits fail and how to avoid them. These patterns emerged from real production campaigns and apply to any editing workflow.

### Color Labels Override Visual References

Every color word in an editing prompt creates a competing signal against the reference image. If you name a color ("rust", "terracotta", "olive green"), the model generates the text-defined tone rather than the actual shade visible in the reference. This happens because the model resolves text-vs-image conflicts by blending both signals — the result matches neither.

Remove all color words from editing directives. The reference block labels the image's role ("Reference shirt — women's linen blouse"); the directive says what to change ("Replace only the shirt with the blouse from the reference"). The reference image itself is the color spec.

This applies to any visual attribute already present in the reference: color, cut, texture, proportion. Naming these in text creates drift. If you want something replicated exactly, don't describe it — let the image be the sole authority.

### High-Contrast Swap Targets

When replacing an element with something of a similar color (olive shirt → olive shirt of different cut), the model can't distinguish source from target and produces near-identical output. The fix is to choose a base image where the element being replaced is a contrasting color — e.g., use a white shirt base for an olive shirt swap. The high contrast gives the model an unambiguous replacement target.

### Gemini Cannot Re-Frame

Gemini cannot execute virtual camera moves. Prompts like "zoom in on the storefront," "show this from a closer angle," or "crop to a tighter shot" will either reproduce the original composition or generate an inconsistent scene — they will not produce a re-framed version of the same content.

Always edit on a base image that already has the target angle, framing, and composition. If you need a closer shot, find or extract a frame at that angle rather than trying to prompt a re-frame.

### Multi-Pass Editing

The three-sentence directive structure (replace / match / keep) works for a single change. When a prompt has two competing changes — garment swap plus sign text, outfit plus accessories, subject plus background — the model compromises on one.

Split into sequential passes: one change per generation call. Pattern for element replacement with correct proportions:

1. **Pass 1**: Remove the element entirely (e.g., "Remove the price sign and easel from the scene")
2. **Pass 2**: Re-add it using a visual reference (e.g., "Add the exact price sign and easel from image 2 to the right side of the shelf")

This remove-then-re-add approach is specifically important for text and sign elements, where text-only swaps change the words but distort the element's proportions and positioning.

### Base Scene Contamination

Accessories and distinctive elements in the base image bleed into garment-swap outputs. If the base scene has a beret, the generated outfit may include a beret. If the base scene has sunglasses, they appear on the output model. Similarly, outfit references shot on plain studio backgrounds can override the base scene's location background — the gray studio backdrop replaces the street.

When choosing a base image for garment editing:
- Prefer images with minimal distinctive accessories
- Avoid bases where the model wears items (bags, hats, jewelry) you don't want in the output
- If using outfit references from studio shoots, verify the output preserves the base scene's environment
