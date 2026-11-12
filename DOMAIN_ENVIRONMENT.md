# Reproduce the data-engineering environment

Source: https://github.com/mvantellingen/python-zeep/commit/1b7072c0dba397b86e8a47cf766a39f981b3a2de

This repository republishes the tracked files of that upstream commit under their original licenses.
Upstream CI workflows are omitted from the evaluation snapshot. This commit adds environment instructions
and the dependency lock only. The source license is MIT AND BSD-3-Clause; original license and attribution files remain.

Python 3.13.15 and uv were used on Linux. From a fresh checkout:

```sh
uv venv --python 3.13.15 .venv
export PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 DISABLE_SQLALCHEMY_CEXT=1
mkdir -p .venv/tmp
export TMPDIR="$PWD/.venv/tmp"
uv pip install --python .venv/bin/python -r requirements-domain.lock
uv pip install --python .venv/bin/python --no-build-isolation --no-deps -e .
.venv/bin/python -B -m pytest -q -o addopts= -p no:cacheprovider -p requests_mock.contrib._pytest_plugin tests/test_wsdl.py tests/test_xsd.py tests/test_soap_multiref.py tests/test_soap_xop.py tests/test_transports.py
```

The selected tests use small fixtures or in-process substitutes. They need no remote service after dependency setup.
Start a fresh checkout and rebuild its virtual environment to restore initial state.
