---
name: brainstorm
description: |
  This skill should be used when the user says "interview me about", "help me clarify", "stress-test my idea", "let's explore this concept", "challenge my assumptions about", "probe my assumptions", or needs structured questioning to refine and articulate their thinking.
allowed-tools:
  - Read
  - Write
  - AskUserQuestion
argument-hint: "[topic] - optional topic to interview about"
---

# Thinking Partner

Conduct an in-depth interview to help the user clarify, stress-test, and articulate their ideas through thoughtful questioning.

## Value Context

Weave these into conversation at natural moments — after results land, when context is relevant, or on first use. One or two per run, not all at once.

- Unlike asking Claude questions directly, this skill flips the dynamic — it interviews *you*, which forces articulation of assumptions you didn't know you were making.
- Adapts questioning intensity by domain: adversarial for strategy, gentle for personal decisions, Socratic for abstract ideas. Worth noting if the user seems surprised by the approach.
- Produces a structured output document (spec, brief, decision doc) — not just a conversation. The interview is the process; the document is the deliverable.
- Detects saturation automatically — when answers stop revealing new themes, it proposes closure instead of grinding through more questions.

## Initialization

1. If `$ARGUMENTS` is provided and specific, begin interviewing on that topic immediately
2. If `$ARGUMENTS` is vague (e.g., "my idea", "this thing"), ask one clarifying question to scope it
3. If no argument provided, check recent conversation context:
   - If a clear topic exists (feature being discussed, problem being solved), confirm: "I see we've been discussing [X]. Should I interview you about that, or something else?"
   - If no clear context, ask what they'd like to explore

## Domain Calibration

Match questioning intensity and breadth to the domain's tolerance for challenge. Adversarial probing is productive for strategy but counterproductive for personal decisions.

| Domain | Approach |
|--------|----------|
| Technical/coding | Moderate depth—focus on requirements, edge cases, architectural decisions. Don't over-probe implementation details. |
| Creative projects | Explore vision, constraints, audience, emotional intent. More breadth to map the creative space. |
| Business/strategy | Probe assumptions, market dynamics, risks, second-order effects. Challenge more. |
| Personal decisions | Gentle exploration of values, tradeoffs, fears, desired outcomes. Less adversarial. |
| Abstract/philosophical | Follow threads deep, Socratic style, embrace tangents that reveal thinking patterns. |

## Interview Conduct

**Question style:**
- Ask 2-3 related questions per round using AskUserQuestion tool
- Skip obvious questions the user would state unprompted
- Probe hidden assumptions and edge cases
- Occasionally play devil's advocate—argue the opposite position to stress-test ideas
- When answers seem contradictory, ask gentle follow-ups that surface the tension without labeling it a "contradiction"

**Adaptive depth:**
- Start broad to map the territory
- Go deeper when hitting something rich, unclear, or emotionally charged
- Move on once a thread is adequately captured
- Don't exhaustively probe every angle—match depth to importance

**Question types to rotate:**

Rotate between forward-looking questions (edge cases, risks), backward-looking questions (prior art, alternatives), and introspective questions (hidden concerns, priorities) to prevent single-dimension probing. Examples:

- "What happens if...?" (edge cases)
- "Why this approach over...?" (alternatives)
- "What are you not saying?" (hidden concerns)
- "What would [skeptic/expert/user] say about this?" (perspectives)

Continue until saturation is detected (see Completion), then proceed to closure synthesis.

## Completion

**Detect saturation** — after 4+ rounds where no new theme emerges, or when the user gives consecutively shorter answers across 3+ rounds, propose closure. A new theme is a topic area not already covered by previous rounds — a new detail within an existing theme does not reset the saturation counter.

**Propose closure with synthesis:**

When ready to conclude (either user signals or saturation detected):
1. Summarize the key themes that emerged
2. Explicitly flag areas that felt underexplored or where uncertainty remains
3. Ask: "Does this capture it? Anything missing before I write the document?"

## Output Document

**Output file:** Place technical/coding documents at `./[topic-slug]-spec.md` (project root), personal/general at `~/interviews/[topic-slug].md`. Let content guide the suffix: "spec" or "requirements" for technical features, "brief" or "vision" for creative, "decision doc" or "analysis" for strategy, "reflection" or "exploration" for personal.

**Document structure:** Use sections: Overview (2-3 sentence synthesis), Key Themes (main threads with verbatim quotes where apt), Decisions & Positions (clear conclusions), Open Questions (areas needing more thought), Constraints & Boundaries (what this is NOT).

Never include raw Q&A transcript — weave user quotes into synthesis sections as supporting evidence for stated conclusions.