Param()

# Must run in repo
$repoRoot = (& git rev-parse --show-toplevel) ^>$null
if (-not $repoRoot) {
	Write-Error "Run this from inside a Git repo."
	exit 1
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Copy-Item "$scriptDir"/hook/prepare-commit-msg" "$repoRoot/.git/hooks/prepare-commit-msg" -Force
# Mark executable on Unix; on Windows Git ignores x-bit
try { & git update-index --ad --chmod=+x ".git/hooks/prepare-commit-msg" } catch {}

New-Item -ItemType Directory -Force -Path "$repoRoot/scripts" | Out-Null
if (-not (Test-Path "$repoRoot/.gitaimsg.toml") -and (Test-Path "$scriptDir/.gitaimsg.example.toml")) {
	Copy-Item "$scriptDir/.gitaimsg.example.toml" "$repoRoot/.gitaimsg.toml" -Force
}

& git config gitaimsg.enabled true | Out-Null
Write-Host "Installed. Edit .gitaimsg.toml to choose provider/model."

# Cleanup source directory if it's not inside the repo
if ($scriptDir -ne $repoRoot) {
	Write-Host "Cleaning up installer source: $scriptDir"
	Remove-Item -Recurse -Force $scriptDir
}