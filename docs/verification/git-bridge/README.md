# Git Bridge verification — 2026-11-05

- `PYTHONPATH=src .venv/bin/python -m unittest tests.test_git_bridge -v`: **16 passed**, 13.329 seconds. [Output](integration-tests.txt).
- `SUI_TEST_REAL_CLAUDE=1 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v`: **195 passed**, 69.228 seconds, including existing real Claude CLI tests against a synthetic loopback endpoint. No real GLM calls.
- Ruff checks for all new Python modules and tests: passed. `git diff --check`: passed.
- Git Bridge integration uses actual local bare repositories, Unix RPC, SQLite and synthetic subprocess jobs. It verifies complete briefs, dependent commands, existing-task binding, Hold/Resume, loss of accepted RPC replies, restart deduplication, publish failure, push races, history rewrites, command mutation/deletion, ledger identity changes, stale Controller observations, report limits/redaction, symlink/FIFO rejection, mailbox initialization and exactly one Mock Job execution through completion.

This is a transport and control-plane regression, not a week-long availability test or GLM experiment-quality evaluation. A real remote smoke record is maintained in the private instance repository; command receipts and dated observations remain on that instance's status branch.
