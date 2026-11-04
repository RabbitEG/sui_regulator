# Third-party notices

`src/sui_regulator/failures/repetition.py` adapts the word-run/frequency, bounded periodicity, sentence replay, n-gram coverage and trigram similarity approaches in [Antiloop detect.ts](https://github.com/noguerol/antiloop/blob/4c163bf5c350962a98f5bc99111f627a5b2b32b0/src/detect.ts).

Copyright (c) 2026 antiloop contributors. MIT License, retained in [docs/licenses/antiloop-MIT.txt](docs/licenses/antiloop-MIT.txt). Modifications include bounded incremental windows, Unicode single-character coverage, evidence hashes and independent runtime/incident interfaces. Pi integration and prompt injection are not included.

AgentRx and Codex Task Watchdog informed the design; their code or datasets are not bundled. Public failure reports inspire synthetic fixtures whose provenance is recorded in each case. These fixtures do not reproduce customer transcripts or claim to reproduce a model's actual failure probability.

## Arknights artwork

The three PNG images in `docs/assets/arknights/` are unmodified Arknights game artwork downloaded from PRTS. Arknights and its artwork remain the property of HYPERGRYPH and their respective rights holders; these images are **not covered by this repository's MIT License**. Individual sources and file checksums are recorded in [the asset notes](docs/assets/arknights/README.md). This project is independent of the game's creators and has no official endorsement.

Yan Console also bundles an identical, unmodified copy of `zuo-le-module-y.png` as `src/sui_regulator/static/zuo-le-records.png`. The same attribution and MIT exclusion apply to that copy, including in installed distributions. Its source is [Zuo Le's module artwork on PRTS](https://prts.wiki/w/左乐#模组); SHA-256: `f4bb6bf50eb73cd36e56f0e2e5a3da66fb21be0a0dc75c4e35c339b0233d4485`.

## Local Markdown renderer and fonts

Yan Console ships these unmodified browser assets; users do not need npm or CDN access. Fixed upstream commits/releases, download integrity, font metadata, sizes and SHA-256 checksums are recorded in [the reading asset manifest](docs/console-reading-assets.json). Copyright notices and full licenses are retained in `docs/licenses/` and included in installed distributions.

| Component | Version | License and retained text |
|---|---|---|
| [markdown-it](https://github.com/markdown-it/markdown-it) | 15.0.3 | [MIT](docs/licenses/markdown-it-MIT.txt) |
| [DOMPurify](https://github.com/cure53/DOMPurify) | 3.4.17 | [Apache-2.0](docs/licenses/DOMPurify-Apache-2.0.txt) or [MPL-2.0](docs/licenses/DOMPurify-MPL-2.0.txt) |
| [Inter Variable](https://github.com/rsms/inter) | 4.001 | [SIL OFL 1.1](docs/licenses/Inter-OFL-1.1.txt) |
| [Source Han Sans CN VF](https://github.com/adobe-fonts/source-han-sans) | 2.005 | [SIL OFL 1.1](docs/licenses/Source-Han-Sans-OFL-1.1.txt) |
| [JetBrains Mono](https://github.com/JetBrains/JetBrainsMono) | 2.305 | [SIL OFL 1.1](docs/licenses/JetBrains-Mono-OFL-1.1.txt) |

The markdown-it UMD bundle also includes `entities` ([BSD-2-Clause](docs/licenses/markdown-it-dependency-entities-BSD-2-Clause.txt)), `linkify-it` ([MIT](docs/licenses/markdown-it-dependency-linkify-it-MIT.txt)), `mdurl` ([MIT](docs/licenses/markdown-it-dependency-mdurl-MIT.txt)), `punycode.js` ([MIT](docs/licenses/markdown-it-dependency-punycode.js-MIT.txt)) and `uc.micro` ([MIT](docs/licenses/markdown-it-dependency-uc.micro-MIT.txt)). Exact versions and provenance are in the manifest. Linkification is disabled in Yan Console; bundled upstream code remains unchanged.

Font bytes and internal family names are unchanged. Source Han Sans uses the complete upstream Simplified Chinese regional WOFF2, not a subset of characters appearing in the interface. Fonts are not relicensed under this project's MIT License. The browser loads them from the local Console, with system sans-serif fallback while loading.
