# SPDX-License-Identifier: GPL-3.0-only
"""Build and inspect the deterministic HACS source archive."""
from pathlib import Path
import hashlib
import json
import zipfile

ROOT = Path(__file__).resolve().parents[1]

def build(output=None):
    output = Path(output or ROOT / 'build/zendure-zensdk-proxy-appdaemon.zip')
    modules = sorted((ROOT / 'apps/Zendure-AppDaemon-Proxy').glob('*.py'))
    if len(modules) < 16:
        raise ValueError('Expected the complete runtime module set')
    sources = modules + [ROOT / 'LICENSE', ROOT / 'NOTICE']
    manifest = json.loads((ROOT / 'audit/provenance.json').read_text())
    indexed = {entry['path']: entry for entry in manifest['files']}
    for source in sources:
        entry = indexed.get(str(source.relative_to(ROOT)))
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if not entry or entry.get('sha256') != digest or entry.get('decision') not in ('independent implementation', 'owner-authored retained', 'standard license text'):
            raise ValueError(f'Provenance entry missing or stale: {source.name}')
        if source.suffix == '.py' and not source.read_text().startswith('# SPDX-License-Identifier: GPL-3.0-only'):
            raise ValueError(f'Missing source license header: {source.name}')
    output.parent.mkdir(parents=True, exist_ok=True)
    # Build in memory; refuse to replace a different existing archive.
    import io
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for source in sources:
            entry = zipfile.ZipInfo(source.name, (2026, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o644 << 16
            archive.writestr(entry, source.read_bytes())
    content = buffer.getvalue()
    if output.exists():
        if output.read_bytes() != content:
            raise FileExistsError(f'Refusing to overwrite existing archive: {output}')
    else:
        with output.open('xb') as stream:
            stream.write(content)
    with zipfile.ZipFile(output) as archive:
        if set(archive.namelist()) != {p.name for p in sources}:
            raise ValueError('Unexpected archive content')
        if archive.testzip() is not None:
            raise ValueError('Archive CRC failed')
    return output, hashlib.sha256(content).hexdigest()

if __name__ == '__main__':
    archive, digest = build()
    print(json.dumps({'archive': str(archive), 'sha256': digest}))
