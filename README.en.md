# SpaceSift

[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-46%20passed-brightgreen.svg)](tests/)

**Look first. Delete later.**

A zero-dependency disk usage analyzer and safe cleaner. It tells you where the space went,
sorts what it finds by *how safe it is to delete*, and only removes what sits on a hard-coded
whitelist. Everything is a CLI, so the result is reproducible and diffable.

## Why not a GUI treemap?

SpaceSniffer and WizTree are great at showing you the blocks. They are bad at leaving a
conclusion behind: you get a screenshot, not a report you can commit to git or attach to a
ticket. SpaceSift produces that report — Markdown or a single-file HTML — from a scriptable
scan, and it separates "cache you can throw away" from "170 GB of simulation results that
belong on another drive."

## Features

- **Fast**: `os.scandir` + post-order aggregation. 3.2M files / 624 GB in ~44 s on NVMe.
- **Four buckets**, by actionability rather than size:
  `safe` (auto-rebuilt caches), `caution` (side effects), `manual` (system switches and
  user documents — never touched), `archive` (large files that belong on another disk).
- **Dry-run by default.** `spacesift clean` only counts; `--yes` is required to delete.
- **Whitelist-only deletion**, protected-path deny list, no following of junctions/symlinks.
- **No dependencies** — standard library only, Python 3.9+.

## Install

```bash
git clone https://github.com/Twofruitsgrape/spacesift.git
cd spacesift
pip install -e .
spacesift
```

## Usage

```bash
spacesift                       # scan + detect + summary (touches nothing)
spacesift scan C:/ --depth 3 --json scan.json
spacesift detect --only npm-cache,user-temp
spacesift clean                 # dry-run
spacesift clean --yes           # delete safe items
spacesift report --out report.md --html
```

## Safety model

1. Rules live in code (`src/spacesift/rules.py`), never in config.
2. Before deleting, the target must (a) resolve inside that rule's declared paths and
   (b) not be a protected path (drive roots, `C:\Windows`, `System32` subtree,
   `Program Files`, `ProgramData`).
3. Reparse points are skipped, never traversed.
4. `manual` rules can never be included in a batch clean.

46 tests cover the failure modes, including "caller hands us a path outside the rule".

## License

MIT © 2026 Twofruitsgrape
