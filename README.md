# repo-diagram

A Claude Code skill that generates an interactive architecture diagram of a repository locally, in the style of [GitDiagram](https://gitdiagram.com). No hosted service, API keys or GitHub API calls: Claude Code acts as the model and two stdlib-only Python scripts handle the rest.

## How it works

1. `scripts/collect_context.py` reads a local checkout, ranks source files, excerpts the top 12 and builds a source index of what each file imports. Output is bounded (about 80k characters).
2. Claude reads that context with the prompt in `references/prompt.md` and writes a graph as JSON: groups, nodes and edges, each with a file path and evidence.
3. `scripts/compile_diagram.py` validates the graph, checks edge evidence against the files that were read, and compiles it to Mermaid. It writes `diagram.html` (ELK layout, clickable nodes), `diagram.md` and `diagram.mmd`. Structural problems exit with code 2 and a list of issues to fix.

Nodes link to GitHub when the repo's `origin` is on GitHub, otherwise to the local file in VS Code.

## Install

```bash
git clone git@github.com:ykehinde/repo-diagram.git ~/.claude/skills/repo-diagram
```

Then ask Claude Code to "diagram this repo", or give it a GitHub URL.

Requires Python 3.9+ and git. Viewing the HTML needs a network connection, as Mermaid loads from jsdelivr.

## Differences from GitDiagram

- Label characters that Mermaid 11 and GitHub show as raw entities are swapped for look-alikes rather than HTML-escaped.
- `experiments/` and `playground/` folders are excluded from source ranking.
- Import resolution covers JS/TS and Python, plus any repository path named literally in a file.

## Credit

The prompt, ranking heuristics, graph schema and compiler are adapted from [ahmedkhaleel2004/gitdiagram](https://github.com/ahmedkhaleel2004/gitdiagram) (MIT).
