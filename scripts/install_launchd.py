"""Install (or remove) the Bond Monitor scheduled jobs as macOS launchd agents.

Times are the Mac's local time (America/New_York here). launchd runs a job missed while the
Mac was asleep as soon as it wakes. Logs: data/logs/<job>.log.

    uv run python scripts/install_launchd.py --dry-run     # print the plists, change nothing
    uv run python scripts/install_launchd.py               # install + load
    uv run python scripts/install_launchd.py --uninstall   # unload + remove
    launchctl print gui/$(id -u)/com.chilemonitor.auctions-daily   # inspect one job
    launchctl kickstart gui/$(id -u)/com.chilemonitor.auctions-daily  # run one now
"""

from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
AGENTS = Path.home() / "Library" / "LaunchAgents"
LOGS = REPO / "data" / "logs"
PREFIX = "com.chilemonitor"
WEEKDAYS = (1, 2, 3, 4, 5)  # launchd: 0/7 = Sunday

# label suffix -> (module, [(weekday, hour, minute), ...])
JOBS = {
    "auctions-daily": ("monitor.jobs.auctions_daily", [(d, 7, 30) for d in WEEKDAYS]),
    "auctions-watch": ("monitor.jobs.auctions_watch", [(d, 8, 0) for d in WEEKDAYS]),
    "auctions-evening": ("monitor.jobs.auctions_evening", [(d, 18, 0) for d in WEEKDAYS]),
    "bond-master-weekly": ("monitor.jobs.bond_master_weekly", [(1, 7, 0)]),
}


def plist(name: str, module: str, times: list[tuple[int, int, int]], uv: str) -> dict:
    log = str(LOGS / f"{name}.log")
    return {
        "Label": f"{PREFIX}.{name}",
        "ProgramArguments": [uv, "run", "--project", str(REPO), "python", "-m", module],
        "WorkingDirectory": str(REPO),
        "StartCalendarInterval": [{"Weekday": w, "Hour": h, "Minute": m} for w, h, m in times],
        "StandardOutPath": log,
        "StandardErrorPath": log,
        "EnvironmentVariables": {"PATH": f"{Path(uv).parent}:/usr/local/bin:/usr/bin:/bin"},
        "RunAtLoad": False,
        "ProcessType": "Background",
    }


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    a = ap.parse_args()
    uv = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
    domain = f"gui/{os.getuid()}"
    for name, (module, times) in JOBS.items():
        label = f"{PREFIX}.{name}"
        path = AGENTS / f"{label}.plist"
        if a.uninstall:
            _launchctl("bootout", f"{domain}/{label}")
            path.unlink(missing_ok=True)
            print(f"removed {label}")
            continue
        data = plistlib.dumps(plist(name, module, times, uv)).decode()
        if a.dry_run:
            print(f"--- {path}\n{data}")
            continue
        LOGS.mkdir(parents=True, exist_ok=True)
        AGENTS.mkdir(parents=True, exist_ok=True)
        _launchctl("bootout", f"{domain}/{label}")  # reload cleanly if already installed
        path.write_text(data)
        r = _launchctl("bootstrap", domain, str(path))
        status = "loaded" if r.returncode == 0 else f"FAILED: {r.stderr.strip()}"
        print(f"{label}: {status}")


if __name__ == "__main__":
    main()
