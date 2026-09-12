"""Check that PyInstaller includes every extracted application module."""
from pathlib import Path
from PyInstaller.archive.readers import CArchiveReader

root = Path(__file__).resolve().parents[1]
executable = root / 'dist' / 'RB3E-Dashboard.exe'
archive = CArchiveReader(str(executable))
pyz_name = next(name for name in archive.toc if name.endswith('.pyz'))
pyz = archive.open_embedded_archive(pyz_name)
expected = []
for path in sorted((root / 'rb3dashboard').rglob('*.py')):
    parts = list(path.relative_to(root).with_suffix('').parts)
    if parts[-1] == '__init__':
        parts.pop()
    expected.append('.'.join(parts))
missing = [name for name in expected if name not in pyz.toc]
assert not missing, 'Missing packaged modules: ' + ', '.join(missing)
print('Archive verified: {} application modules in {}'.format(len(expected), executable))
for name in expected:
    print('  ' + name)
