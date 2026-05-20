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
Bad:    "Replace the clothing with the outfit from image 2"
Better: "Replace the clothing with the linen button-up shirt and wide-leg trousers from image 2"
```

Name structural attributes (garment type, cut, fabric) when extracting parts from a reference — but never colors. Structural names tell the model which elements to isolate from the reference's surrounding context; colors belong to the reference image's pixels (see *Color Labels Override Visual References* below).

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

Roles stay short — one phrase. Don't enumerate what the reference image contains in the label ("Image 2: Reference outfit — white linen blouse and camel trousers"). That re-describes what the pixels already show; the redundancy creates competing signals against the reference.

**Scope references positively, never via exclusion.** Phrases like "ignore the person, pose, face, and background" or "the model in this image is not retained" name the very elements you want the model to disregard, pulling them back into attention. Instead, list only what to *use* from each reference. The model treats anything you don't name as ambient context for the named contribution — no suppression required.

```
Bad:    "Image 2: Reference for outfit. Ignore the person, pose, face, and background."
Good:   "Image 2: Use only for the shirt — collar, button placket, chest pocket, fabric weave."

Bad:    "Image 5: Collar reference. The face visible at the top is NOT the character."
Good:   "Image 5: Use only for the collar construction — point shape, spread angle, stand height."
```

The rule: list what to use; never list what to disregard. If a reference contains a contaminant (a face, a hat, a backdrop) the user doesn't want in the output, the way to suppress it is to specify the positive replacement elsewhere ("the identity comes from Image 1") and let the named-use scoping carry the rest.

**Scope to *all* the positive attributes you want — narrow scoping strips co-baked ones.** Scoping a reference to a single attribute ("use only for the silhouette") can drop other attributes baked into that reference's framing: the camera angle, the orientation, the lighting direction it happened to be shot at. If you need those too, name them — "use Image 2 for the silhouette and the 3/4 front camera angle". Two positive contributions from one reference, both explicit. See capability-patterns.md "Reference Orientation Lock" for a worked instance.

### Minimal Directive Pattern

The whole pattern:

```
[Reference block]

Replace [scope] with [pointer to reference].
```

One directive that points to the change. The verb's implicit scope carries the preservation work — `Replace` edits in place by definition, so everything else stays. No stop clause needed.

Example:

```
Image 1: Base photograph to edit
Image 2: Character and wardrobe reference

