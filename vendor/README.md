# vendor/ — vendored closed-core artifacts

## aios_sdk-0.1.0+gd3c69e4-py3-none-any.whl

Standalone build of the AIOS Python SDK (`aios_sdk` package only — the client
imports nothing from `aios.*` core), vendored so that `requirements.txt` can
install it everywhere the suite runs. The `aios` repository is private and CI
references no secrets (a documented design constraint of `ci.yml`), so the SDK
cannot be fetched from the repo at install time; a committed wheel is the
secret-free alternative. Closing CHANGELOG Sprint 4 limitation I5: with the
SDK installed, `tests/test_aios_tasks_repository.py` and
`tests/test_aios_tasks_adapter.py` run in CI instead of skipping.

**Provenance**

- Source repository: `git@github.com:dimastov-lab/aios.git`
- Ref: `origin/main` @ `d3c69e4f1ec1830791b7b8775ed3c5ab87707516`
  (content taken via `git archive` — committed state only, never a working tree)
- Built by: `scripts/build_aios_sdk_wheel.py` (deterministic: fixed zip
  timestamps, sorted entries; rebuilding from the same ref is byte-identical)
- sha256: `caae6022459a29203e6a75baeec57d87743134054486b6b5bf4f91f834507bbe`
- Wheel version: SDK's own `_version.py` (`0.1.0`, `SUPPORTED_API_MAJOR = 1`)
  plus a PEP 440 local segment `+g<short-sha>` pinning the aios commit.
- Runtime dependencies (bands sourced from the aios `pyproject.toml` at the
  same ref): `httpx>=0.27.0`, `pydantic>=2.7.0`. Requires Python `>=3.12`.

**Refreshing the wheel** (after the SDK changes in the aios repo):

```bash
python scripts/build_aios_sdk_wheel.py --aios-repo ../aios --ref origin/main
```

Then update the filename reference in `requirements.txt` and the provenance
block above (new sha + new local segment), and delete the old wheel.

**Local development note**: if you also install the full aios package
editable (`pip install -e ../aios`), both distributions provide the
`aios_sdk` import in one environment, and which copy wins depends on
`sys.path` order. If the import resolves oddly, `pip uninstall aios-sdk aios`
and reinstall the one you want.
The `uv` dev lane (`[dependency-groups] dev`) keeps using the `../aios` path
dependency and does not consume this wheel.
