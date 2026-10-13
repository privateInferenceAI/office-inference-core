"""Every fixture script parses and states its claim, because nothing else checks them.

The fixtures under `tests/fixtures/` are the acceptance probes: they are run by hand on the box, by
`docs/runbook.md` steps and by the manual's operating pages, and they are the only thing standing between a
documented procedure and a reader discovering it is broken. **Nothing imports them and nothing specifies
them.** The unit tests cover `services/`; the fixtures call into it and are never themselves executed by
`pytest`, because they need a database, a vector store, an embedding service and a reranker.

That arrangement is how `ask.py` shipped with `--verify` dying on an `AttributeError` for every caller: the
default path worked, the flag did not, and no test touched either. A fixture is a program, and the cheapest
part of checking a program is that it parses.

What these tests prove is deliberately narrow: each fixture compiles, and each says what it proves. They
prove nothing about behaviour. They are here because the failure they catch — a probe that cannot start —
is one a reviewer finds only by running the exact command the manual documents, and the runbook's own
history says that happens late. A fixture that cannot be compiled is a documented command that will fail in
a reader's hands.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_scripts() -> list[Path]:
    return sorted(p for p in FIXTURES.glob("*.py") if not p.name.startswith("__"))


SCRIPTS = fixture_scripts()


def test_there_are_fixtures_to_check() -> None:
    """A glob that silently matched nothing would make every test below vacuous."""
    assert len(SCRIPTS) >= 20, f"only found {len(SCRIPTS)} fixtures; is the path right?"


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_fixture_compiles(path: Path) -> None:
    source = path.read_text()
    try:
        compile(source, str(path), "exec")
    except SyntaxError as exc:
        pytest.fail(f"{path.name} does not compile: {exc}")


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_fixture_states_its_claim(path: Path) -> None:
    """Each fixture says what it proves.

    Not decoration: these scripts are cited by name from the runbook and the manual, and a fixture whose
    docstring does not state its claim leaves a reader unable to tell what a pass means. The convention is
    old and every existing fixture follows it, so a new one that does not is a break rather than a style
    preference.
    """
    docstring = ast.get_docstring(ast.parse(path.read_text()))
    assert docstring, f"{path.name} has no module docstring"
    assert len(docstring.strip()) > 40, f"{path.name}'s docstring says too little to be a claim"