Replace the person in Image 1 with the woman from Image 2, wearing her exact outfit from Image 2.
```

The directive's job is to **connect the dots** between images — name what's being swapped, point to where the replacement comes from. Add clauses beyond this only when the model is observably dropping a specific element you need to keep; in that case name only that single element in positive form ("Keep the seamless gray backdrop intact"), then stop.

**Why minimal beats verbose.** Every adjective, color word, or preservation enumeration is a degree of freedom the model reconciles against the reference image pixels. Text-vs-image conflicts get resolved by blending both signals — the result matches neither. Over-specifying what the reference already shows actively degrades fidelity. Add detail only when the model cannot infer it from the references and you have a specific outcome in mind for it.

**No "do not change anything else" stop clause.** Earlier versions of this skill used `Do not change anything else.` as the keystone of the minimal directive. Production evidence showed that clause activates the *concept* of changing other things — same negation-biases-toward-the-concept mechanism the Core Prompting Principle warns about. The verb's implicit scope is sufficient; the stop clause is gratuitous and counterproductive.

**Verb choice.**
- **Replace** anchors to the base scene (model edits in place). Use this for in-place edits.
- **Change** allows full recomposition — use only when you want the model to consider discarding the scene.

"Only" after the verb tightens scope: "replace only the front figure" beats "replace the front figure." Sentence order affects spatial placement — the element mentioned last in a spatial assignment tends to land in the more prominent position.

### Per-Reference Role Assignment

For prompts with three or more references, a single positively-framed directive sentence outperforms a bulleted role list. The pattern: one sentence that assigns each reference its specific contribution, enumerating attributes positively, using replication verbs.

Canonical template:

```
Perfectly replicate the exact [element] from Image [N]; the exact [element]
from Image [M]; the [element] from Images [P and Q]; and the [environment,
lighting, and finish] from Image [S].
```

Worked example (scene composite):

```
Perfectly replicate the exact building facade and signage from Image 2; the
parked vintage car from Image 3; the storefront awning from Image 4; and the
dusk sky, street lighting, and color grade from Image 1.
```

What makes this work, structurally:

- **Per-reference role assignment** — every image gets a positively-named job, no ambiguity about which signal goes where
- **Enumerated attributes per image** — "facade + signage" not "the building"; name the specific parts, not the whole
- **Replication verbs** — "perfectly replicate", "exact" — positive intensifiers, zero negation
- **Single coherent sentence** — one directive carries the whole multi-ref assignment, easier for the model to parse than a bulleted block

When the prompt has 3+ references, default to this form. For a fashion worked example (face + garment construction + fabric + lighting across six references), see capability-patterns.md "Multi-Reference Fashion Directive".

## Inventory Preamble (Nano Banana + thinking high)

For multi-reference composition with Nano Banana at `--thinking high`, prefix each reference's role with a "silently inventory" instruction. This forces Gemini's auto-regressive head to reason about each reference's design-critical details before diffusion activates — lifting adherence to the attributes you've assigned each reference measurably.

When to use:
- Multi-reference composition with 3+ references
- Nano Banana + `--thinking high` only (Pro has a fixed reasoning budget; no benefit)
- Skip for single-reference edits (overkill) and t2i (irrelevant)

Pattern:

```
## Reference inventory — analyze silently before generation

Image 1 — [Role: the bundle-source for X, Y, Z]. Silently inventory:
[the specific attributes this reference owns — enumerated positively].

Image 2 — [Role: the source for attribute W]. Silently inventory:
- [the precise geometry/structure of W — enumerated per attribute]
- [any co-baked attribute you also want from this reference, e.g. orientation]

[continue per reference]
```

Follow the inventory block with a single positive directive sentence (Per-Reference Role Assignment, above) and any `CRITICAL` sections needed for geometry lock or continuity. For a worked fashion inventory (a reference scoped to both cuff construction and arm orientation), see capability-patterns.md "Reference Orientation Lock".

## Constraint Locking with CRITICAL Sections

A `## CRITICAL — [attribute]` block is a general constraint-locking primitive, not a fixed menu. Any attribute the model tends to drift on can get its own CRITICAL section that names the target positively and in detail. Geometry and continuity (below) are the two with full worked treatment, but production prompts routinely stack several different locks in one prompt:

| Lock | What it pins | One-line shape |
|---|---|---|
| **Identity** | a character's face/hair/skin across shots | "The subject is exactly the person from Image 1 — same hair, jawline, skin tone and grain; identity sourced exclusively from Image 1." |
| **Skin & finish** | texture realism, no plastic/waxy look | "Natural unedited skin with visible pores and matte complexion. Muted naturalistic neutral palette." |
| **Lighting & color** | photographic register continuity | "Same key-light direction, shadow density and falloff, color science, and finish as Image 1." |
| **Geometry** | exact construction / proportions | enumerate per attribute — see Geometry Enumeration |
| **Orientation / pose** | which way the subject faces | "The back of the head faces the lens; the head turns slightly so a sliver of cheek shows in profile at the frame's right edge." |
| **Continuity** | a whole bundle from one reference | "from the same set as Image 1: same subject, same setting, same light" — see Continuity Assertion |
| **Subject-pose adaptation** | drape/fall follows the new pose, not the reference's | "Adapt the garment drape to the described pose, not Image 3's pose. Follow Image 3 only for color, cut, construction." |

