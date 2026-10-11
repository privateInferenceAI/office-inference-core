# Review log

Every review round we have run, what it covered, and what it caught. The process itself is in
`AGENTS.md` under "What finished means"; this is the evidence that it earns its cost, and the
record a new session reads before running the next one.

Two loops exist, and they are different things:

- **The deliverable loop** checks that a phase is usable: an executor runs every command on the box, a
  cold reader reads the documentation with no access to the code, and a fidelity checker reads the
  claims against the source. It runs at the end of a phase.
- **The decision loop** checks that a choice is sound: several independent angles research it, one agent
  synthesises, two attack the result from different directions, and one revises. It runs before a
  decision that is expensive to reverse.

Both exist because reading is not running. The single most useful finding in the whole of C1 came from
an agent typing a command the manual told it to type, not from anyone reading the manual.

## The deliverable loop

| Round | Covered | Agents | What it caught |
|---|---|---|---|
| 1 | The operating manual, against the code and against the live box | Fidelity, executor, cold reader | The manual implied a JSON schema was enforced when nothing read it; the validator's refusal list was about half the real rules; the SSM access path looked broken but was a false positive, because the agent is a snap and `systemctl` cannot see it |
| 2 | The corrections from round 1, re-tested | Executor, fidelity | **`oi up -d litellm` reloads nothing**, so the add-a-role walkthrough would have silently done nothing; the export job shipped the day that was *starting*; the pack catalog's `requires` edges were documentation that nothing read; two worked services had no `service.yaml` while the convention required one |
| 3 | The corrections from round 2 | Executor, fidelity | The claim that `data_classes_allowed` is rendered into the environment was false; two `verify` sample blocks were stale and self-contradictory, quoting a hash no artifact ever held |
| 4 | The whole manual as a newcomer would meet it, plus the code claims | Executor, cold reader, fidelity | The manual's own "worked example" for adding a service did not exist as described; `DATABASE_URL` was listed as renderer-written when compose assembles it; the reranker documentation read as a ban when it was a deferral |

Roughly seventy-five findings across the four rounds. The ones worth remembering are the ones a
reader could not have found: a command that reloads nothing, an export job that ships the wrong day,
a convention half the worked examples break.

**What changed as a result**, beyond the manual: three code defects fixed (`up -d` documented as
`restart`, the export day, the anchor default crashing with a traceback), two new capabilities
(`keys --revoke`, the pack `requires` closure), migration 006, and the manual's troubleshooting
section rewritten so every entry gives a discriminator rather than a suspicion.

## The decision loop

| Round | Decision | Agents | What it caught |
|---|---|---|---|
| 5 | The C2 retrieval architecture | Nine research angles, a synthesis, two adversarial critiques, a revision | That the problem is **tokenisation, not ranking** (an identifier shatters in the parser and no BM25 buys it back); that the reranker at the brief's depth cannot ship on CPU; that the synthesis had invented a constraint by carrying a Qdrant version across from a sibling project; that ParadeDB is the only measured engine to recover partial identifiers, and that its licence is a decision rather than a prohibition |

The research reports, the critiques and the revision are in `docs/research/search-2026-10/`. The
decision they produced is `docs/search-architecture.md`.

## What the log is for

Three things, none of which is bookkeeping:

1. **So the next session runs the loop the same way.** The panel, the cap and the verdict requirement
   are only real if someone can see them being applied.
2. **So findings are not re-discovered.** Two licence mistakes were caught by verification rather than
   memory, and one of them was caught by Anthony reading a vendor's own page. Both are recorded in
   `docs/evaluation-corpora.md` and in the search architecture's licence section.
3. **So the loop is judged on evidence.** If a phase passes review with only cosmetics, that is the
   signal to stop, and it is only visible if the previous rounds are written down.

## Adding a round

Append a row. Name what was covered, how many agents, and the findings that changed something. If a
round found nothing that changed anything, say so: that is the signal the phase is done, and it is
more useful than a round that found nothing and was not recorded.
