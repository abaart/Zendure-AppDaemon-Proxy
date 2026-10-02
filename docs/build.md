# Build and verify a release

Use Python 3.10 or later. Install `tools/requirements.txt` and `requirements-test.txt` into an isolated environment. AppDaemon provides its own runtime dependencies on Home Assistant.

```sh
python3 -m compileall -q apps/Zendure-AppDaemon-Proxy tools
PYTHONPATH=apps/Zendure-AppDaemon-Proxy python3 -c 'import zendure_proxy; print(zendure_proxy.ZendureProxy)'
python3 -m unittest discover -s tests
python3 -m pytest -q
python3 scripts/build_release.py
```

The build script creates `build/zendure-zensdk-proxy-appdaemon.zip` with the Python modules, GPL text and notices at the archive root. The archive excludes configuration and example files, so installation preserves the user's `apps.yaml`. GitHub's source archive at the same release tag supplies the full tests, examples and build instructions.

Publication requires completed provenance review, all tests passing, GitHub license detection `GPL-3.0`, and HACS validation with the license check enabled. A successful test result establishes tested behavior; the provenance record supplies separate evidence about publication rights.
