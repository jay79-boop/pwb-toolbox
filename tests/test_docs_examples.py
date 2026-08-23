"""Check that the code examples in ``docs/`` still match the real signatures.

The converter for this was a real miss: the config-object refactor changed
``run_strategy`` to take a ``StrategyConfig`` and left ``docs/backtesting.md``
calling it with nine keyword arguments. Nothing caught it -- the examples are
prose to pytest, and a doc that would crash as written costs a reader far more
than a stale sentence does.

So each ``python`` block is parsed, its *own* imports are read to learn what
each name refers to, and every call landing in ``pwb_toolbox`` is bound against
the live signature with :func:`inspect.Signature.bind`. A ``TypeError`` there is
an example that would crash.

Binding is deliberately the only assertion made. Nothing is executed -- the
examples reach for the network, a broker and a live cerebro -- and argument
*values* are not modelled, only their names and arity. That catches the drift
this class of bug is made of (a renamed function, a dropped parameter, a
signature that grew a required argument) without pretending to run the docs.

The two tests that matter are the pair: :func:`test_docs_examples_match_signatures`
would pass just as happily if the scanner resolved nothing at all, so
:func:`test_scanner_reports_real_breakage` injects known-bad calls and requires
them to be found, and :func:`test_scanner_reaches_every_documented_module` pins
the coverage floor. An earlier cut of this scanner silently resolved 8 of 43
calls and reported the docs clean; the floor is what makes that a failure
rather than a green tick.
"""

import ast
import importlib
import inspect
import pathlib

import pytest

DOCS = pathlib.Path(__file__).resolve().parent.parent / "docs"
PKG = "pwb_toolbox"

#: Every docs file that carries a checkable example, and the least number of
#: calls the scanner must still resolve in it. Floors rather than exact counts:
#: adding an example should not fail the suite, but a scanner that quietly
#: stops seeing a file must.
COVERAGE_FLOOR = {
    "backtesting.md": 2,
    "converting.md": 2,
    "datasets.md": 1,
    "execution.md": 8,
    "scraping.md": 4,
}


def _blocks(text):
    """(line number, source) for each ```python fence in a markdown file."""
    out, lines, i = [], text.splitlines(), 0
    while i < len(lines):
        if lines[i].strip() == "```python":
            start = i + 1
            j = start
            while j < len(lines) and lines[j].strip() != "```":
                j += 1
            out.append((start + 1, "\n".join(lines[start:j])))
            i = j
        i += 1
    return out


