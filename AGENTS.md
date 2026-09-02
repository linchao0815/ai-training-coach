# ai-training-coach - Codex Project Rules

## Language

- Always answer in Traditional Chinese, unless the user explicitly asks for another language.
- Keep code, commands, file names, API names, and other proper nouns in their original language when appropriate.

## LLM Wiki (`wiki/`)

This project has an LLM-maintained knowledge base in `wiki/`; its schema and workflow are documented in [wiki/WIKI.md](wiki/WIKI.md). The wiki continuously integrates knowledge from `my.md`, `README.md`, `doc/*.md`, `data/analysis/*.json`, and related sources so future conversations do not need to rebuild context from raw files every time.

The wiki is not limited to running training. It's designed as the user's broader personal body and health knowledge base — training and injuries are the example topic with the most content in this template, but bloodwork, health checks, genetic testing, and other health data can be equally important independent threads.

When a user request is related to training planning, injury, health data, or data analysis:

1. Check whether `wiki/.pending-ingest` exists and is non-empty.
2. If it exists and is non-empty, briefly tell the user how many pending ingest changes there are and which commits/files they came from, then ask whether to process them now.
3. If the user agrees, follow the ingest process in `wiki/WIKI.md`, then clear `wiki/.pending-ingest` after completion.
4. If `wiki/.pending-ingest` does not exist or is empty, do not mention it; use `wiki/index.md` as the starting point for background knowledge.

A git commit that touches source files in the wiki whitelist, as defined in `wiki/WIKI.md`, is recorded by the `githooks/post-commit` hook in `wiki/.pending-ingest`. The hook does not block the commit and does not automatically spend tokens on ingest; the decision to review or process ingest is intentionally left for the next relevant conversation.

Note: the hook is version-controlled under `githooks/` and takes effect via `core.hooksPath`. Because git hooks are not copied by `git clone`, a fresh clone must run `git config core.hooksPath githooks` once (see [README.md](README.md)); otherwise the queue fails **silently** — commits still succeed, they just stop being recorded, with no error. Verify with `git config --get core.hooksPath`.

## Other References

- Project tool usage: [README.md](README.md)
- Runner background: copy [my.md.example](my.md.example) to `my.md` (already gitignored) and fill in your own data.
