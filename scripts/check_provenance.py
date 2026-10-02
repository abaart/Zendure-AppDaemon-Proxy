# SPDX-License-Identifier: GPL-3.0-only
"""Require a declared source and current digest for every distributable file."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IGNORED = {'.git', '.venv', '__pycache__', '.pytest_cache', 'build'}

def distributable_files():
    return sorted(p for p in ROOT.rglob('*') if p.is_file()
                  and not any(part in IGNORED for part in p.relative_to(ROOT).parts)
                  and p.relative_to(ROOT).parts[:2] != ('tools', 'sandbox')
                  and not p.name.startswith('live-'))

def check():
    path = ROOT / 'audit/provenance.json'
    manifest = json.loads(path.read_text())
    actual = {str(p.relative_to(ROOT)): p for p in distributable_files()}
    entries = {entry['path']: entry for entry in manifest['files']}
    if len(entries) != len(manifest['files']) or set(entries) != set(actual):
        raise ValueError('Manifest file set does not match distributable file set')
    for name, source in actual.items():
        entry = entries[name]
        if not entry.get('source') or not entry.get('decision') or not entry.get('rights_basis'):
            raise ValueError(f'Incomplete provenance: {name}')
        if name != 'audit/provenance.json' and entry['sha256'] != hashlib.sha256(source.read_bytes()).hexdigest():
            raise ValueError(f'Stale provenance digest: {name}')
        if source.suffix == '.py' and not source.read_text().startswith('# SPDX-License-Identifier: GPL-3.0-only'):
            raise ValueError(f'Missing GPL source header: {name}')
    return len(entries)

if __name__ == '__main__':
    print(f'Provenance declarations and hashes verified for {check()} files')