def _scope(tree):
    """The names a block's own imports bind, split by what they refer to.

    ``from pwb_toolbox import execution as pwb_exec`` binds a *submodule*, so
    calls on it are attribute access rather than a bare name -- filing it as a
    symbol makes every call in ``execution.md`` invisible. Which one it is is
    settled by trying the import, not by guessing from the syntax.
    """
    modules, symbols = {}, {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(PKG):
                    modules[alias.asname or alias.name.split(".")[0]] = alias.name
        elif isinstance(node, ast.ImportFrom):
            if not (node.module or "").startswith(PKG):
                continue
            for alias in node.names:
                dotted = f"{node.module}.{alias.name}"
                try:
                    importlib.import_module(dotted)
                except ImportError:
                    symbols[alias.asname or alias.name] = (node.module, alias.name)
                else:
                    modules[alias.asname or alias.name] = dotted
    return modules, symbols


def _resolve(func, modules, symbols, receivers):
    """``(label, object, is_bound)`` for a call, or None when it is not ours."""
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        name = func.value.id
        cls = receivers.get(name)
        if cls is not None:
            # `ibc.get_positions()` -- reached off the class, so `self` is a
            # parameter the written call does not supply.
            return f"{name}.{func.attr}", getattr(cls, func.attr, None), True
        dotted = modules.get(name)
        if dotted is None:
            return None
        module = importlib.import_module(dotted)
        return f"{name}.{func.attr}", getattr(module, func.attr, None), False
    if isinstance(func, ast.Name):
        found = symbols.get(func.id)
        if found is None:
            return None
        module = importlib.import_module(found[0])
        return func.id, getattr(module, found[1], None), False
    return None


def _receivers(tree, modules, symbols):
    """``var -> class`` for ``var = SomeClassOfOurs(...)``.

    Without this the connector and store examples are skipped: their methods
    are called on a local, so there is no import to resolve them through.
    """
    found = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        hit = _resolve(node.value.func, modules, symbols, {})
        if hit and isinstance(hit[1], type):
            found[node.targets[0].id] = hit[1]
    return found


def scan(text, filename="<docs>"):
    """``(findings, checked)`` for one markdown document.

    A finding is ``(location, label, reason)``; ``checked`` lists the labels
    actually bound, which is what the coverage floor is asserted against.
    """
    findings, checked = [], []
    for offset, source in _blocks(text):
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue  # an illustrative fragment, not a whole program
        modules, symbols = _scope(tree)
        if not modules and not symbols:
            continue
        receivers = _receivers(tree, modules, symbols)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            hit = _resolve(node.func, modules, symbols, receivers)
            if hit is None:
                continue
            label, obj, is_bound = hit
            where = f"{filename}:{offset + node.lineno - 1}"
            if obj is None:
                findings.append((where, label, "no such name in the package"))
                continue
            if not callable(obj):
                continue
            try:
                signature = inspect.signature(obj)
            except (TypeError, ValueError):
                continue
            if any(isinstance(a, ast.Starred) for a in node.args) or any(
                k.arg is None for k in node.keywords
            ):
                continue  # unpacking hides the real arity
            checked.append(label)
            positional = [None] * (len(node.args) + (1 if is_bound else 0))
            try:
                signature.bind(*positional, **{k.arg: None for k in node.keywords})
            except TypeError as exc:
                findings.append((where, label, str(exc)))
    return findings, checked


def _scan_all():
    findings, checked = [], {}
    for path in sorted(DOCS.glob("*.md")):
        found, seen = scan(path.read_text(encoding="utf-8"), path.name)
        findings.extend(found)
        if seen:
            checked[path.name] = seen
    return findings, checked


def test_docs_examples_match_signatures():
    """No example in docs/ calls a pwb_toolbox function that would reject it."""
    findings, _ = _scan_all()
    assert not findings, "stale signatures in docs/:\n" + "\n".join(
        f"  {where}  {label}() -> {why}" for where, label, why in findings
    )


@pytest.mark.parametrize("filename,floor", sorted(COVERAGE_FLOOR.items()))
def test_scanner_reaches_every_documented_module(filename, floor):
    """The scan must keep resolving calls in each file that documents the API.

    Guards the failure mode that makes the test above worthless: a scanner
    which resolves nothing reports every document clean.
    """
    _, checked = _scan_all()
    assert filename in checked, f"{filename} resolved no calls at all"
    assert len(checked[filename]) >= floor, (
        f"{filename} resolved {len(checked[filename])} calls, expected >= {floor}; "
        "the scanner has stopped seeing calls it used to check"
    )


@pytest.mark.parametrize(
    "block,expected",
    [
        # Every required argument is supplied, so the unknown keyword is the
        # only thing left to fail on -- `bind` reports the first problem it
        # hits, and a probe missing a required argument would pass this test
        # while proving nothing about unknown keywords.
        pytest.param(
            "import pwb_toolbox.datasets as pwb_ds\n"
            "df = pwb_ds.load_dataset('Stocks-Daily-Price', no_such_option=1)\n",
            "no_such_option",
            id="unknown-keyword",
        ),
        pytest.param(
            "import pwb_toolbox.datasets as pwb_ds\n"
            "df = pwb_ds.load_dataset_renamed('Stocks-Daily-Price')\n",
            "no such name",
            id="renamed-function",
        ),
        pytest.param(
            "from pwb_toolbox.scraping import ScriptStore\n"
            "store = ScriptStore('x')\n"
            "store.records(bogus=1)\n",
            "bogus",
            id="bad-argument-on-method",
        ),
        pytest.param(
            "from pwb_toolbox.converting import convert\n" "convert()\n",
            "missing a required argument",
            id="dropped-required-argument",
        ),
    ],
)
def test_scanner_reports_real_breakage(block, expected):
    """Each way a doc goes stale is actually detected.

    A checker that has only ever returned "clean" has not been shown to work.
    """
    findings, _ = scan(f"```python\n{block}```\n", "probe.md")
    assert findings, f"scanner missed a broken call:\n{block}"
    assert any(
        expected in why for _, _, why in findings
    ), f"detected something other than the injected fault: {findings}"


def test_scanner_ignores_blocks_that_are_not_ours():
    """Third-party and non-parsing blocks are passed over, not reported."""
    findings, checked = scan(
        "```python\nimport pandas as pd\npd.DataFrame(anything=1)\n```\n"
        "```python\ndef broken(:\n```\n",
        "probe.md",
    )
    assert findings == []
    assert checked == []
