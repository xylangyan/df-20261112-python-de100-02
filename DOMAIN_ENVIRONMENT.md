# Reproduce the data-engineering environment

Source: https://github.com/mloesch/sickle/commit/14ef88e1aecce1b767ef7a3824e90c009899e1f7

This repository republishes the tracked files of that upstream commit under their original licenses.
Upstream CI workflows are omitted from the evaluation snapshot. This commit adds environment instructions
and the dependency lock only. The source license is BSD-3-Clause; original license and attribution files remain.

Python 3.11.16 and uv were used on Linux. From a fresh checkout:

```sh
uv venv --python 3.11.16 .venv
export PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 DISABLE_SQLALCHEMY_CEXT=1
mkdir -p .venv/tmp
export TMPDIR="$PWD/.venv/tmp"
uv pip install --python .venv/bin/python -r requirements-domain.lock
uv pip install --python .venv/bin/python --no-build-isolation --no-deps -e .
.venv/bin/python -B -m pytest -q -o addopts= -p no:cacheprovider sickle/tests
```

The selected tests use small fixtures or in-process substitutes. They need no remote service after dependency setup.
Start a fresh checkout and rebuild its virtual environment to restore initial state.
