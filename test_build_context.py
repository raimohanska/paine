"""Catch missing/ignored COPY inputs even on developer machines without Docker."""

from fnmatch import fnmatchcase
from pathlib import Path
import shlex


def test_addon_copy_inputs_reach_docker():
    context = Path(__file__).parent / 'paine'
    rules = [s.strip() for s in (context/'.dockerignore').read_text().splitlines()
             if s.strip() and not s.lstrip().startswith('#')]
    # This context uses a flat, deny-by-default allowlist, not arbitrary Docker patterns.
    assert rules[0] == '*' and all(s.startswith('!') and '/' not in s for s in rules[1:])
    for line in (context/'Dockerfile').read_text().replace('\\\n', ' ').splitlines():
        if not line.strip().upper().startswith('COPY '):
            continue
        for pattern in shlex.split(line)[1:-1]:
            matches = list(context.glob(pattern))
            assert matches, f'Dockerfile COPY {pattern}: no source in {context.name}/'
            for path in matches:
                assert any(fnmatchcase(path.name, rule[1:]) for rule in rules[1:]), (
                    f'Dockerfile copies {path.name}, but paine/.dockerignore excludes it')
