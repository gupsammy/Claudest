# Lens Reference: Questions and Deepening

Command recipes for each lens live in `SKILL.md`. This file holds the *analytical* questions to apply to retrieved sessions and the supplementary searches to run when the primary recipe surfaces too little signal.

## Core Questions

After retrieving sessions with the lens recipe, look for these specifically:

| Lens | Ask |
|------|-----|
| restore-context | What's unfinished? What were the next steps? What context would a fresh session need to pick up? |
| extract-learnings | Where did understanding shift? What mistakes became lessons? What is a small/medium/big lesson learned? |
| find-gaps | What topics recur? Where is guidance needed repeatedly? Where does the user keep getting stuck? |
| review-process | Is there planning before coding? Is debugging systematic? Where does the workflow leak time? |
| run-retro | How did the solution evolve? What worked? What was painful? What would the user do differently? |
| extract-decisions | What trade-offs were discussed? What was rejected and why? Which decisions deserve a CLAUDE.md rule? |
| find-antipatterns | What mistakes repeat? What confusions persist across sessions? What corrections does the user issue more than once? |

## Follow-up Suggestions

After running a lens, suggest the natural next step:

- **find-gaps** → recommend `learn-anything` skill (if available) for targeted instruction on the gap topic
- **extract-decisions** → recommend `/update-claudemd` to persist surfaced decisions as project rules
- **find-antipatterns** → propose CLAUDE.md additions documenting the antipattern explicitly so future sessions avoid it

## Supplementary Searches

When the primary recipe's retrieval is thin, layer a targeted search on top of it. These complement (not replace) the recipe in SKILL.md.

| Lens | Supplementary Query |
|------|---------------------|
| extract-learnings | `"learned realized understand clicked finally"` |
| find-gaps | `"confused struggling help don't understand stuck"` |
| extract-decisions | `"decided chose instead trade-off because rather"` |
| find-antipatterns | `"again same mistake repeated forgot keeps happening"` |

Run these via `search_conversations.py --query "..."` (auto-detects current project; pass `--project NAME` to override or `--all-projects` to widen). Combine results with the primary retrieval before synthesizing.

## When to Add --all-projects

Recipes default to project-scoped because retros and reflective lenses usually concern the current codebase. Widen scope when the user's intent is genuinely cross-cutting:

- "what mistakes do I keep making *everywhere*"
- "patterns across *all* my projects"
- "general lessons" (without a project context)
- find-antipatterns specifically — antipatterns are often person-level habits, not project-bound

In those cases, add `--all-projects` to the recipe.
