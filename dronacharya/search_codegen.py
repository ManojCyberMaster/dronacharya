""""Find everything matching X" — for enumeration questions ("list all my
passwords and where they're used", "every item on my wish lists") that
plain top-k semantic retrieval structurally cannot answer: it finds the
best-matching passages, not every matching one, and a generic reference
doc often out-scores the one terse line that actually matters.

Design: ONE LLM call writes a small search function from the user's
QUESTION ALONE (it never sees document content, so nothing embedded in a
saved page can steer what code gets written); that function then runs,
unmodified, over every stored unit in a restricted sandbox — fast enough
to cover the whole knowledge base without a per-batch LLM call for each
chunk of it. Scope is strictly read-only text matching over the caller's
own already-ingested units: the generated code gets three plain strings
per unit and must return a string or None, nothing else.
"""

from __future__ import annotations

import ast
import builtins
import multiprocessing as mp
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config
    from .embeddings import Embedder

MATCHER_SYSTEM = """\
You write ONE small, safe Python function that searches a personal
knowledge base for items matching the user's request. Define exactly:

    def match(text, heading, title):
        ...
        return "<short human-readable string describing what matched>"
        # or: return None   (this unit does not match)

Inputs: `text` is a stored note/document fragment, `heading` is its
section breadcrumb (may be empty), `title` is its document title. These
three strings are the ONLY things you can look at.

Rules:
- The `re` module is already available as `re` — do not import it or
  anything else. No import statements at all.
- Only string methods, `re.search/match/findall/finditer`, basic control
  flow (if/for/while, comprehensions), and these builtins: len, range,
  enumerate, sorted, min, max, sum, any, all, zip, str, int, float, bool,
  list, dict, set, tuple, isinstance, abs, round, map, filter, reversed.
- Never use eval, exec, compile, open, getattr, setattr, globals, locals,
  __import__, or any name starting with an underscore. You have no file,
  network, or system access — text/heading/title in, a string or None out.
- Match generously (case-insensitive, several likely phrasings) since you
  only get one pass over each unit — but return None for clear non-matches
  rather than guessing.
- The returned string is shown to the user as the found item, so make it
  the actual useful content (the value, the list entry, the fact) — not
  just "matched" or "yes".
Output ONLY the function definition. No prose, no markdown fences.
"""

_ALLOWED_NODES = (
    ast.Module, ast.FunctionDef, ast.arguments, ast.arg,
    ast.Return, ast.If, ast.For, ast.While, ast.Break, ast.Continue, ast.Pass,
    ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Expr,
    ast.Call, ast.BinOp, ast.BoolOp, ast.UnaryOp, ast.Compare, ast.IfExp,
    ast.List, ast.Dict, ast.Set, ast.Tuple, ast.Subscript, ast.Slice,
    ast.Name, ast.Load, ast.Store, ast.Constant, ast.Attribute, ast.Starred,
    ast.comprehension, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp,
    ast.JoinedStr, ast.FormattedValue,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.FloorDiv, ast.Pow,
    ast.And, ast.Or, ast.Not, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt,
    ast.GtE, ast.In, ast.NotIn, ast.USub, ast.UAdd, ast.Invert, ast.BitAnd,
    ast.BitOr, ast.BitXor,
)
_FORBIDDEN_NAMES = {
    "eval", "exec", "compile", "open", "__import__", "getattr", "setattr",
    "delattr", "globals", "locals", "vars", "input", "help", "exit", "quit",
    "breakpoint", "memoryview", "classmethod", "staticmethod", "property",
    "type", "super", "object",
}
_SAFE_BUILTIN_NAMES = (
    "len", "range", "enumerate", "sorted", "min", "max", "sum", "any",
    "all", "zip", "str", "int", "float", "bool", "list", "dict", "set",
    "tuple", "isinstance", "abs", "round", "map", "filter", "reversed",
)


def _strip_fences(code: str) -> str:
    s = code.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else ""
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


