---
name: run-research
description: >
  This skill should be used when the user asks to "research a topic",
  "run-research", "last30", "what's happening with X", "what are people
  saying about X", "find the best X", "X prompts", "latest on X", "X news",
  "what are people recommending for X", "research X for me", or wants to
  know what's trending, discussed, or debated about any subject in recent
  weeks.
argument-hint: 'run-research AI video tools, run-research best project management tools'
user-invocable: true
allowed-tools:
  - Bash(which:*)
  - Bash(curl:*)
  - Bash(reddit-cli:*)
  - Bash(bird:*)
  - Bash(python3:*)
  - Bash(brave-cli:*)
  - Read
  - AskUserQuestion
  - WebSearch
---

# run-research

## Value Context

Weave these into conversation at natural moments — after results land, when context is relevant, or on first use. One or two per run, not all at once.

- This isn't a single web search — it hits Reddit, X, YouTube, and web sources in parallel and cross-references findings by engagement signals. A Reddit thread with 2k upvotes carries more weight than an unsourced blog post.
- YouTube transcript analysis is a unique capability — it reads what creators actually said, not just video titles, surfacing insights that don't appear in text-based search.
- The `--deep` flag roughly doubles source coverage and adds subreddit discovery and transcript extraction — worth it for decisions with real stakes.
- After research completes, the agent becomes a domain expert on the topic — follow-up questions are answered from the research data, not general knowledge. Prompt generation is also grounded in what real users report working.

---

## STEP 0: Parse Intent

If `$ARGUMENTS` is empty, use `AskUserQuestion` to ask the user for the research topic before proceeding.

Extract from `$ARGUMENTS`:

- **TOPIC** — the core subject (e.g. "Claude Code skills", "AI video tools")
- **TARGET_TOOL** — specific tool/product they'll use results in, if stated (e.g. "Midjourney"). Leave as `unset` if not mentioned.
- **DAYS** — look-back window. Default `30`. Accept `--days=N`.
- **DEPTH** — `quick`, `default`, or `deep`. Accept `--quick` / `--deep`. Default: `default`.
- **QUERY_TYPE** — classify the intent:
  - `RECOMMENDATIONS` → "best X", "top X", "what X should I use", "recommended X"
  - `NEWS` → "what's happening with X", "X news", "latest on X"
  - `PROMPTING` → "X prompts", "prompting for X", "X techniques", "X best practices"
  - `GENERAL` → everything else

Depth → result limits:

| Depth   | Reddit `--limit` | Bird count | YouTube count |
|---------|-----------------|------------|---------------|
| quick   | 15              | 12         | 5             |
| default | 50              | 30         | 10            |
| deep    | 100             | 60         | 20            |

**Output this block before calling any tools:**

```
I'll research {TOPIC} across Reddit, X, YouTube, and the web (last {DAYS} days).

Parsed intent:
- TOPIC        = {TOPIC}
- TARGET_TOOL  = {TARGET_TOOL or "not specified"}
- QUERY_TYPE   = {QUERY_TYPE}
- DEPTH        = {DEPTH}

Starting research now…
```

Do NOT ask about TARGET_TOOL before running — research first, ask only if needed after.

---

## STEP 1: Detect Available Sources

Run in one Bash call:

```bash
which reddit-cli 2>/dev/null && echo "REDDIT=ok" || echo "REDDIT=missing"
which bird       2>/dev/null && echo "BIRD=ok"   || echo "BIRD=missing"
which yt-dlp     2>/dev/null && echo "YTDLP=ok"  || echo "YTDLP=missing"
(which brave-cli >/dev/null 2>&1 && ([ -n "$BRAVE_API_KEY" ] || grep -q "BRAVE_API_KEY" ~/.secrets 2>/dev/null)) && echo "BRAVE=ok" || echo "BRAVE=missing"
```

Record `REDDIT`, `BIRD`, `YTDLP`, `BRAVE`. Always run at least web search.

### Install missing tools (reddit-cli / brave-cli only)