**It's a balance.** Each CRITICAL section removes a degree of freedom — raising fidelity on that attribute, but also adding tokens the model must reconcile and risking crowding out others. Lock the attributes that both (a) matter for this shot and (b) the model is actually drifting on. A simple t2i or single-element edit needs none. A complex multi-reference composition — where identity, construction, lighting, and skin realism all have to hold at once — earns several stacked locks. The count scales with how many independent things can drift, not with how ambitious the prompt is. Add a lock when you observe drift; don't pre-emptively lock everything. An attribute the references already carry reliably (because a hero image encodes it) needs no lock — see Single-Reference Collapse.

Two empirical patterns from production:

- **Skin & finish is the near-universal lock for photorealistic human shots.** Generative skin reliably drifts toward plastic, waxy, or over-retouched, so it earns a lock even when every other attribute is stable. It is often the *only* lock a hero-anchored shot needs.
- **The harder it is for the references to carry an attribute, the more it needs an explicit lock.** A back view where the face is mostly hidden has almost no pixels to anchor identity, so identity must be re-asserted in text even though a hero portrait exists. When a reference *can't* be used for a lock (e.g. a construction reference that leaks an unwanted face), describe the attribute in text instead and annotate the header — `## CRITICAL — Collar geometry (described, not image-referenced)`.

Place CRITICAL sections after the generation directive and shot description, one per attribute, each headed `## CRITICAL — [attribute]`. Phrase every lock positively (the rule from the Core Prompting Principle in SKILL.md).

## Continuity Assertion (Composition Lock)

When an output should inherit a *bundle* of qualities from one reference — an identity, OR a product, OR an environment plus its light, grade, and finish — assert sameness in a single clause instead of enumerating each quality. The bundle-source can be any reference; name the attributes that reference owns.

```
This is a [shot] from the same [session / set / scene] as Image [N]:
same [subject], same [setting], same [light].
```

The phrase does enormous load-bearing work — production runs replaced multi-paragraph preservation blocks with this single sentence and got tighter coherence. Use it as a dedicated `## CRITICAL — Continuity` section in any follow-up shot that should read as part of the same set as an earlier image.

Generic example — compositing a product onto a recurring set:

```
## CRITICAL — Continuity

This is a detail shot from the same set as Image 1: same product, same marble
table, same softbox lighting, same color grade. The bottle is the visual hero,
centered in the frame against the table surface.
```

This works because asserting continuity ("same set as Image 1") re-anchors the whole bundle in one token-cheap clause, where re-describing each quality separately would multiply the degrees of freedom the model has to reconcile against the reference. For a fashion worked example (back-of-wrist detail anchored to a hero shoot), see capability-patterns.md "Detail Shots".

## Single-Reference Collapse

Once one image reliably encodes a locked bundle of attributes — because an earlier generation produced it, or because it was assembled for the purpose — drop the references that originally contributed those attributes. A composite built from six references to establish a subject can collapse to two inputs for follow-ups: the locked image as the bundle-source, plus the single reference for the new attribute being introduced.

Fewer references means fewer competing signals, which raises fidelity on the attributes that matter. The collapse is not domain-specific:

- **Character work** — once a hero portrait locks the face, drop the turnaround/character sheets; use the portrait plus the new pose or wardrobe reference.
- **Product work** — once a hero render locks materials and lighting, drop the CAD/lighting refs; use the render plus the new angle reference.
- **Scene work** — once an establishing frame locks the environment and grade, drop the mood-board refs; use the frame plus the new foreground element.

Which reference becomes the bundle-source is a per-task choice, not a fixed rule. For the fashion instance (a hero front shot collapses the character sheet and lighting reference for every follow-up detail crop), see capability-patterns.md "Detail Shots".

## Geometry Enumeration

A generic "match Image 2 exactly" does not preserve specific geometric attributes — the model reads "exactly" aspirationally and lets dimensions, edge shapes, counts, and angles drift seed-to-seed. To lock geometry, enumerate each attribute explicitly and positively in a dedicated `## CRITICAL — Geometry match` section.

