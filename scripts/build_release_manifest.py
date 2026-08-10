from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from command_center.release_manifest import build_release_manifest


def _load_specs(path: Path) -> list[dict[str, object]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        specs = payload
    elif isinstance(payload, dict):
        specs = payload.get("artifacts")
    else:
        specs = None
    if not isinstance(specs, list):
        raise ValueError("spec file must contain an array or object with 'artifacts' array")
    if not all(isinstance(entry, dict) for entry in specs):
        raise ValueError("each artifact spec must be a JSON object")
    return specs


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build reproducible release manifest with artifact hashes and signing slots."
    )
    parser.add_argument("--source-sha", required=True, help="Exact git SHA this release candidate binds to.")
    parser.add_argument("--spec-file", type=Path, required=True, help="JSON file with artifact descriptors.")
    parser.add_argument("--output", type=Path, required=True, help="Output JSON manifest path.")
    args = parser.parse_args()

    specs = _load_specs(args.spec_file)
    manifest = build_release_manifest(args.source_sha, specs, repository_root=REPO_ROOT)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
