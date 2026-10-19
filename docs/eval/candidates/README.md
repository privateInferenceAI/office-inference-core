# Candidate evaluation questions

**Status: draft candidates, not part of the suite.** The live instrument is `docs/eval/data-questions.yaml`.
Nothing in this directory is scored until a candidate set is reviewed, validated mechanically and accepted.

## Why this exists

The current forty-question set touches 11 of 539 corpus documents and is near its ceiling (about 50.6 of 52 on
five runs). To measure anything further we need questions that are harder and drawn from documents the current
set never exercises. Vendor and client both need this to be repeatable, so candidates are authored here first.

## Rules for a candidate

1. **Ground truth comes from the indexed text.** Every expected value must be read from `chunk_index` on the
   box (the text the retriever actually sees), and the carrying passage quoted in the evidence file. No outside
   knowledge, no value copied from an assistant answer.
2. **One value, one document.** The `contains` value must appear in the document named in `docs`, and the
   question must be unambiguous: a second reasonable answer in the corpus is a reason to rewrite the question.
3. **No duplicates.** The fact must not already be the answer or the premise of one of the existing forty
   questions or the four probes. Check `docs/eval/data-questions.yaml` and `tests/fixtures/data_suite_run.py`.
4. **A source quote on every candidate.** A candidate without a quoted passage does not go in.
5. **Staff role by default.** `executive` expectations only for an access-boundary candidate, and then both
   expectations are required.
6. **Read-only on the box.** Query `chunk_index`, the parser output and the retrieval path. Never modify the
   corpus, a deployment file, the suite, or git.

## Corpus access

    ssh -i /home/anthony/ai-stack/ai-stack-key.pem -o StrictHostKeyChecking=no ubuntu@52.204.202.66 'bash -s' <<'EOF'
    cd /opt/oi-core
    USER=$(grep -E "^POSTGRES_USER=" rendered/env | cut -d= -f2)
    DB=$(grep -E "^POSTGRES_DB=" rendered/env | cut -d= -f2)
    docker exec -i postgres psql -U "$USER" -d "$DB" -c "select doc_id, page_start, kind, left(text, 400) from chunk_index where text ilike '%something%' limit 10"
    EOF

Retrieval-only check of a question, as the retriever sees it:

    ssh -i /home/anthony/ai-stack/ai-stack-key.pem ubuntu@52.204.202.66 \
      "cd /opt/oi-core && docker compose -f deploy/office/compose/docker-compose.yml --env-file rendered/env run -T --rm --no-deps --entrypoint python -v /opt/oi-core:/w:ro -w /w -e ACCESS_REGISTRY=/w/rendered/access.json eval /w/tests/fixtures/ask.py --question '<question>' --role staff --json"

## Format

```yaml
- id: CAND_<AXIS>_01
  category: <category>
  question: "..."
  expected:
    staff: {answered: true, docs: ["company/group-02/123456.csv"], contains: ["12,345"]}
  source_quote: "the exact line or sentence from chunk_index that carries the value"
  evidence: "verified with select ...; value appears only in this document"
```

The evidence file beside the YAML carries the full passage, the exact SQL used and its result, and why the
candidate is not a duplicate.

## What happens to a candidate

A candidate is accepted into the suite only after: a mechanical validation run (document exists, quote found in
the index, value in the quote, no duplicate), a review of the question wording, and a measurement run. Accepted
candidates move to `docs/eval/data-questions.yaml`; the rest stay here with the reason they were not taken.