If `REDDIT=missing` or `BRAVE=missing`, use `AskUserQuestion` to offer installation before proceeding. Tailor the question to which tools are missing:

- Only `REDDIT=missing` → "reddit-cli is not installed. Install it now so Reddit results are included?"
- Only `BRAVE=missing` → "brave-cli is not installed. Install it now for better web search coverage?"
- Both missing → ask once, offering to install both.

If the user approves, run the installer(s) for the missing tools:

```bash
# reddit-cli
curl -fsSL https://raw.githubusercontent.com/gupsammy/reddit-cli/main/install.sh | bash

# brave-cli
curl -fsSL https://raw.githubusercontent.com/gupsammy/brave-cli/main/install.sh | sh
```

After installing, re-run the detection block to confirm `REDDIT=ok` / `BRAVE=ok` before proceeding.

Skip any source still marked `missing` without error or prompting.

If `BIRD=missing` or `YTDLP=missing`, read `references/sources.md` and surface the setup instructions to the user after research completes.

---

## STEP 2: Run Sources

Run each available source fully before moving to the next. Read the complete output.

### Reddit (if `REDDIT=ok`)

```bash
reddit-cli search "{TOPIC}" --days {DAYS} --limit {REDDIT_LIMIT} --output json
```

Output schema: `{"items": [{id, title, url, subreddit, score, num_comments, author, date}]}`

Track: post count, top subreddits, total upvotes.

For `deep` depth: also find relevant subreddits and drill into the top one:

```bash
reddit-cli subreddits "{TOPIC}" --by description --limit 5 --output json
reddit-cli search "{TOPIC}" --subreddit {TOP_SUB} --days {DAYS} --limit 20 --output json
```

Merge and deduplicate by URL.

### X / Twitter (if `BIRD=ok`)

```bash
bird search "{TOPIC} since:$(date -v-{DAYS}d +%Y-%m-%d 2>/dev/null || date -d "{DAYS} days ago" +%Y-%m-%d)" -n {BIRD_LIMIT} --json
```

Track: post count, total likes, total reposts, top @handles by engagement.

### YouTube (if `YTDLP=ok`)

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/search-youtube/scripts/yt_research.py" search "{TOPIC}" --count {YT_COUNT} --after $(date -v-{DAYS}d +%Y%m%d 2>/dev/null || date -d "{DAYS} days ago" +%Y%m%d)
```

For `deep` depth, or when ≥1 video looks highly relevant (based on title + view count): fetch transcript of the top 1-2 results:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/search-youtube/scripts/yt_research.py" transcript "{VIDEO_URL}"
```

Transcripts are dense signal — read them fully and extract specific insights, not summaries.

Track: video count, total views, number of transcripts read.

### Web Search

**If `BRAVE=ok`** — use brave-cli (cheaper, single binary):

```bash
brave-cli search "{TOPIC}" -n 10 --freshness month --output json
brave-cli search "{TOPIC} github OR dev.to OR medium.com" -n 5 --freshness month --output json
```

**If `BRAVE=missing`** — use Claude's `WebSearch` tool. Run 2-3 queries based on QUERY_TYPE:

- `RECOMMENDATIONS` → `best {TOPIC}`, `{TOPIC} comparison`
- `NEWS` → `{TOPIC} news 2026`, `{TOPIC} announcement`
- `PROMPTING` → `{TOPIC} prompts examples`, `{TOPIC} techniques`
- `GENERAL` → `{TOPIC} 2026`, `{TOPIC} discussion`

Exclude: reddit.com, x.com, twitter.com (already covered above). Target: blogs, docs, GitHub, dev.to, news sites.

Track: page count.

---

## STEP 3: Judge Agent — Synthesize

Ground synthesis entirely in the actual research output, not prior knowledge.

Weight by engagement because engagement = aggregate human signal strength — a thread with 2k upvotes represents more real-world consensus than an article with no engagement metric:

