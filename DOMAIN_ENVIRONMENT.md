# Reproduce the data-engineering environment

Source: https://github.com/stac-utils/pystac/commit/9ea0cd1188de0652434d602f3050ce06730227af

This repository republishes the tracked files of that upstream commit under their original licenses.
Upstream CI workflows are omitted from the evaluation snapshot. This commit adds environment instructions
and the dependency lock only. The source license is Apache-2.0; original license and attribution files remain.

Python 3.13.15 and uv were used on Linux. From a fresh checkout:

```sh
uv venv --python 3.13.15 .venv
export PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 DISABLE_SQLALCHEMY_CEXT=1
mkdir -p .venv/tmp
export TMPDIR="$PWD/.venv/tmp"
uv pip install --python .venv/bin/python -r requirements-domain.lock
uv pip install --python .venv/bin/python --no-build-isolation --no-deps -e core
uv pip install --python .venv/bin/python --no-build-isolation --no-deps -e pytest-pystac
.venv/bin/python -B -m pytest -q -o addopts= -p no:cacheprovider -m 'not vcr' -p pytest_recording.plugin -p pytest_pystac.plugin --block-network --record-mode=none tests/test_cache.py tests/test_item_collection.py tests/test_layout.py tests/test_link.py tests/test_common_metadata.py
```

The selected tests use small fixtures or in-process substitutes. They need no remote service after dependency setup.
Start a fresh checkout and rebuild its virtual environment to restore initial state.
