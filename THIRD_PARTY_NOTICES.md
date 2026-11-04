# Third-party notices

`src/sui_regulator/failures/repetition.py` adapts the word-run/frequency, bounded periodicity, sentence replay, n-gram coverage and trigram similarity approaches in [Antiloop detect.ts](https://github.com/noguerol/antiloop/blob/4c163bf5c350962a98f5bc99111f627a5b2b32b0/src/detect.ts).

Copyright (c) 2026 antiloop contributors. MIT License, retained in [docs/licenses/antiloop-MIT.txt](docs/licenses/antiloop-MIT.txt). Modifications include bounded incremental windows, Unicode single-character coverage, evidence hashes and independent runtime/incident interfaces. Pi integration and prompt injection are not included.

AgentRx and Codex Task Watchdog informed the design; their code or datasets are not bundled. Public failure reports inspire synthetic fixtures whose provenance is recorded in each case. These fixtures do not reproduce customer transcripts or claim to reproduce a model's actual failure probability.

## Arknights artwork

The three PNG images in `docs/assets/arknights/` are unmodified Arknights game artwork downloaded from PRTS. Arknights and its artwork remain the property of HYPERGRYPH and their respective rights holders; these images are **not covered by this repository's MIT License**. Individual sources and file checksums are recorded in [the asset notes](docs/assets/arknights/README.md). This project is independent of the game's creators and has no official endorsement.

Yan Console also bundles an identical, unmodified copy of `zuo-le-module-y.png` as `src/sui_regulator/static/zuo-le-records.png`. The same attribution and MIT exclusion apply to that copy, including in installed distributions. Its source is [Zuo Le's module artwork on PRTS](https://prts.wiki/w/左乐#模组); SHA-256: `f4bb6bf50eb73cd36e56f0e2e5a3da66fb21be0a0dc75c4e35c339b0233d4485`.