1. **X posts** — highest when engagement (likes + reposts) is high
2. **Reddit threads** — high; engagement = upvotes + comments
3. **YouTube transcripts** — high when available (dense, human-verified signal)
4. **Web pages** — lower; no engagement data

Identify:
- Patterns appearing across ≥2 sources (strongest signals)
- Contradictions between sources (note them explicitly)
- Top 3-5 actionable insights
- Specific names: product names, @handles, subreddits, tools mentioned by real users

**Anti-hallucination rule**: If a source says "ClawdBot" and your prior knowledge maps this to something else, report what the research says. Do not conflate based on similarity to known entities.

When all findings are captured and cross-source patterns identified, proceed to STEP 4.

---

## STEP 4: Output

### If QUERY_TYPE = RECOMMENDATIONS

```
🏆 Most mentioned:

[Name] — {N}x mentions
Use Case: [what it does]
Sources: @handle1, r/sub1, [site name]

[Name] — {N}x mentions
...

Notable: [items with 1-2 mentions]
```

Every item needs a real Sources line with @handles or subreddit names from the research output.

### If QUERY_TYPE = PROMPTING / NEWS / GENERAL

```
What I learned:

**{Finding 1}** — [1-2 sentences, per @handle or r/sub]

**{Finding 2}** — [1-2 sentences, per @handle or r/sub]

KEY PATTERNS:
1. [Pattern] — per @handle
2. [Pattern] — per r/sub
3. [Pattern] — per [YouTube channel]
```

Citation priority: @handles > subreddits > YouTube channels > web sites. Never cite raw URLs — use names and handles only. When a web article and an X post say the same thing, cite the X post.

### Stats block (always shown, right before invitation)

Calculate real totals from the research output — never estimate.

```
---
✅ Research complete!
├─ 🟠 Reddit:  {N} threads │ {N} upvotes │ top: r/{sub1}, r/{sub2}
├─ 🔵 X:       {N} posts │ {N} likes │ top: @{handle1}, @{handle2}
├─ 🔴 YouTube: {N} videos │ {N} views │ {N} transcripts read
├─ 🌐 Web:     {N} pages
└─ ⚙️  Sources: {comma-separated list of sources that returned results}
---
```

Omit any line where the source was unavailable or returned 0 results.
Use tree characters `├─` `└─` `│` exactly — no plain dashes.

### Invitation (always last — specific, never generic)

Must reference real things from the research. Generic suggestions are a quality failure.

**PROMPTING:**
```
I'm now an expert on {TOPIC} for {TARGET_TOOL}. What do you want to make? For example:
- [specific idea based on technique people are actually using]
- [trending approach from the research data]
- [riff on what users are actually creating]

Describe your vision and I'll write a ready-to-paste prompt.
```

**RECOMMENDATIONS:**
```
I'm now an expert on {TOPIC}. Want to go deeper?
- [Compare specific item A vs item B from results]
- [Why {specific item} is trending right now]
- [How to get started with {specific item}]
```

**NEWS / GENERAL:**
```
I'm now an expert on {TOPIC}. Some things you could ask:
- [Follow-up on the biggest finding from the data]
- [Implications or next steps based on what was found]
- [Comparison or deeper dive from a specific source]
```

---

## STEP 5: Wait, Then Respond

Stop after the stats + invitation block. Wait for the user to reply.

When they respond:
- **Question** → Answer from the research. No new searches.
- **Go deeper** → Elaborate using research findings.
- **Create something / prompt request** → Write ONE prompt (see below). Never write a prompt when they asked a question.

### Writing a Prompt (when requested)

One prompt only. Structure:
1. Role/context line — tool, style, purpose
2. Core instruction — exact task with specific parameters grounded in research
3. Quality modifiers — from what real users report actually working, per research
4. Output spec — format, dimensions, tone, tool-specific requirements

Every element must come from the research. No generic filler.

---

## Resources

### references/
`sources.md` — setup instructions for each source (reddit-cli auth, bird auth, yt-dlp install, brave-search setup).
