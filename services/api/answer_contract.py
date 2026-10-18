"""The contract between the retrieved passages and the answer, kept out of the FastAPI app so it is testable.

**Why this exists.** The measured failures on 2026-10-18 were not evenly spread: of 32 question-runs that failed
across four identical suite runs, 18 had the needed value sitting in the retrieved passages and still came back
refused, citation-only, or wrong. Retrieval was no longer the main problem. The answer layer was, and it was a
prompt and a post-check rather than a model capability, because Nova Pro scored the same as Nova Lite.

So two things live here, and both are deliberate:

1. **The prompt states what an answer is.** Value first, cite the passage that carries it, no narration about the
   passages, no citation without an answer, refuse only when the value is genuinely absent.
2. **A post-check that can refuse an unsupported figure.** If the answer states numbers and every one of them is
   absent from the retrieved context, that is a fabricated figure or a badly miscopied one, and the honest result
   is a refusal with the passages rather than a confident number. An arithmetic answer is still allowed: when the
   operands are present and only the result is new, the context carries part of the answer and the check passes.

The check is intentionally not "every number must appear". A cross-document subtraction legitimately produces a
number that is in no passage, and refusing those would turn a working feature into a refusal. The line is drawn at
answers whose entire evidence is missing.
"""
from __future__ import annotations

import re

CITATION = re.compile(r"\[\d+\]")
# Numbers as a reader would write them: 77,258 and 1.45 and 315400. A bare year is a number too, which is why
# the check is about the whole answer rather than about one token.
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")

ANSWER_PROMPT = (
    "Answer the question using only the numbered passages below.\n"
    "Rules:\n"
    "1. Put the answer in the first sentence. If the question asks for a figure, quote the figure exactly as it "
    "appears in the passage, including its units.\n"
    "2. Put the citation for the passage that contains the answer immediately after the sentence, as [n].\n"
    "3. Do not describe the passages, do not repeat the question, and do not narrate your search. Answer it.\n"
    "4. Do not reply with only a citation. A citation is evidence for an answer, not an answer.\n"
    "5. If answering takes two passages combined, such as a difference, a ratio or a comparison, do that and "
    "cite both passages. Only refuse when a needed value is absent.\n"
    "6. If the passages do not contain the answer, reply exactly: I could not find that in the available "
    "documents. Do not use outside knowledge and do not guess.\n"
    "7. A figure copied from a table row is a valid answer. Check every passage before concluding the answer is "
    "absent.\n\n"
    "Passages:\n{context}\n\nQuestion: {question}"
)

REFUSAL = "i could not find that in the available documents"


def build_prompt(context: str, question: str) -> str:
    return ANSWER_PROMPT.format(context=context, question=question)


def _digits(text: str) -> str:
    """Digits only, so a figure copied as 77,258 and stored as 77258 compare equal."""
    return "".join(character for character in text if character.isdigit())


def numbers_in(answer: str) -> list[str]:
    return NUMBER.findall(CITATION.sub(" ", answer or ""))


def citation_only(answer: str) -> bool:
    stripped = CITATION.sub("", answer or "")
    return not any(character.isalnum() for character in stripped)


def unsupported_numbers(answer: str, passage_texts: list[str]) -> list[str]:
    """Numbers in the answer whose digits appear in none of the passages."""
    context = _digits(" ".join(passage_texts))
    return [number for number in numbers_in(answer) if _digits(number) not in context]


def violation(answer: str, passage_texts: list[str]) -> str | None:
    """The reason this answer must be sent back to the model, or None when it may be returned.

    Empty is a violation. Citation-only is a violation. A figure with no presence anywhere in the passages is a
    violation only when *no* number in the answer is supported, so an arithmetic result beside its operands
    passes.
    """
    text = (answer or "").strip()
    if not text:
        return "empty_answer"
    if citation_only(text):
        return "citation_only"
    stated = numbers_in(text)
    if stated and not any(_digits(number) in _digits(" ".join(passage_texts)) for number in stated):
        return "unsupported_number"
    return None


def corrective_message(reason: str) -> str:
    """The follow-up sent with the second attempt. One corrective instruction, not a second answer attempt."""
    if reason == "citation_only":
        return ("Your previous reply was only a citation. State the answer itself in the first sentence, then "
                "the citation.")
    if reason == "unsupported_number":
        return ("Your previous reply stated a figure that does not appear in the passages. Answer again using "
                "only figures that appear verbatim in the passages, and if the answer is not there, say "
                "exactly: I could not find that in the available documents.")
    return ("Your previous reply was empty. Answer the question from the passages, or say exactly: I could not "
            "find that in the available documents.")