This applies to any object with specific geometry the reference cannot be trusted to carry on its own:

- **A logo** — stroke weight, corner radius, letter-spacing, x-height, overall aspect ratio
- **An architectural feature** — window proportions, column count, roof pitch, bay spacing
- **A product silhouette** — neck-to-body ratio, shoulder curve, base diameter
- **A face** — feature spacing, jaw angle, brow position

Phrase every attribute positively — "sharp 90° corners" not "not rounded", "a single fastener at the edge" not "no double row". For the fashion garment-region instance (cuff / collar / placket / sleeve attribute table), see capability-patterns.md "Geometry Lock for Detail Shots".

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
to a vintage brown leather chesterfield."
```

"Only" after the verb defines scope. The element name ("the blue sofa") defines the mask region. The `change` verb's implicit scope protects everything else — no stop clause needed.

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

The minimal directive pattern (one Replace, no stop clause — see "Minimal Directive Pattern" above) works for a single change. When a prompt has two competing changes — garment swap plus sign text, outfit plus accessories, subject plus background — the model compromises on one.

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

### Pose to Make Unwanted Elements Impossible

When framing or composition language alone fails to suppress an unwanted element (positive crop instructions get interpreted aspirationally — "tight crop to forearm" still includes the belt), pose the subject so the unwanted element is physically not in the shot. The model gets a concrete physical solution instead of a suppression task.

Worked example: a cuff macro that kept including the trouser. The fix was not "no trouser in frame" — it was repositioning the arm:

```
Bad:   "Crop tightly to the forearm and cuff only. No belt, no trouser visible."
Good:  "The arm is held slightly away from the body so the cuff is silhouetted
        against the studio backdrop. The forearm and hand fill the frame against
        the seamless backdrop behind them."
```

The trouser is suppressed not by naming its absence but by giving the arm a physical position where the trouser cannot be visible. Same technique works for accessories ("the hands hang at the sides, fingers loose" makes hand-in-pocket impossible), facial features ("the head is turned profile to camera" makes both eyes impossible to show), and backdrops.

### Asymmetric Directive Collapse

Gemini's editorial training prior couples contrapposto with symmetric framing — "one hand in pocket, the other at side" gets symmetrized to "both hands in pockets" on ~50% of seeds. Standard hedging language ("naturally asymmetric") doesn't help; the model defaults to its prior.

To improve compliance, name the asymmetry as the explicit goal and describe both sides positively in separate clauses:

```
Bad:   "Hands settle naturally — one in pocket, one at the side."
Good:  "Only the left hand is in the pocket. The right hand rests visibly at the
        right thigh with fingers loose. The arms are deliberately asymmetric."
```

Naming both sides removes the option of guessing; describing the asymmetry as the goal removes the option of regression toward symmetry.

### Listed Alternatives Collapse

When a prompt offers the model multiple options for the same attribute ("hands at sides, OR hand in pocket, OR adjusting cuff"), Gemini picks one option and repeats it across every seed in the batch — defeating the purpose of offering alternatives for variety.

For pose or styling variety across a batch, run separate generations each with a single distinct directive. One prompt = one option; the batch flag controls per-prompt variance, not directive variance.

### Per-Seed Variance

Image generation is stochastic — the same prompt with the same references produces meaningfully different outputs across seeds. For directives requiring precise execution (specific pose, asymmetric framing, exact crop, geometry lock), expect ~50% per-seed hit rate even with locked recipes. The remedy is `--batch 3` or `--batch 4` plus cherry-picking the obeying frame, not iterating prompt language to push consistency past ~75%.

When cherry-picking, judge against the *primary transferred attribute* first — whatever the shot exists to deliver — then secondary consistency (identity, environment), then staging (composition, framing). Decide what is primary per task before you generate; for a multi-reference detail shot the primary attribute is usually the construction being highlighted, not the identity carrying it.
