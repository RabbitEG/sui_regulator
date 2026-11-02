#!/usr/bin/env python3
"""Export public schemas, or check that committed schemas match the implementation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sui_regulator.gateway import request_schema  # noqa: E402
from sui_regulator.mcp import WORKER_TOOLS  # noqa: E402
from sui_regulator.service import COMMAND_KINDS  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    yan = request_schema()
    yan["$id"] = "sui-regulator/yan-request/0.1"
    command = {
        **yan,
        "$id": "sui-regulator/command/0.1",
        "oneOf": [
            variant
            for variant in yan["oneOf"]
            if variant["properties"]["method"]["const"][4:] in COMMAND_KINDS
        ],
    }
    sui = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "sui-regulator/sui-tools/0.1",
        "$defs": {
            name: {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            }
            for name, (properties, required) in WORKER_TOOLS.items()
        },
    }
    failures = []
    for name, schema in [
        ("yan-request.schema.json", yan),
        ("command.schema.json", command),
        ("sui-tools.schema.json", sui),
    ]:
        path = ROOT / "schemas" / name
        output = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.exists() or json.loads(path.read_text()) != schema:
                failures.append(name)
        else:
            path.write_text(output)
    if failures:
        raise SystemExit("stale schemas: " + ", ".join(failures))
    print("Schemas are current." if args.check else "Schemas exported.")


if __name__ == "__main__":
    main()
