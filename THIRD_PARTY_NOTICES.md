# Third-party notices

`src/sui_regulator/failures/repetition.py` adapts the word-run/frequency, bounded periodicity, sentence replay, n-gram coverage and trigram similarity approaches in [Antiloop detect.ts](https://github.com/noguerol/antiloop/blob/4c163bf5c350962a98f5bc99111f627a5b2b32b0/src/detect.ts).

Copyright (c) 2026 antiloop contributors. MIT License, retained in [docs/licenses/antiloop-MIT.txt](docs/licenses/antiloop-MIT.txt). Modifications include bounded incremental windows, Unicode single-character coverage, evidence hashes and independent runtime/incident interfaces. Pi integration and prompt injection are not included.

AgentRx and Codex Task Watchdog informed the design; their code or datasets are not bundled. Public failure reports inspire synthetic fixtures whose provenance is recorded in each case. These fixtures do not reproduce customer transcripts or claim to reproduce a model's actual failure probability.
