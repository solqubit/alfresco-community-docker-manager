# Runs the unit tests inside a Linux container (the manager requires POSIX).
$ErrorActionPreference = 'Stop'
$repository = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
& docker build -q -t acdm-test-runner:local -f (Join-Path $repository 'tests/Dockerfile') $repository
if ($LASTEXITCODE -ne 0) { throw 'Test runner build failed' }
& docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -v "${repository}:/workspace:ro" acdm-test-runner:local python3 -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'Unit tests failed' }
