#!/usr/bin/env python3
"""Collect the bounded repository context GitDiagram gives its model.

Ported (simplified) from gitdiagram's src/server/generate/{github,repository-context,
source-context,source-excerpt,source-references}.ts (MIT, Ahmed Khaleel).

Usage: collect_context.py <repo_dir> <out_dir>
Writes <out_dir>/context.md (the model input) and <out_dir>/repo.json (used by
compile_diagram.py to validate paths and edge evidence).
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

MAX_SOURCE_CHARACTERS = 48_000
MAX_SOURCE_FILES = 12
MAX_SOURCE_FILE_BYTES = 512_000
MAX_REFERENCE_FILES = 28
MAX_LISTED_FILES = 20
MAX_TREE_CHARACTERS = 24_000
MAX_README_CHARACTERS = 8_500
MAX_SOURCE_INDEX_CHARACTERS = 9_000
MAX_REFERENCES_SHOWN = 16
MAX_REFERENCES_PER_FILE = 40
MAX_CORE_MODULES = 16
MAX_EXCERPT_PER_FILE = 10_000

EXCLUDED_DIRECTORY_SEGMENTS = {
    "node_modules", "vendor", "venv", "__pycache__", ".cache", ".tmp", ".vscode", ".idea", ".git",
}
EXCLUDED_SUFFIXES = (
    ".pyc", ".pyo", ".pyd", ".so", ".dll", ".class", ".jpg", ".jpeg", ".png", ".gif", ".ico",
    ".svg", ".ttf", ".woff", ".woff2", ".webp", ".log", "yarn.lock", "poetry.lock",
)
EXCLUDED = re.compile(
    r"(^|/)(?:\.[^/]+|tests?|__tests__|__mocks__|mocks?|testdata|fixtures?|e2e|cypress|examples?(?:_src)?"
    r"|samples?|demos?|stories|storybook|docs?(?:_src)?|tutorials?(?:_src)?|documentation|bench|benchmarks?"
    r"|vendor|third_party|node_modules|dist|build|generated|migrations?|alembic|assets|locales?|translations?"
    r"|experiments?|playground)(/|$)"
    r"|(?:\.test(?:-d)?|\.spec|\.generated|\.min|\.stories|\.designer)\."
    r"|(?:_pb2(?:_grpc)?\.py|\.pb(?:\.gw)?\.go|\.g\.cs)$"
    r"|(?:^|/)(?:test\.[^/]+|bench(?:mark|marker)?\.[^/]+|test_[^/]+|[^/]+_test\.[^/]+)$",
    re.I,
)
SOURCE = re.compile(
    r"\.(?:[cm]?[jt]sx?|py|go|rs|java|kt|kts|swift|cs|cpp|cc|c|h|hpp|rb|php|ex|exs|scala|clj|vue|svelte|proto|graphql)$",
    re.I,
)
MANIFEST = re.compile(
    r"(?:^|/)(?:package\.json|Cargo\.toml|go\.mod|pyproject\.toml|requirements\.txt|build\.gradle(?:\.kts)?"
    r"|mix\.exs|composer\.json|Gemfile|CMakeLists\.txt)$",
    re.I,
)
SENSITIVE = re.compile(
    r"(?:^|/)(?:.*(?:secrets?|credentials?|passwords?|private[_-]?key).*|\.env.*|.*\.(?:pem|key|p12|pfx))$", re.I
)
SUPPORTING_FILE = re.compile(
    r"(?:^|/)(?:types?|index|mod|__init__|constants?|utils?|helpers?|errors?|[^/]*Error)\.[^/]+$|\.d\.ts$", re.I
)
README = re.compile(r"^readme(?:\.[a-z0-9]+)?$", re.I)
BARREL = re.compile(r"(?:^|/)(?:index|mod|__init__)\.[^/]+$", re.I)
JS_SOURCE = re.compile(r"\.(?:[cm]?[jt]sx?|vue|svelte)$", re.I)
JS_EXTENSIONS = [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".vue", ".svelte"]
KEY_LINE = re.compile(
    r"(?:constructor\s*\(|\b(?:onCreate|__init__|main|setup|initialize|create_app|ServeHTTP|handle_request"
    r"|dispatch_request|POST|GET|PUT|DELETE|PATCH)\s*\(|func\s+New\w*\s*\(|@(?:app|router|bp|blueprint)\.\w+"
    r"|^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function|class|def|fn|func)\s)",
    re.I,
)


def should_include_file(path: str) -> bool:
    lower = path.lower()
    if ".min." in lower or lower.endswith(EXCLUDED_SUFFIXES):
        return False
    return not any(seg in EXCLUDED_DIRECTORY_SEGMENTS for seg in lower.split("/"))


def is_architecture_source(path: str) -> bool:
    return not EXCLUDED.search(path) and not SENSITIVE.search(path) and bool(SOURCE.search(path) or MANIFEST.search(path))


def list_files(repo: Path) -> list[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "--cached", "--others", "--exclude-standard"],
            check=True, capture_output=True, text=True,
        ).stdout
        files = [line for line in out.splitlines() if line]
    except (subprocess.CalledProcessError, FileNotFoundError):
        files = []
        for root, dirs, names in os.walk(repo):
            dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRECTORY_SEGMENTS]
            for name in names:
                files.append(os.path.relpath(os.path.join(root, name), repo).replace(os.sep, "/"))
    return sorted(f for f in files if should_include_file(f) and (repo / f).is_file())


def score(path: str) -> int:
    name = path.split("/")[-1]
    value = 20 - len(path.split("/"))
    if MANIFEST.search(path):
        value += 5 if "/" in path else 45
    if re.match(r"^(?:main|apps?|server|applications?|Program)\.", name, re.I):
        value += 28
    if re.match(r"^(?:route|\+server|\+page\.server)\.[cm]?[jt]sx?$", name, re.I):
        value += 32
    if re.search(r"page-client\.[cm]?[jt]sx?$", name, re.I):
        value += 22
    if re.match(r"^page\.[jt]sx$", name, re.I):
        value += 16
    if re.match(r"^use[A-Z].*\.[cm]?[jt]sx?$", name):
        value += 22
    if re.match(r"^(?:index|lib|mod)\.", name, re.I):
        value += 22 if len(path.split("/")) <= 3 else 2
    if re.search(r"controller|manager|routes|query|ingest|search|auth|parser|context|session|templating", name, re.I):
        value += 10
    if re.search(
        r"webhook|router|routes|routing|handler|controller|tasks|worker|pipeline|engine|manager|repository"
        r"|storage|database|client|service|models?\.py|views?\.py",
        name, re.I,
    ):
        value += 22
    if re.search(r"pipeline|engine|orchestrat|review|retriev|embedding|inference|llm|rag|query|ingest", name, re.I):
        value += 18
    if re.search(r"config|types|constants|utils|helpers|schema", name, re.I):
        value -= 6
    if re.search(r"analytics|telemetry|instrumentation|logger|logging", name, re.I):
        value -= 25
    if re.search(r"(?:^|/)(?:healthz?|readyz?|livez?)(?:/|\.)", path, re.I):
        value -= 30
    if re.search(r"(?:^|/)scripts?/", path, re.I):
        value -= 35
    if re.match(r"^(?:testclient|conftest)\.", name, re.I):
        value -= 35
    if re.search(r"(?:activity|service)\.(?:kt|java)$", name, re.I):
        value += 18
    if re.match(r"^I[A-Z].*\.(?:java|kt|cs)$", name) or name.endswith(".d.ts"):
        value -= 20
    return value


def rank_source_paths(files: list[str], sizes: dict[str, int], limit: int) -> list[str]:
    base: dict[str, float] = {}
    for p in files:
        size = sizes.get(p, 0)
        if not is_architecture_source(p) or size > MAX_SOURCE_FILE_BYTES:
            continue
        # Tiny wrappers and small barrels should not crowd out substantial runtime modules.
        base[p] = (score(p) - (15 if size < 250 else 0) - (18 if size < 2000 and BARREL.search(p) else 0)
                   + min(10, math.log2(1 + size / 1000)))
    picked: list[str] = []
    dir_count: dict[str, int] = {}
    area_count: dict[str, int] = {}
    manifests = 0

    def directory(p: str) -> str:
        return p.rsplit("/", 1)[0] if "/" in p else ""

    def area(p: str) -> str:
        return "/".join(directory(p).split("/")[:2])

    # Soft diversity penalty: siblings can coexist, but one folder family cannot take every slot.
    def priority(p: str) -> float:
        return base[p] - 5 * dir_count.get(directory(p), 0) - 3 * area_count.get(area(p), 0)

    while len(picked) < limit:
        options = [p for p in base if p not in picked and not (MANIFEST.search(p) and manifests >= 1)]
        if not options:
            break
        best = min(options, key=lambda p: (-priority(p), p))
        picked.append(best)
        dir_count[directory(best)] = dir_count.get(directory(best), 0) + 1
        area_count[area(best)] = area_count.get(area(best), 0) + 1
        manifests += 1 if MANIFEST.search(best) else 0
    return picked


def read_text(repo: Path, path: str) -> str | None:
    try:
        data = (repo / path).read_bytes()
    except OSError:
        return None
    if len(data) > MAX_SOURCE_FILE_BYTES or b"\0" in data:
        return None
    return data.decode("utf-8", errors="replace")


def resolve_references(path: str, text: str, file_set: set[str], dirs: set[str]) -> list[str]:
    """Repository files a file imports or names anywhere in its text."""
    out: list[str] = []
    base = os.path.dirname(path)

    def add(candidate: str | None) -> None:
        if candidate and candidate != path and candidate not in out:
            out.append(candidate)

    def resolve_js(spec: str) -> str | None:
        target = os.path.normpath(os.path.join(base, spec)).replace(os.sep, "/")
        if target.startswith(".."):
            return None
        for cand in [target] + [target + e for e in JS_EXTENSIONS] + [f"{target}/index{e}" for e in JS_EXTENSIONS]:
            if cand in file_set:
                return cand
        # TS "./x.js" pointing at x.ts
        stem = re.sub(r"\.[cm]?jsx?$", "", target)
        for e in JS_EXTENSIONS:
            if stem + e in file_set:
                return stem + e
        return None

    def resolve_py(module: str, level: int) -> str | None:
        parts = module.split(".") if module else []
        roots = [base] if level else ["", "src", "lib", "app"]
        if level:
            anchor = base
            for _ in range(level - 1):
                anchor = os.path.dirname(anchor)
            roots = [anchor]
        for root in roots:
            joined = "/".join(p for p in [root, *parts] if p)
            for cand in (joined + ".py", joined + "/__init__.py"):
                if cand in file_set:
                    return cand
        return None

    if JS_SOURCE.search(path):
        for m in re.finditer(r"""(?:from\s+|import\s*\(\s*|require\s*\(\s*|import\s+)["'](\.{1,2}/[^"']+)["']""", text):
            add(resolve_js(m.group(1)))
        for m in re.finditer(r"""from\s+["'](?:~|@)/([^"']+)["']""", text):
            spec = m.group(1)
            for root in ("src/", ""):
                for cand in [root + spec] + [root + spec + e for e in JS_EXTENSIONS] + [f"{root}{spec}/index{e}" for e in JS_EXTENSIONS]:
                    if cand in file_set:
                        add(cand)
                        break
    elif path.endswith(".py"):
        for m in re.finditer(r"^\s*from\s+(\.*)([\w.]*)\s+import\s+([\w*,\s()]+)", text, re.M):
            level, module = len(m.group(1)), m.group(2)
            hit = resolve_py(module, level)
            for name in re.findall(r"\w+", m.group(3)):
                sub = resolve_py(f"{module}.{name}" if module else name, level)
                if sub:
                    add(sub)
            add(hit)
        for m in re.finditer(r"^\s*import\s+([\w.]+)", text, re.M):
            add(resolve_py(m.group(1), 0))
    # Any repository path named literally (configs, HTTP routes to files, Go/Rust paths, etc.).
    for candidate in file_set:
        if "/" in candidate and len(candidate) > 6 and candidate in text:
            add(candidate)
    return out[:MAX_REFERENCES_PER_FILE]


def excerpt_source(text: str, budget: int) -> str:
    """Line-numbered windows around architecture-bearing code; omissions are explicit."""
    if len(text) <= budget:
        return text
    lines = text.split("\n")
    keep: set[int] = set(range(min(len(lines), 25)))  # imports and module header
    for i, line in enumerate(lines):
        if KEY_LINE.search(line):
            keep.update(range(max(0, i - 1), min(len(lines), i + 10)))
    out: list[str] = []
    used = 0
    last = -1
    for i in sorted(keep):
        piece = f"{i + 1}: {lines[i]}"
        if used + len(piece) + 1 > budget - 60:
            break
        if last != -1 and i != last + 1:
            out.append(f"... [lines {last + 2}-{i} omitted] ...")
        out.append(piece)
        used += len(piece) + 1
        last = i
    if last < len(lines) - 1:
        out.append(f"... [lines {last + 2}-{len(lines)} omitted] ...")
    return "\n".join(out)


def is_reexport_barrel(text: str) -> bool:
    lines = [l.strip() for l in text.split("\n") if l.strip() and not re.match(r"^(?://|#|\*|/\*)", l.strip())]
    if not lines:
        return True
    decl = sum(
        1 for l in lines
        if re.match(r"^(?:export\s+(?:\*|\{[^}]*\}|type\s+\{[^}]*\})\s+from\s|export\s+\*\s+as\s|(?:pub(?:\([^)]*\))?\s+)?(?:mod|use)\s|from\s+\.\S*\s+import\s)", l)
    )
    return decl / len(lines) >= 0.6


def fmt_size(size: int | None) -> str:
    if size is None:
        return ""
    return f"{max(0.1, round(size / 100) / 10)} KB, " if size < 10_000 else f"{round(size / 1000)} KB, "


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    repo = Path(sys.argv[1]).resolve()
    out_dir = Path(sys.argv[2]).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    files = list_files(repo)
    if not files:
        sys.exit("No files found.")
    file_set = set(files)
    dirs = {"/".join(f.split("/")[:i]) for f in files for i in range(1, len(f.split("/")))}
    sizes = {f: (repo / f).stat().st_size for f in files}

    ranked = rank_source_paths(files, sizes, MAX_SOURCE_FILES + MAX_REFERENCE_FILES + MAX_LISTED_FILES)
    selected = ranked[:MAX_SOURCE_FILES]
    reference_paths = ranked[MAX_SOURCE_FILES:MAX_SOURCE_FILES + MAX_REFERENCE_FILES]
    listed = ranked[MAX_SOURCE_FILES + MAX_REFERENCE_FILES:]

    # File tree: ranked files first, then directories, runtime sources, everything else; capped.
    runtime = [f for f in files if is_architecture_source(f)]
    context_paths = runtime if len(runtime) > 40 else files
    ordered = list(dict.fromkeys(
        ranked + sorted(d for d in dirs if not EXCLUDED.search(d)) + runtime + context_paths
    ))
    tree, chars = [], 0
    for p in ordered:
        if chars + len(p) + 1 > MAX_TREE_CHARACTERS:
            continue
        tree.append(p)
        chars += len(p) + 1
    tree_truncated = len(tree) < len(set(files) | dirs)

    readme_path = next((f for f in files if "/" not in f and README.match(f)), None)
    readme = (repo / readme_path).read_text(errors="replace") if readme_path else "No README found."
    if len(readme) > MAX_README_CHARACTERS:
        readme = readme[:MAX_README_CHARACTERS] + "\n[README excerpt ends here.]"

    texts: dict[str, str] = {}
    for p in selected + reference_paths:
        t = read_text(repo, p)
        if t is not None:
            texts[p] = t
    read_order = [p for p in selected + reference_paths if p in texts]
    excerpt_set = {p for p in selected if p in texts}
    references = {p: resolve_references(p, texts[p], file_set, dirs) for p in read_order}

    used_by: dict[str, int] = {}
    for p in read_order:
        for r in references[p]:
            used_by[r] = used_by.get(r, 0) + 1

    def core_score(p: str) -> float:
        return 3 * min(4, used_by.get(p, 0)) + min(6, math.log2(1 + sizes.get(p, 0) / 1000))

    core_candidates = list(dict.fromkeys(read_order + [r for r in used_by if r not in texts and is_architecture_source(r)]))
    scored = sorted(
        ((p, core_score(p)) for p in core_candidates
         if not MANIFEST.search(p) and not SUPPORTING_FILE.search(p)
         and not (p in texts and is_reexport_barrel(texts[p]))),
        key=lambda e: (-e[1], e[0]),
    )
    core = sorted([p for p, s in scored if s >= 3][:MAX_CORE_MODULES])

    # SOURCE INDEX
    header = "\n".join([
        "SOURCE INDEX (computed from the repository files, not written by a model)",
        'Each line: path (size, how it was used) -> repository files it imports or names anywhere in its full text, '
        'including parts not excerpted below. "excerpt": excerpted below. "read": read in full, not excerpted. '
        '"not read": never opened, so its contents and connections are unknown. Lists miss dynamic, HTTP, event and configuration wiring.',
        *( [f"CORE MODULES (the largest and most used files; give each a node, alone or as part of its subsystem's node): {', '.join(core)}"] if core else [] ),
    ])
    entries: list[tuple[str, str]] = []
    budget = MAX_SOURCE_INDEX_CHARACTERS - len(header) - 20
    unread = [p for p in dict.fromkeys(ranked + [r for p in read_order for r in references[p]] + listed)
              if p not in texts and p in file_set and is_architecture_source(p)]
    for p in read_order + unread:
        status = "excerpt" if p in excerpt_set else ("read" if p in texts else "not read")
        line = f"{p} ({fmt_size(sizes.get(p))}{status})"
        if p in references:
            shown = references[p][:MAX_REFERENCES_SHOWN]
            more = len(references[p]) - len(shown)
            line += f" -> {', '.join(shown)}{f', +{more} more' if more > 0 else ''}" if shown else " -> none found"
        if len(line) + 1 > budget:
            continue
        entries.append((p, line))
        budget -= len(line) + 1
    index = "\n".join([header, *(l for _, l in sorted(entries)), "END SOURCE INDEX"])

    # Fair excerpt budget across selected files.
    available = [p for p in selected if p in texts]
    limits = {p: 0 for p in available}
    remaining = MAX_SOURCE_CHARACTERS - 4000 - len(index)
    pending = list(available)
    while pending and remaining > 0:
        share = remaining // len(pending)
        if not share:
            break
        nxt = []
        for p in pending:
            cap = min(len(texts[p]), MAX_EXCERPT_PER_FILE)
            alloc = min(share, cap - limits[p])
            limits[p] += alloc
            remaining -= alloc
            if limits[p] < cap:
                nxt.append(p)
        pending = nxt
    excerpts = []
    for p in available:
        truncated = len(texts[p]) > limits[p]
        excerpts.append(f"FILE {json.dumps(p)}{' (partial excerpt)' if truncated else ''}\n{excerpt_source(texts[p], limits[p])}\nEND FILE")
    body = "\n\n".join(excerpts) if excerpts else "No source excerpts available. Use documented relationships only."
    source_files = f"{index}\n\n{body}"[:MAX_SOURCE_CHARACTERS]

    context = "\n".join([
        f"<file_tree>\n{chr(10).join(sorted(tree))}\n</file_tree>",
        f"<readme>\n{readme}\n</readme>",
        f"<source_files>\n{source_files}\n</source_files>",
    ])
    (out_dir / "context.md").write_text(context)
    (out_dir / "repo.json").write_text(json.dumps({
        "root": str(repo),
        "paths": sorted(file_set | dirs),
        "dirs": sorted(dirs),
        "readPaths": read_order,
        "references": references,
    }, indent=1))
    print(json.dumps({
        "context": str(out_dir / "context.md"),
        "characters": len(context),
        "files": len(files),
        "excerpted": available,
        "readForReferences": len(read_order) - len(available),
        "coreModules": core,
        "treeTruncated": tree_truncated,
    }, indent=2))


if __name__ == "__main__":
    main()