def validate_matcher_code(code: str) -> None:
    """Raise ValueError with a specific reason on anything outside the
    whitelisted subset. Runs before the code ever touches real data."""
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as e:
        raise ValueError(f"generated code has a syntax error: {e}") from e
    funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    if len(tree.body) != 1 or not funcs or funcs[0].name != "match":
        raise ValueError("generated code must define exactly one top-level function named match(...)")
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ValueError(f"disallowed syntax: {type(node).__name__}")
        if isinstance(node, ast.Name) and (node.id.startswith("_") or node.id in _FORBIDDEN_NAMES):
            raise ValueError(f"disallowed name: {node.id}")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ValueError(f"disallowed attribute access: {node.attr}")


def _child_worker(code: str, units: list[tuple[str, str, str]], out_queue) -> None:
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    except Exception:  # noqa: BLE001 — best-effort hardening, not fatal if unavailable
        pass
    safe_builtins = {name: getattr(builtins, name) for name in _SAFE_BUILTIN_NAMES}
    namespace = {"__builtins__": safe_builtins, "re": re}
    try:
        exec(compile(code, "<matcher>", "exec"), namespace)  # noqa: S102 — validated above, sandboxed here
        match_fn = namespace["match"]
    except Exception as e:  # noqa: BLE001
        out_queue.put({"error": f"matcher failed to load: {e}"})
        return
    results = []
    for i, (text, heading, title) in enumerate(units):
        try:
            r = match_fn(text, heading, title)
        except Exception:  # noqa: BLE001 — one bad unit must not kill the scan
            continue
        if r:
            results.append((i, str(r)[:500]))
    out_queue.put({"matches": results})


def run_matcher(code: str, units: list[tuple[str, str, str]], timeout: float = 30.0) -> list[tuple[int, str]]:
    validate_matcher_code(code)
    # "spawn", never "fork": by the time this runs, the caller (CLI/server)
    # has usually already loaded the embedder/reranker (torch/CUDA) —
    # forking a CUDA-initialized process is unsafe and can silently corrupt
    # the child's execution instead of crashing it (observed in testing:
    # code that provably always raises produced plausible-looking fake
    # matches under fork). spawn starts a genuinely fresh interpreter.
    ctx = mp.get_context("spawn")
    q: mp.Queue = ctx.Queue()
    proc = ctx.Process(target=_child_worker, args=(code, units, q))
    proc.start()
    proc.join(timeout)
    if proc.is_alive():
        proc.terminate()
        proc.join()
        raise TimeoutError("matcher took too long and was terminated")
    if q.empty():
        raise RuntimeError("matcher process produced no result (it likely crashed)")
    result = q.get()
    if "error" in result:
        raise RuntimeError(result["error"])
    return result["matches"]


@dataclass
class FindResult:
    code: str = ""
    provider: str = ""
    scanned: int = 0
    items: list[dict] = field(default_factory=list)


def find_all(repo, embedder: Embedder, config: Config, question: str, *,
            chain=None) -> FindResult:
    from .llm import get_provider_chain, run_complete

    chain = chain if chain is not None else get_provider_chain(config, task="answer")
    user_msg = f"Find everything matching: {question}"
    code, provider, err = "", "", None
    for _attempt in range(2):   # real models occasionally add a stray import
        code, provider = run_complete(chain, MATCHER_SYSTEM, user_msg, max_tokens=500)
        code = _strip_fences(code)
        try:
            validate_matcher_code(code)
            err = None
            break
        except ValueError as e:
            err = e
            user_msg = (f"Find everything matching: {question}\n\nYour previous "
                       f"attempt was rejected: {e}. Follow the rules exactly and "
                       "try again — output ONLY the function definition.")
    if err is not None:
        raise err

    rows: list[tuple[str, str, str]] = []
    meta: list[tuple] = []
    for doc, units in repo.iter_documents_with_units():
        for u in units:
            rows.append((u.text or "", u.heading_path or "", doc.title or ""))
            meta.append((doc, u))

    matches = run_matcher(code, rows)
    items = []
    for idx, matched_text in matches:
        doc, unit = meta[idx]
        items.append({
            "text": matched_text,
            "document": doc.title or "(untitled)",
            "document_id": doc.id,
            "where": unit.heading_path or "",
            "source": doc.url or doc.file_path or "",
        })
    repo.log_event("find_all", {"q": question[:200], "scanned": len(rows), "matches": len(items)})
    return FindResult(code=code, provider=provider, scanned=len(rows), items=items)
