"""Measuring retrieval on a fixed question set, so a change is a number rather than an opinion.

RET-EVAL-001: "retrieval changes are measured on a fixed question set, not assumed". The requirement's own note
gives the reason it comes before the others: C2 decides the embedding model, the fusion and the chunking, so a
yardstick has to exist before those decisions. Those decisions were made and built; this is the yardstick.

**What this measures and what it deliberately does not.** It measures the retrieval layer, because that is what
Phase 3 built and what a question set can grade without a model. Two rates, and the difference between them is
the point:

* **Retrieval** — the document that holds the answer is among the passages returned. A weak measure that a
  large corpus passes by accident, because a long document is easy to be near.
* **Support** — a passage containing the answer's own value is among them. Much stricter: the right document
  being present is not the same as the right sentence being present, and a system that returns page 1 of the
  right filing for every question would score well on the first and badly on the second.

**Unanswerable questions are measured differently and the difference is honest.** The rule in the question set
is that an unanswerable question must produce a refusal, and a refusal is the answer layer's job, which does not
exist until Phase 4. What can be measured now is the thing a refusal threshold would be built on: **how
confident retrieval is about a question the corpus cannot answer.** If the top score on unanswerable questions
overlaps the top score on answerable ones, no threshold separates them and the refusal has to come from
somewhere else. Reporting that overlap is more useful than reporting zero refusals on a layer that does not
refuse.

**A rate at n=10 is not a quality claim.** The research says it plainly about its own gate: the interval around
55/60 is 0.80 to 0.95, so we can say a gate was passed and not that we are at 92 percent. At ten questions the
interval is wider still. What ten questions can do is catch a search that is broken, and that is what this is
for. The client's own sixty arrive in C8.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Question:
    """One graded item, with everything the question set decided rather than everything it hoped."""

    id: str
    question: str
    category: str = "uncategorised"
    answerable: bool = True
    source_document: str | None = None
    reference_answer: str | None = None
    page: int | None = None
    pages: list[int] = field(default_factory=list)
    # Why a question is unanswerable, or what to look for instead. Written when the set was checked against the
    # parsed corpus, and kept because it is the only record of what "not in this corpus" was checked against.
    note: str | None = None

    @property
    def expected_pages(self) -> list[int]:
        return self.pages or ([self.page] if self.page else [])


def load_document_paths(manifest_path: str | Path | None) -> dict[str, str]:
    """Manifest id to the file the corpus holds, so a question's source can be matched to a retrieved passage.

    The question set names a document by its manifest id, which is the publication's identity: `irs-p15-2026` is
    Publication 15 for 2026. The corpus holds a file called `irs-p15.pdf`. Those are two names for one document
    and the harness was comparing the first against the second, so Q01 and Q02 were scored as retrieval failures
    for a naming mismatch.

    The fix belongs here rather than in the question set, and the corpus validator is what settled it: it refuses
    a question whose source is not a manifest id, which means the ids are the contract and the filenames are an
    implementation detail of how the corpus was assembled.
    """
    if not manifest_path:
        return {}
    path = Path(manifest_path)
    if not path.exists():
        return {}
    import yaml
    data = yaml.safe_load(path.read_text()) or {}
    return {doc["id"]: doc.get("path") or doc["id"] for doc in (data.get("documents") or [])}


def load_questions(path: str | Path) -> list[Question]:
    import yaml
    data = yaml.safe_load(Path(path).read_text())
    out: list[Question] = []
    for raw in data.get("questions") or []:
        expected = raw.get("expected") or {}
        refs = expected.get("references") or {}
        out.append(Question(
            id=raw["id"], question=raw["question"],
            category=raw.get("category") or expected.get("category") or "uncategorised",
            answerable=bool(expected.get("answerable", True)),
            source_document=expected.get("source_document"),
            reference_answer=refs.get("reference_answer"),
            page=refs.get("page"),
            pages=list(refs.get("pages") or []),
            note=refs.get("reference_note") or expected.get("reference_note") or raw.get("note"),
        ))
    return out


def normalise(text: str) -> str:
    """Whitespace collapsed and case folded, so a value found across a line break still matches.

    The numbers are handled separately by `number_forms` rather than here, because a passage that writes
    `391,035` and an answer that writes `391035` are the same figure and a substring match would miss it.
    """
    return re.sub(r"\s+", " ", text or "").strip().lower()


def primary_figure(value: str) -> str | None:
    """The most specific figure in a reference answer, which is the fact the question is about.

    Needed because the reference answers were written by reading the corpus and explaining what they found, so
    many are a paragraph rather than a number. Requiring every figure in a paragraph to appear in one passage
    scores a correct retrieval as a miss: Q06's answer names the fiscal year and the year-end date as well as
    the sales figure, and no single chunk of a financial table holds all three.

    The longest digit run is the specific one. `391,035` is what the question asks for; `2024` and `29` are how
    the answer situates it, and they belong in the explanation rather than in the test.
    """
    runs = re.findall(r"\d[\d,.]*\d|\d", value or "")
    cleaned = [(re.sub(r"\D", "", run), run) for run in runs]
    cleaned = [(digits, run) for digits, run in cleaned if len(digits) >= 2]
    if not cleaned:
        return None
    return max(cleaned, key=lambda pair: len(pair[0]))[1]


def number_forms(value: str) -> set[str]:
    """Every spelling of a figure, so one occurrence matches whichever way either side wrote it.

    `391,035`, `391035` and `391.035` are the same figure in a financial table, where the thousands separator
    is often dropped and the unit lives in a column header. Matching the digits rather than the punctuation is
    what makes the comparison about the number instead of about the typesetting.
    """
    forms: set[str] = set()
    for match in re.finditer(r"\d[\d,.\s]*\d|\d", value or ""):
        digits = re.sub(r"\D", "", match.group(0))
        if len(digits) >= 2:
            forms.add(digits)
    return forms


@dataclass
class Outcome:
    """What one question did on one run."""

    question_id: str
    category: str
    answerable: bool
    # The document that should hold the answer appeared among the passages.
    retrieved_document: bool = False
    # A passage containing the answer's own value appeared among them.
    supported: bool = False
    # Where the first supporting passage sat, one-based. None when there was none.
    rank: int | None = None
    top_score: float | None = None
    documents: list[str] = field(default_factory=list)
    # Whether the passage that supported the answer came from a document with no real pages. Six of the ten
    # questions draw on HTML filings whose every chunk reports page 1, so the question set's rule that a cited
    # page must fall inside the chunk's range is satisfied trivially for them: 1 is inside 1..1. Recording it
    # stops that rule being quoted as though it had been tested on those six.
    page_is_nominal: bool = False
    # Whether support could be measured at all. False for a reference answer with no figure in it, because
    # support for prose needs entailment and this layer does not have it.
    support_measurable: bool = True
    detail: str = ""


@dataclass
class Report:
    """A run, and the comparisons it supports."""

    configuration: str
    k: int
    outcomes: list[Outcome] = field(default_factory=list)

    @property
    def answerable(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.answerable]

    @property
    def unanswerable(self) -> list[Outcome]:
        return [o for o in self.outcomes if not o.answerable]

    def _rate(self, outcomes: list[Outcome], attribute: str) -> float | None:
        if not outcomes:
            return None
        return sum(1 for o in outcomes if getattr(o, attribute)) / len(outcomes)

    @property
    def retrieval_rate(self) -> float | None:
        return self._rate(self.answerable, "retrieved_document")

    @property
    def support_rate(self) -> float | None:
        """Over the questions support CAN be measured on, which is fewer than the answerable ones.

        A rate that counted an unmeasurable question as a miss would be a measure of how many questions have a
        figure in their answer, reported as a measure of retrieval quality.
        """
        return self._rate([o for o in self.answerable if o.support_measurable], "supported")

    @property
    def support_not_measurable(self) -> int:
        return sum(1 for o in self.answerable if not o.support_measurable)

    def by_category(self) -> dict[str, dict]:
        """Per-category counts, because one rate over ten questions hides which kind is failing.

        A corpus-wide 70 percent could be every question about tables failing and every question about prose
        passing, and the two call for different work. The counts are the smallest thing that tells them apart.
        """
        categories: dict[str, dict] = {}
        for outcome in self.outcomes:
            bucket = categories.setdefault(outcome.category, {"n": 0, "retrieved": 0, "supported": 0,
                                                              "answerable": 0})
            bucket["n"] += 1
            bucket["retrieved"] += int(outcome.retrieved_document)
            bucket["supported"] += int(outcome.supported)
            bucket["answerable"] += int(outcome.answerable)
        for name, bucket in categories.items():
            bucket["retrieval_rate"] = bucket["retrieved"] / bucket["n"]
            bucket["support_rate"] = bucket["supported"] / bucket["n"]
        return dict(sorted(categories.items()))

    def unanswerable_scores(self) -> list[float]:
        return [o.top_score for o in self.unanswerable if o.top_score is not None]

    def answerable_scores(self) -> list[float]:
        return [o.top_score for o in self.answerable if o.top_score is not None]

    @property
    def supported_on_nominal_pages(self) -> int:
        """How many supported answers came from a document with no real pages.

        The number that keeps the page rule honest. A citation from a pageless filing is a citation with a
        nominal page, and a run where most of the successes are of that kind has tested the page rule on the
        documents where it cannot fail.
        """
        return sum(1 for o in self.answerable if o.supported and o.page_is_nominal)

    @property
    def scores_overlap(self) -> bool | None:
        """Whether the top scores on unanswerable questions reach into the answerable range.

        The measurement a refusal threshold would be built on. If the weakest answerable question scores below
        the strongest unanswerable one, no threshold on the top score separates "the corpus answers this" from
        "it does not", and a refusal has to come from somewhere other than confidence. Reporting that is more
        useful than reporting zero refusals on a layer that does not refuse.
        """
        asked, unasked = self.answerable_scores(), self.unanswerable_scores()
        if not asked or not unasked:
            return None
        return max(unasked) >= min(asked)

    def as_dict(self) -> dict:
        return {
            "configuration": self.configuration,
            "k": self.k,
            "n": len(self.outcomes),
            "retrieval_rate": self.retrieval_rate,
            "support_rate": self.support_rate,
            "by_category": self.by_category(),
            "scores_overlap": self.scores_overlap,
            "supported_on_nominal_pages": self.supported_on_nominal_pages,
            "answerable_top_scores": sorted(self.answerable_scores(), reverse=True),
            "unanswerable_top_scores": sorted(self.unanswerable_scores(), reverse=True),
            "outcomes": [
                {"id": o.question_id, "category": o.category, "answerable": o.answerable,
                 "retrieved_document": o.retrieved_document, "supported": o.supported, "rank": o.rank,
                 "top_score": o.top_score, "page_is_nominal": o.page_is_nominal, "detail": o.detail}
                for o in self.outcomes],
        }

    def render(self) -> str:
        lines = [f"configuration: {self.configuration}   k={self.k}   n={len(self.outcomes)}"]
        for label, rate, attribute in (("retrieval", self.retrieval_rate, "retrieved_document"),
                                       ("support", self.support_rate, "supported")):
            if rate is None:
                lines.append(f"  {label:<10} n/a")
                continue
            denominator = len(self.answerable) if label == "retrieval" else (
                len(self.answerable) - self.support_not_measurable)
            count = sum(1 for o in self.answerable if getattr(o, attribute))
            lines.append(f"  {label:<10} {rate:.0%} ({count}/{denominator})")
        if self.support_not_measurable:
            lines.append(f"  support was not measurable on {self.support_not_measurable} answerable question(s): "
                         f"no figure in the reference answer, so it needs entailment")
        lines.append("")
        lines.append(f"  {'category':<18}{'n':>3}{'retr':>7}{'supp':>7}")
        for name, bucket in self.by_category().items():
            lines.append(f"  {name:<18}{bucket['n']:>3}{bucket['retrieval_rate']:>7.0%}"
                         f"{bucket['support_rate']:>7.0%}")
        if self.supported_on_nominal_pages:
            lines.append("")
            lines.append(f"  supported answers from documents with no real pages: "
                         f"{self.supported_on_nominal_pages} of "
                         f"{sum(1 for o in self.answerable if o.supported)}. For those the page rule is "
                         f"satisfied trivially and has not been tested.")
        if self.scores_overlap is not None:
            lines.append("")
            lines.append(f"  unanswerable top scores reaching into the answerable range: {self.scores_overlap}")
            lines.append(f"    answerable   {[round(s, 3) for s in sorted(self.answerable_scores(), reverse=True)]}")
            lines.append(f"    unanswerable {[round(s, 3) for s in sorted(self.unanswerable_scores(), reverse=True)]}")
        lines.append("")
        for outcome in self.outcomes:
            mark = "ok " if (outcome.supported or not outcome.answerable) else "MISS"
            lines.append(f"  {mark} {outcome.question_id:<5}{outcome.category:<18}"
                         f"{'doc' if outcome.retrieved_document else '   '}"
                         f"{' ans' if outcome.supported else '    '}"
                         f"  rank={outcome.rank}  {outcome.detail}")
        return "\n".join(lines)


def grade(question: Question, passages: list, k: int, document_paths: dict[str, str] | None = None) -> Outcome:
    """Score one question's passages.

    Support is checked by looking for the answer's figures in the passage text rather than for the answer string,
    because the answer is written in prose and the document is written in a table. `184,500` in a publication
    and `184500 dollars` in a reference answer are the same fact, and requiring the strings to match would score
    correct retrieval as a miss.
    """
    outcome = Outcome(question_id=question.id, category=question.category, answerable=question.answerable,
                      documents=[getattr(p, "doc_id", "") for p in passages[:k]])
    if passages:
        outcome.top_score = getattr(passages[0], "score", None)

    if not question.answerable:
        # Nothing to find. The outcome is recorded for its top score, which is what the overlap measurement
        # above uses, and `supported` stays false because there is no support to find.
        outcome.detail = question.note or "not answerable from this corpus"
        return outcome

    # Resolved through the manifest when one is supplied, so a question can name a document by its identity and
    # still be matched against the filename the corpus holds. Without a manifest the id is used as it stands,
    # which is what a test supplies.
    resolve = document_paths or {}

    def file_for(doc_id: str) -> str:
        return resolve.get(doc_id) or doc_id

    wanted_docs = set()
    if question.source_document == "both":
        wanted_docs = {"apple-ex10-1-2024", "apple-ex10-22-2024"}
    elif question.source_document:
        wanted_docs = {file_for(question.source_document)}

    for rank, passage in enumerate(passages[:k], start=1):
        doc = getattr(passage, "doc_id", "") or ""
        # Compared on the stem, because the manifest records `irs-p15.pdf` and the chunk records
        # `company/irs-p15.pdf`: the root directory is part of the ingested identity and not part of the corpus
        # manifest's.
        if any(Path(wanted).stem in doc for wanted in wanted_docs):
            outcome.retrieved_document = True
            break
    if not wanted_docs:
        # No document named, so the retrieval half is not measurable and only support is.
        outcome.retrieved_document = True
        outcome.detail = "no source document named; only support is measurable"

    if question.reference_answer:
        # Graded on the answer's most specific figure when it has one, and on a share of its distinctive words
        # when it does not. The first version required every figure in the answer to appear in one passage and
        # scored Q06 and Q07 as misses while the retriever had put the right table in front of the model: the
        # reference answers explain what they found, so they carry the fiscal year and a date beside the figure.
        figure = primary_figure(question.reference_answer)
        if not figure:
            # Support is NOT measured for an answer with no figure, and this is a decision rather than a gap.
            #
            # The first version fell back to counting shared words and needed 60 percent of them. That number
            # was arbitrary and the mechanism is weak in a way no number fixes: a paraphrase question and its
            # source legitimately share almost no wording, so "forfeited on termination" against "shall
            # terminate on the Severance Date" matched on two stems out of seven and was scored a miss while the
            # passage said exactly that. Counting shared vocabulary measures vocabulary and reports it as
            # support, which is a number that means nothing dressed as one that means something.
            #
            # Support for prose needs entailment, which is Layer 1 and arrives with the answer layer. Until then
            # the honest report says this question's support was not measured, excludes it from the rate rather
            # than counting it as a miss, and prints the count so the rate's coverage is visible beside it.
            outcome.support_measurable = False
            outcome.detail = ("no figure in the reference answer, so support needs entailment and is not "
                              "measurable at this layer. Excluded from the support rate rather than counted "
                              "as a miss.")
            return outcome

        for rank, passage in enumerate(passages[:k], start=1):
            text = getattr(passage, "text", "") or ""
            if re.sub(r"\D", "", figure) in re.sub(r"\D", "", text):
                outcome.supported, outcome.rank = True, rank
                outcome.detail = f"matched the figure {figure}"
                outcome.page_is_nominal = bool(getattr(passage, "page_is_nominal", False))
                break
        if not outcome.supported:
            outcome.detail = f"no passage among the top {k} contains the figure {figure}"
    else:
        outcome.detail = (outcome.detail + " | " if outcome.detail else "") + \
            "no reference answer, so support is not measurable"
    return outcome


def run(questions: list[Question], search, k: int = 10, configuration: str = "default",
        document_paths: dict[str, str] | None = None) -> Report:
    """Ask every question and grade it. `search` takes a question and returns passages, best first."""
    report = Report(configuration=configuration, k=k)
    for question in questions:
        try:
            passages = search(question.question)
        except Exception as exc:  # noqa: BLE001
            # A question that errors is recorded as a miss rather than aborting the run, because a run that
            # stops on the first failure cannot show which questions a change broke.
            report.outcomes.append(Outcome(
                question_id=question.id, category=question.category, answerable=question.answerable,
                detail=f"{type(exc).__name__}: {exc}"))
            continue
        report.outcomes.append(grade(question, passages, k, document_paths))
    return report


def compare(before: Report, after: Report) -> str:
    """Two configurations on the same questions, and what moved.

    The same questions is the requirement, and it is why this takes two reports rather than re-running: a
    comparison across two different question sets measures the sets.
    """
    ids_before = {o.question_id for o in before.outcomes}
    ids_after = {o.question_id for o in after.outcomes}
    if ids_before != ids_after:
        missing = ids_before ^ ids_after
        return (f"the two reports do not cover the same questions, so comparing them would measure the sets "
                f"rather than the configurations: {sorted(missing)}")

    lines = [f"  {'':<18}{before.configuration:>14}{after.configuration:>16}{'change':>10}"]
    for label, attribute in (("retrieval", "retrieval_rate"), ("support", "support_rate")):
        a, b = getattr(before, attribute), getattr(after, attribute)
        if a is None or b is None:
            lines.append(f"  {label:<18}{'n/a':>14}{'n/a':>16}{'':>10}")
            continue
        delta = b - a
        arrow = "same" if abs(delta) < 1e-9 else ("better" if delta > 0 else "worse")
        lines.append(f"  {label:<18}{a:>13.0%}{b:>15.0%}{delta:>+9.0%}  {arrow}")

    changed = [f"    {o.question_id}: {o.detail}" for o, n in
               ((o, next((x for x in after.outcomes if x.question_id == o.question_id), None))
                for o in before.outcomes)
               if n and o.supported != n.supported]
    if changed:
        lines.append("  questions whose support changed:")
        lines.extend(changed)
    return "\n".join(lines)


def main() -> int:
    """Run against a deployment. The caller supplies the search function; this is the plain RAG one."""
    import os
    import sys

    # Where the other services live. In the repository this file sits beside them and the relative paths work.
    # In the tool's image it does not: `__file__` is /app/harness.py and the repository is mounted at /w, so the
    # relative path resolves to /retrieval and the import fails with "No module named 'ingest'". Found by
    # running it, and the environment variables are how the image says where the checkout is.
    for candidate in (
        Path(os.environ.get("RETRIEVAL_PATH", "")) if os.environ.get("RETRIEVAL_PATH") else None,
        Path(os.environ.get("INGESTION_PATH", "")) if os.environ.get("INGESTION_PATH") else None,
    ):
        if candidate:
            sys.path.insert(0, str(candidate))
    for name in ("retrieval", "ingestion"):
        beside = Path(__file__).resolve().parent.parent / name
        if beside.is_dir():
            sys.path.insert(0, str(beside))

    import ingest
    import retriever

    questions_path = os.environ.get("QUESTIONS", "/w/fixtures/corpus/questions.yaml")
    manifest_path = os.environ.get("CORPUS_MANIFEST", "/w/fixtures/corpus/manifest.yaml")
    role = os.environ.get("ROLE", "staff")
    k = int(os.environ.get("K", "10"))
    configuration = os.environ.get("CONFIGURATION", "fused")
    services = ingest.Services()

    def search(question: str):
        return retriever.search(services, question, role=role, limit=k).passages

    report = run(load_questions(questions_path), search, k=k, configuration=configuration,
                 document_paths=load_document_paths(manifest_path))
    print(report.render())
    out = os.environ.get("REPORT_OUT")
    if out:
        Path(out).write_text(json.dumps(report.as_dict(), indent=2, sort_keys=True) + "\n")
        print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
