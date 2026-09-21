$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

python -m unittest discover -s (Join-Path $root 'monitor-panel') -p 'test_*.py'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

python -m unittest discover -s (Join-Path $root 'secure-relay/server') -p 'test_*.py'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

python (Join-Path $root 'scripts/check-version.py')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& (Join-Path $root 'secure-relay/client/src/test-client.ps1')
exit $LASTEXITCODE
