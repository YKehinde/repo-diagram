#!/usr/bin/env python3
"""Validate an architecture graph and compile it to Mermaid, Markdown and HTML.

Ported (simplified) from gitdiagram's src/server/generate/graph.ts, edge-evidence.ts
and src/features/diagram/mermaid-config.ts (MIT, Ahmed Khaleel).

Usage: compile_diagram.py <graph.json> <repo.json> <out_dir> [--title NAME]

<graph.json> is {"explanation": str, "graph": {...}} or the bare graph.
Exit 0: written. Exit 2: structural issues printed as repair feedback (fix and rerun).
"""
from __future__ import annotations

import html
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path
from urllib.parse import quote

MAX_GROUPS, MAX_NODES, MAX_EDGES = 10, 34, 48
ID = re.compile(r"^[a-z][a-z0-9_]*$")
SHAPES = {"box", "database", "queue", "document", "circle", "hexagon"}
README = re.compile(r"(?:^|/)readme(?:\.[a-z0-9]+)?$", re.I)
GENERIC_TYPES = {
    "app", "application", "component", "directory", "folder", "library", "module", "package",
    "project", "repo", "repository", "service", "system", "utility",
}
TONES = ["toneBlue", "toneAmber", "toneMint", "toneRose", "toneIndigo", "toneTeal"]
CLASS_DEFS = [
    "classDef toneBlue fill:#dbeafe,stroke:#2563eb,stroke-width:1.5px,color:#172554",
    "classDef toneAmber fill:#fef3c7,stroke:#d97706,stroke-width:1.5px,color:#78350f",
    "classDef toneMint fill:#dcfce7,stroke:#16a34a,stroke-width:1.5px,color:#14532d",
    "classDef toneRose fill:#ffe4e6,stroke:#e11d48,stroke-width:1.5px,color:#881337",
    "classDef toneIndigo fill:#e0e7ff,stroke:#4f46e5,stroke-width:1.5px,color:#312e81",
    "classDef toneTeal fill:#ccfbf1,stroke:#0f766e,stroke-width:1.5px,color:#134e4a",
]


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFC", value)
    value = re.sub(r"[\u0000-\u001f\u007f-\u009f  ]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def esc(value: str) -> str:
    # GitDiagram emits HTML entities, which its Mermaid 12 renderer decodes. Mermaid 11 and
    # GitHub show them literally with htmlLabels off, so swap the characters that can break a
    # quoted label (or be read as markup or a #code; entity) for safe look-alikes instead.
    out = normalize_text(value)
    for ch, safe in [('"', "'"), ("`", "'"), ("<", "‹"), (">", "›"), ("#", "＃"), ("&", "＆"), ("|", "¦"), ("\\", "/")]:
        out = out.replace(ch, safe)
    return out.strip() or "Unnamed"


def validate(graph: dict, paths: set[str]) -> tuple[list[str], dict]:
    """Return (structural issues, graph with unknown paths stripped)."""
    issues: list[str] = []
    groups, nodes, edges = graph.get("groups") or [], graph.get("nodes") or [], graph.get("edges") or []
    if not nodes:
        issues.append("nodes: At least one node is required.")
    for key, items, cap in (("groups", groups, MAX_GROUPS), ("nodes", nodes, MAX_NODES), ("edges", edges, MAX_EDGES)):
        if len(items) > cap:
            issues.append(f"{key}: {len(items)} exceeds the limit of {cap}.")

    def canonical(p: str | None) -> str | None:
        if not p or not str(p).strip():
            return None
        p = str(p).strip()
        if p in paths:
            return p
        fixed = re.sub(r"/+$", "", re.sub(r"^\./", "", p))
        return fixed if fixed in paths else None  # unknown paths are cosmetic: strip, never fatal

    group_ids: set[str] = set()
    for i, g in enumerate(groups):
        if not ID.match(str(g.get("id", ""))):
            issues.append(f"groups.{i}.id: Must match ^[a-z][a-z0-9_]*$.")
        if g.get("id") in group_ids:
            issues.append(f'groups.{i}.id: Duplicate group id "{g.get("id")}".')
        if not normalize_text(str(g.get("label") or "")):
            issues.append(f"groups.{i}.label: Must contain visible text.")
        group_ids.add(g.get("id"))
    node_ids: set[str] = set()
    for i, n in enumerate(nodes):
        if not ID.match(str(n.get("id", ""))):
            issues.append(f"nodes.{i}.id: Must match ^[a-z][a-z0-9_]*$.")
        if n.get("id") in node_ids:
            issues.append(f'nodes.{i}.id: Duplicate node id "{n.get("id")}".')
        node_ids.add(n.get("id"))
        if not normalize_text(str(n.get("label") or "")):
            issues.append(f"nodes.{i}.label: Must contain visible text.")
        if n.get("groupId") and n["groupId"] not in group_ids:
            issues.append(f'nodes.{i}.groupId: Unknown group id "{n["groupId"]}" for node "{n.get("id")}".')
        if n.get("shape") and n["shape"] not in SHAPES:
            issues.append(f"nodes.{i}.shape: Must be one of {sorted(SHAPES)} or null.")
    for i, e in enumerate(edges):
        if e.get("from") not in node_ids:
            issues.append(f'edges.{i}.from: Unknown source node id "{e.get("from")}".')
        if e.get("to") not in node_ids:
            issues.append(f'edges.{i}.to: Unknown target node id "{e.get("to")}".')
        if e.get("style") not in (None, "solid", "dashed"):
            issues.append(f"edges.{i}.style: Must be solid, dashed or null.")

    stripped = {
        "groups": groups,
        "nodes": [{**n, "path": canonical(n.get("path"))} for n in nodes],
        "edges": [{**e, "evidencePath": canonical(e.get("evidencePath"))} for e in edges],
    }
    return issues, stripped


def covers(node_path: str, path: str) -> bool:
    return path == node_path or path.startswith(node_path.rstrip("/") + "/")


def apply_edge_evidence(graph: dict, repo: dict) -> tuple[dict, int, int]:
    """Drop citations to files that were never read or don't show the edge; fill provable ones."""
    read_paths: list[str] = repo["readPaths"]
    seen = set(read_paths)
    refs: dict[str, list[str]] = repo["references"]
    nodes = {n["id"]: n for n in graph["nodes"]}

    def relevant(file: str, a: dict | None, b: dict | None) -> bool:
        if README.search(file):
            return True
        if file not in seen:
            return False
        ends = [x.get("path") for x in (a, b) if x]
        if any(p and covers(p, file) for p in ends):
            return True
        return len(ends) == 2 and all(p and any(covers(p, r) for r in refs.get(file, [])) for p in ends)

    def support(a: dict | None, b: dict | None) -> str | None:
        if not a or not b or not a.get("path") or not b.get("path"):
            return None
        for x, y in ((a["path"], b["path"]), (b["path"], a["path"])):
            for file in read_paths:
                if not covers(x, file):
                    continue
                if x == y:
                    if file == x:
                        return file
                    continue
                if any(covers(y, r) for r in refs.get(file, [])):
                    return file
        return None

    stripped = filled = 0
    edges = []
    for e in graph["edges"]:
        a, b = nodes.get(e["from"]), nodes.get(e["to"])
        ev = e.get("evidencePath")
        if ev and not relevant(ev, a, b):
            stripped += 1
            ev = None
        if not ev:
            ev = support(a, b)
            filled += 1 if ev else 0
        edges.append({**e, "evidencePath": ev})
    return {**graph, "edges": edges}, stripped, filled


def tone_for(node: dict, group_order: dict[str, int]) -> str:
    if node.get("groupId") in group_order:
        return TONES[group_order[node["groupId"]] % len(TONES)]
    words = f'{node.get("label", "")} {node.get("type", "")}'.lower()
    if node.get("shape") == "database" or re.search(r"database|storage|cache|postgres|sqlite|redis|clickhouse", words):
        return "toneAmber"
    if re.search(r"queue|worker|background|scheduler|task", words):
        return "toneRose"
    if re.search(r"client|browser|user|frontend|view|screen|ui\b", words):
        return "toneBlue"
    if re.search(r"api|server|route|request|handler|webhook", words):
        return "toneMint"
    if not node.get("path") or re.search(r"model|inference|provider|integration", words):
        return "toneIndigo"
    return "toneTeal"


def node_label(node: dict) -> str:
    label = esc(node["label"])
    detail = None
    t = (node.get("type") or "").strip()
    if t:
        tl, ll = t.lower(), node["label"].strip().lower()
        if not (tl in GENERIC_TYPES or tl == ll or tl in ll or ll in tl or len(t.split()) > 4):
            detail = esc(t)
    if not detail:
        p = (node.get("path") or "").strip()
        name = p.split("/")[-1] if p and not p.endswith("/") and "." in p else ""
        if name and len(name) <= 18:
            detail = f"[{esc(name)}]"
    return f"{label}<br/>{detail}" if detail else label


def render_node(node: dict) -> str:
    nid, label = f'node_{node["id"]}', node_label(node)
    shape = node.get("shape") or "box"
    if shape == "database":
        return f'{nid}[("{label}")]'
    if shape == "circle":
        return f'{nid}(("{label}"))'
    if shape == "hexagon":
        return f'{nid}{{{{"{label}"}}}}'
    return f'{nid}["{label}"]'


def link_builder(repo: dict):
    """GitHub links when origin is GitHub, otherwise VS Code links to the local file."""
    root = repo["root"]
    dirs = set(repo["dirs"])
    try:
        remote = subprocess.run(["git", "-C", root, "remote", "get-url", "origin"], capture_output=True, text=True).stdout.strip()
        branch = subprocess.run(["git", "-C", root, "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True).stdout.strip()
    except FileNotFoundError:
        remote = branch = ""
    m = re.search(r"github\.com[:/]([^/]+)/(.+?)(?:\.git)?$", remote)
    if m and branch and branch != "HEAD":
        owner, name = m.group(1), m.group(2)

        def github(path: str) -> str:
            kind = "tree" if path in dirs else "blob"
            return f"https://github.com/{quote(owner)}/{quote(name)}/{kind}/{quote(branch)}/{quote(path)}"
        return github
    return lambda path: "vscode://file" + quote(f"{root}/{path}")


def compile_mermaid(graph: dict, link) -> str:
    lines = ["flowchart TD"]
    group_order = {g["id"]: i for i, g in enumerate(graph["groups"])}
    classes: dict[str, list[str]] = {}
    grouped: set[str] = set()

    def push(node: dict, indent: str = "") -> None:
        lines.append(indent + render_node(node))
        classes.setdefault(tone_for(node, group_order), []).append(f'node_{node["id"]}')

    for g in graph["groups"]:
        lines += ["", f'subgraph group_{g["id"]}["{esc(g["label"])}"]']
        for n in graph["nodes"]:
            if n.get("groupId") == g["id"]:
                push(n, "  ")
                grouped.add(n["id"])
        lines.append("end")
    rest = [n for n in graph["nodes"] if n["id"] not in grouped]
    if rest:
        lines.append("")
        for n in rest:
            push(n)
    if graph["edges"]:
        lines.append("")
        for e in graph["edges"]:
            arrow = "-.->" if e.get("style") == "dashed" else "-->"
            label = f'|"{esc(e["label"])}"|' if e.get("label") else ""
            lines.append(f'node_{e["from"]} {arrow}{label} node_{e["to"]}')
    linked = [n for n in graph["nodes"] if n.get("path")]
    if linked:
        lines.append("")
        for n in linked:
            lines.append(f'click node_{n["id"]} "{link(n["path"])}"')
    lines += [""] + CLASS_DEFS
    for cls, ids in classes.items():
        lines.append(f"class {','.join(ids)} {cls}")
    return "\n".join(lines).strip()


HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ architecture</title>
<style>
  :root { color-scheme: light dark; --bg:#ffffff; --fg:#171717; --muted:#525252; --border:#e5e5e5; }
  @media (prefers-color-scheme: dark) { :root { --bg:#1f2631; --fg:#e8edf5; --muted:#a3adbd; --border:#323d4d; } }
  body { margin:0; background:var(--bg); color:var(--fg); font:16px/1.55 system-ui, sans-serif; }
  main { max-width:1200px; margin:0 auto; padding:24px 16px 64px; }
  h1 { font-size:1.4rem; margin:0 0 4px; }
  p.meta { color:var(--muted); margin:0 0 20px; font-size:.9rem; }
  #diagram { border:1px solid var(--border); border-radius:8px; padding:16px; overflow:auto; }
  #diagram svg { max-width:none; height:auto; }
  details { margin-top:24px; } summary { cursor:pointer; font-weight:600; }
  pre { white-space:pre-wrap; border:1px solid var(--border); border-radius:8px; padding:16px; font-size:.85rem; }
  .explanation { white-space:pre-wrap; }
</style>
</head>
<body>
<main>
  <h1>__TITLE__</h1>
  <p class="meta">Generated from the file tree, README and a sample of source files. Nodes link to their source. Check details against the code.</p>
  <div id="diagram" role="img" aria-label="Architecture diagram of __TITLE__"></div>
  <details open><summary>Overview</summary><div class="explanation">__EXPLANATION__</div></details>
  <details><summary>Mermaid source</summary><pre id="source">__SOURCE__</pre></details>
</main>
<script type="module">
  import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.esm.min.mjs";
  import elkLayouts from "https://cdn.jsdelivr.net/npm/@mermaid-js/layout-elk@0.1.7/dist/mermaid-layout-elk.esm.min.mjs";
  mermaid.registerLayoutLoaders(elkLayouts);
  const dark = matchMedia("(prefers-color-scheme: dark)").matches;
  mermaid.initialize({
    startOnLoad: false, securityLevel: "antiscript", theme: "base", htmlLabels: false, layout: "elk",
    flowchart: { wrappingWidth: 200, curve: "linear", nodeSpacing: 50, rankSpacing: 50, padding: 15 },
    themeVariables: dark
      ? { background: "#1f2631", primaryColor: "#2c3544", primaryBorderColor: "#6dd4e9", primaryTextColor: "#e8edf5", lineColor: "#ffd486" }
      : { background: "#ffffff", primaryColor: "#f7f7f7", primaryBorderColor: "#000000", primaryTextColor: "#171717", lineColor: "#000000" },
  });
  const source = document.getElementById("source").textContent;
  const { svg, bindFunctions } = await mermaid.render("arch", source);
  const el = document.getElementById("diagram");
  el.innerHTML = svg;
  bindFunctions?.(el);
</script>
</body>
</html>
"""


def main() -> None:
    args = sys.argv[1:]
    title = None
    if "--title" in args:
        i = args.index("--title")
        title = args[i + 1]
        args = args[:i] + args[i + 2:]
    if len(args) != 3:
        sys.exit(__doc__)
    raw = json.loads(Path(args[0]).read_text())
    repo = json.loads(Path(args[1]).read_text())
    out_dir = Path(args[2])
    out_dir.mkdir(parents=True, exist_ok=True)
    graph = raw.get("graph", raw)
    explanation = raw.get("explanation", "") if "graph" in raw else ""

    issues, graph = validate(graph, set(repo["paths"]))
    if issues:
        print("Graph validation failed. Repair these issues without unrelated redesign:", file=sys.stderr)
        print("\n".join(issues), file=sys.stderr)
        sys.exit(2)
    graph, stripped, filled = apply_edge_evidence(graph, repo)

    title = title or Path(repo["root"]).name
    mermaid = compile_mermaid(graph, link_builder(repo))
    (out_dir / "diagram.mmd").write_text(mermaid + "\n")
    (out_dir / "graph.validated.json").write_text(json.dumps({"explanation": explanation, "graph": graph}, indent=2))
    (out_dir / "diagram.md").write_text(
        f"# {title} architecture\n\n{explanation}\n\n```mermaid\n{mermaid}\n```\n"
    )
    (out_dir / "diagram.html").write_text(
        HTML.replace("__TITLE__", html.escape(title))
        .replace("__EXPLANATION__", html.escape(explanation))
        .replace("__SOURCE__", html.escape(mermaid))
    )
    unproven = sum(1 for e in graph["edges"] if not e.get("evidencePath") and e.get("style") != "dashed")
    print(json.dumps({
        "html": str(out_dir / "diagram.html"),
        "markdown": str(out_dir / "diagram.md"),
        "mermaid": str(out_dir / "diagram.mmd"),
        "nodes": len(graph["nodes"]),
        "edges": len(graph["edges"]),
        "nodesWithoutPath": sum(1 for n in graph["nodes"] if not n.get("path")),
        "evidenceStripped": stripped,
        "evidenceFilled": filled,
        "solidEdgesWithoutEvidence": unproven,
    }, indent=2))


if __name__ == "__main__":
    main()
