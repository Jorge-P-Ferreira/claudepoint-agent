# One-paste laptop onboarding (Windows PowerShell). The bot's /pair prints this line:
#
#   $env:CP_PAIR='<CODE> <BROKER_URL>'; irm https://raw.githubusercontent.com/Jorge-P-Ferreira/claudepoint-agent/main/scripts/bootstrap.ps1 | iex
#
# Installs the agent (via uv, which also fetches a Python if needed), pairs it, and
# registers a scheduled task that starts it at logon. Projects live under
# ~\Projects unless $env:CLAUDEPOINT_PROJECTS says otherwise. Safe to re-run with a
# fresh code. Runs via `iex`, so it uses `return`/`throw`, never `exit` (which would
# close the user's window).

$ErrorActionPreference = 'Stop'

& {
    $pair = "$env:CP_PAIR".Trim() -split '\s+'
    Remove-Item Env:CP_PAIR -ErrorAction SilentlyContinue
    if ($pair.Count -ne 2 -or -not $pair[0]) {
        Write-Host "Set `$env:CP_PAIR='<code> <broker-url>' first (get both from /pair on the bot)." -ForegroundColor Red
        return
    }
    $code, $broker = $pair
    $package = if ($env:CLAUDEPOINT_AGENT_SOURCE) { $env:CLAUDEPOINT_AGENT_SOURCE } else {
        'https://github.com/Jorge-P-Ferreira/claudepoint-agent/archive/refs/heads/main.zip'
    }
    $projects = if ($env:CLAUDEPOINT_PROJECTS) { $env:CLAUDEPOINT_PROJECTS } else {
        Join-Path $HOME 'Projects'
    }

    # Checked before pairing so a missing prerequisite doesn't burn the one-time code.
    if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
        Write-Host "Claude Code isn't installed (no 'claude' on PATH)." -ForegroundColor Red
        Write-Host "Install it, run 'claude' once to log in, then paste the /pair line again."
        return
    }

    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Host '==> Installing uv'
        powershell -ExecutionPolicy ByPass -NoProfile -Command 'irm https://astral.sh/uv/install.ps1 | iex'
        $env:Path = (Join-Path $HOME '.local\bin') + ';' + $env:Path
    }

    Write-Host '==> Installing the agent'
    uv tool install --force --python 3.12 $package
    if ($LASTEXITCODE) { throw 'uv tool install failed' }
    $agent = Join-Path (uv tool dir --bin) 'claude-telegram-agent.exe'

    Write-Host "==> Pairing (projects folder: $projects)"
    New-Item -ItemType Directory -Force -Path $projects | Out-Null
    & $agent pair $code --broker $broker --project-roots $projects
    if ($LASTEXITCODE) { throw 'pairing failed (code expired or already used? send /pair again)' }

    Write-Host '==> Starting it in the background at logon'
    & $agent service install
    if ($LASTEXITCODE) { throw 'service install failed' }

    Write-Host ''
    Write-Host 'Done - the bot should say this laptop is connected.' -ForegroundColor Green
    Write-Host "If Claude Code was never logged in here, run 'claude' once to log in."
}
