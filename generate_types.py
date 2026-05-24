"""
Generates two artifacts from Pydantic models for frontend consumption:

  schema.json  — canonical JSON Schema (draft 2020-12) with every
                 request/response type and shared $defs. Language-agnostic.
  types.ts     — TypeScript interfaces derived from schema.json via
                 `npx json-schema-to-typescript`. Requires Node/npx.

Run:
    uv run python generate_types.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Type

from pydantic import BaseModel, TypeAdapter

from posprinter.models import (
    CheckStatusRequest,
    CheckStatusResponse,
    ErrorResponse,
    GetPrintersRequest,
    GetPrintersResponse,
    PrintCalibrationTextRequest,
    PrinterInfo,
    PrinterProfile,
    PrinterStatusData,
    PrintJobRequest,
    RequestModel,
    SuccessResponse,
)

ROOT = Path(__file__).parent
SCHEMA_PATH = ROOT / "schema.json"
TYPES_PATH = ROOT / "types.ts"

# Top-level types we want as named exports in TS.
# (Tasks, connections etc. are auto-included via $defs.)
EXPORTED_MODELS: list[Type[BaseModel]] = [
    PrintJobRequest,
    GetPrintersRequest,
    CheckStatusRequest,
    PrintCalibrationTextRequest,
    SuccessResponse,
    GetPrintersResponse,
    CheckStatusResponse,
    ErrorResponse,
    PrinterProfile,
    PrinterInfo,
    PrinterStatusData,
]


PRIMITIVE_TYPES = {"string", "integer", "number", "boolean", "array", "null"}

# Named unions that Pydantic dumps inline at every usage. We collapse the inline
# `oneOf` blocks into a single $ref so json-schema-to-typescript stops emitting
# Connection1, Connection2... duplicates.
CONNECTION_MEMBERS = [
    "Win32Connection",
    "NetworkConnection",
    "SerialConnection",
    "DummyConnection",
]
PRINT_TASK_MEMBERS = [
    "TextTask",
    "ImageTask",
    "PdfTask",
    "TableTask",
    "FeedTask",
    "CutTask",
    "RawTask",
]


def _ref_members(one_of: list) -> list[str]:
    out = []
    for item in one_of:
        ref = item.get("$ref", "") if isinstance(item, dict) else ""
        out.append(ref.rsplit("/", 1)[-1])
    return out


def _strip_noise(obj: Any) -> Any:
    """Walk the schema tree and remove noise that confuses json-schema-to-typescript:
    - `title` on primitive properties (avoids `export type Foo = string` pollution)
    - Pydantic's `discriminator` block (the tool already handles oneOf unions)
    - Inline oneOf for Connection / PrintTask -> replace with single $ref
    """
    if isinstance(obj, dict):
        if "oneOf" in obj and isinstance(obj["oneOf"], list):
            members = _ref_members(obj["oneOf"])
            if members == CONNECTION_MEMBERS:
                return {"$ref": "#/definitions/Connection"}
            if members == PRINT_TASK_MEMBERS:
                return {"$ref": "#/definitions/PrintTask"}

        t = obj.get("type")
        if isinstance(t, str) and t in PRIMITIVE_TYPES:
            obj.pop("title", None)
        elif "anyOf" in obj or "enum" in obj:
            obj.pop("title", None)

        obj.pop("discriminator", None)
        return {k: _strip_noise(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_strip_noise(x) for x in obj]
    return obj


def build_schema() -> Dict[str, Any]:
    """Build one JSON Schema document containing all exported models + the
    discriminated Request union, sharing a single $defs namespace."""
    definitions: Dict[str, Any] = {}

    def absorb(name: str, schema: Dict[str, Any]) -> None:
        # Pydantic always emits $defs regardless of ref_template — pull them in.
        for key in ("$defs", "definitions"):
            if key in schema:
                definitions.update(schema.pop(key))
        definitions[name] = schema

    # Top-level discriminated union as a named definition
    req_schema = TypeAdapter(RequestModel).json_schema(
        ref_template="#/definitions/{model}"
    )
    absorb("Request", req_schema)

    for model in EXPORTED_MODELS:
        schema = TypeAdapter(model).json_schema(ref_template="#/definitions/{model}")
        absorb(model.__name__, schema)

    # Collapse inline Connection/PrintTask unions to single $refs FIRST,
    # then add the named definitions (so collapse doesn't rewrite them
    # into self-referencing $refs).
    definitions = _strip_noise(definitions)
    definitions["Connection"] = {
        "oneOf": [{"$ref": f"#/definitions/{m}"} for m in CONNECTION_MEMBERS],
    }
    definitions["PrintTask"] = {
        "oneOf": [{"$ref": f"#/definitions/{m}"} for m in PRINT_TASK_MEMBERS],
    }

    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "definitions": definitions,
    }


def write_schema() -> None:
    schema = build_schema()
    SCHEMA_PATH.write_text(
        json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"✓ {SCHEMA_PATH.name}  ({len(schema['definitions'])} definitions)")


_EMPTY_SCHEMA_INTERFACE = """export interface Schema {
  [k: string]: unknown;
}
"""


def _post_process_ts() -> None:
    """Strip the noise json-schema-to-typescript injects:
    - The empty `Schema` wrapper interface
    - Per-definition "This interface was referenced by …" comment blocks
    """
    import re

    text = TYPES_PATH.read_text(encoding="utf-8")
    text = text.replace(_EMPTY_SCHEMA_INTERFACE, "")
    text = re.sub(
        r"/\*\*\n \* This interface was referenced by `Schema`'s JSON-Schema\n"
        r" \* via the `definition` \"[A-Za-z0-9_]+\"\.\n \*/\n",
        "",
        text,
    )
    # Collapse runs of blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    TYPES_PATH.write_text(text, encoding="utf-8")


def write_types() -> None:
    """Invoke `npx json-schema-to-typescript` to derive types.ts from schema.json."""
    try:
        result = subprocess.run(
            [
                "npx",
                "--yes",
                "json-schema-to-typescript@latest",
                str(SCHEMA_PATH),
                "--output",
                str(TYPES_PATH),
                "--additionalProperties",
                "false",
                "--unreachableDefinitions",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        if result.stderr.strip():
            print(result.stderr, file=sys.stderr)
        _post_process_ts()
        line_count = sum(1 for _ in TYPES_PATH.open())
        print(f"✓ {TYPES_PATH.name}  ({line_count} lines)")
    except FileNotFoundError:
        sys.exit(
            "✗ `npx` not found. Install Node.js or run "
            "`npx json-schema-to-typescript schema.json -o types.ts` manually."
        )
    except subprocess.CalledProcessError as e:
        print(e.stdout, file=sys.stdout)
        print(e.stderr, file=sys.stderr)
        sys.exit(f"✗ json-schema-to-typescript failed (exit {e.returncode})")


if __name__ == "__main__":
    print("Generating schema and TypeScript types…")
    write_schema()
    write_types()
