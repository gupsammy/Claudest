# claude-thinking ![v0.3.6](https://img.shields.io/badge/v0.3.6-blue?style=flat-square)

Structured thinking and multi-perspective deliberation tools for Claude Code. Single-agent dialogue for clarifying ideas, and multi-agent councils for stress-testing decisions from multiple cognitive frames.

## Why

Some of the best thinking happens in conversation, but unstructured conversation wanders. These skills provide two complementary modes: brainstorm uses one-on-one dialogue with domain-calibrated questioning to help you articulate your thinking, while council spawns multiple cognitive personas in parallel to surface perspectives you might miss on your own. Both produce structured, reusable output.

## Installation

```
/plugin marketplace add gupsammy/claudest
/plugin install claude-thinking@claudest
```

## Skills

### brainstorm

In-depth interview to clarify, stress-test, and articulate ideas. Calibrates questioning style to the domain — adversarial for strategy, gentle for personal decisions, Socratic for abstract topics. Produces a synthesis document (spec, brief, decision doc, reflection) with key themes, decisions, and open questions. No raw Q&A transcript — user quotes are woven into the synthesis where they're apt.

Triggers on: "interview me about", "help me clarify", "stress-test my idea", "let's explore this concept", "challenge my assumptions about", "probe my assumptions".

Domain calibration:

| Domain | Approach |
|--------|----------|
| Technical/coding | Moderate depth — focus on requirements, edge cases, architectural decisions |
| Creative projects | Explore vision, constraints, audience, emotional intent with more breadth |
| Business/strategy | Probe assumptions, market dynamics, risks, second-order effects. Challenge more. |
| Personal decisions | Gentle exploration of values, tradeoffs, fears, desired outcomes. Less adversarial. |
| Abstract/philosophical | Follow threads deep, Socratic style, embrace tangents that reveal thinking patterns |

Saturation detection: after 4+ rounds with no new theme, or 3+ rounds of consecutively shorter answers, the skill proposes closure with a summary of key themes and underexplored areas.

Runs on: Claude Opus. Tools: Read, Write, AskUserQuestion.

### council

Spawns parallel agents with distinct cognitive personas to deliberate on a question. Each agent investigates relevant codebase files before forming a position. A dialectical synthesis resolves consensus, tensions, and blind spots into an opinionated verdict with next steps.

Triggers on: "get perspectives on", "multiple viewpoints", "council on this", "devil's advocate", "stress test this idea", "red team this", "poke holes in", "second opinion on", "what am I missing".

Six personas available:

| Persona | Frame | Signature Question |
|---------|-------|--------------------|
| Architect | Systems, structure, dependencies | "What are the load-bearing assumptions?" |
| Skeptic | Risks, failure modes, hidden assumptions | "What are we not seeing?" |
| Pragmatist | Effort-value, simplicity, maintenance | "What's the simplest thing that works?" |
| Innovator | Alternatives, inversions, analogies | "What would the opposite approach look like?" |
| Advocate | User experience, empathy, accessibility | "How does this feel to encounter first?" |
| Strategist | Timelines, second-order effects, reversibility | "What does this look like in 6 months?" |

Council sizes: `--quick` (2), default (4), `--full` (6). Use `--deep` for Opus-powered agents. Use `--include`/`--exclude` to override persona selection.

Adaptive selection: the skill classifies your question (architecture, strategy, risk, UX, innovation, planning) and picks the most relevant personas automatically.

Runs on: Claude Sonnet (agents), session model (orchestrator). Tools: Agent, Read, Glob, Grep, AskUserQuestion.

## Commands

### /council

Explicit invocation with flag control: `/council "Should we use Redis or Memcached?" --quick --deep`

## Roadmap

Planned skills tracked in [`docs/skill-ideas.md`](docs/skill-ideas.md):

| Skill | Description |
|-------|-------------|
| decision-journal | Structured decision logging with revisitation and predicted-vs-actual tracking |
| rubber-duck | Explain-your-code dialogue that surfaces implicit assumptions and hidden complexity |
| pre-mortem | Prospective hindsight analysis — work backwards from failure to identify risks |
| steelman | Build the strongest counter-argument to stress-test your own position |

Council roadmap:

| Version | Feature |
|---------|---------|
| v0.3 | Parallel agents, 6 personas, adaptive selection, dialectical synthesis |
| v0.4 | `--deliberate` flag (TeamCreate mode for inter-agent debate) |
| v0.5 | Multi-model dispatch (Claude + external agents via coding-agent skill) |

## License

MIT — see [LICENSE](../../LICENSE) if present, otherwise standard MIT terms apply.
