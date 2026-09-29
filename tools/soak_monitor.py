"""Watch a running S2 Watch for hours and report whether its memory keeps growing.

The map process (QtWebEngine) is where earlier long runs failed, so this samples the
memory of S2 Watch and each of its child processes once a minute, writes them to a CSV
file, and summarises the growth rate. It follows the app across a restart (an upgrade).
Linux only (reads /proc); it does not start S2 Watch or touch its data.

Usage: python tools/soak_monitor.py [--hours 12] [--interval 60] [--csv soak.csv]
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from datetime import datetime
from pathlib import Path


def processes() -> dict[int, tuple[int, str]]:
    """pid -> (parent pid, command line) for every readable process."""
    found = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
            parent = int((entry / "stat").read_text().rsplit(")", 1)[1].split()[1])
        except (OSError, IndexError, ValueError):
            continue
        found[int(entry.name)] = (parent, command)
    return found


def rss_mb(pid: int) -> float:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    except OSError:
        pass
    return 0.0


def find_app(table) -> int | None:
    """The S2 Watch main process: the installed launcher's Python, or a PyInstaller build."""
    wrappers = {"timeout", "bash", "sh", "nohup", "env", "sudo"}
    for pid, (_parent, command) in table.items():
        program = os.path.basename(command.split(" ")[0])
        if pid != os.getpid() and program not in wrappers \
                and ("-m s2_watch.main" in command or command.split(" ")[0].endswith("/s2-watch")) \
                and "--configure" not in command:
            return pid
    return None


def descendants(table, root: int) -> list[int]:
    children, frontier = [], [root]
    while frontier:
        parent = frontier.pop()
        for pid, (ppid, _command) in table.items():
            if ppid == parent:
                children.append(pid)
                frontier.append(pid)
    return children


def slope_per_hour(points: list[tuple[float, float]]) -> float:
    """Least-squares growth in MB per hour."""
    if len(points) < 2:
        return 0.0
    n = len(points)
    mean_t = sum(t for t, _ in points) / n
    mean_m = sum(m for _, m in points) / n
    spread = sum((t - mean_t) ** 2 for t, _ in points)
    return (sum((t - mean_t) * (m - mean_m) for t, m in points) / spread) * 3600 if spread else 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--hours", type=float, default=12)
    parser.add_argument("--interval", type=float, default=60, help="seconds between samples")
    parser.add_argument("--csv", default=f"s2-watch-soak-{datetime.now():%Y%m%d-%H%M}.csv")
    arguments = parser.parse_args()
    deadline = time.time() + arguments.hours * 3600
    runs: dict[int, list[tuple[float, float, float]]] = {}   # pid -> [(t, app MB, map MB)]
    restarts = []
    with open(arguments.csv, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["time", "pid", "app_mb", "map_mb", "total_mb", "processes"])
        last_pid = None
        while time.time() < deadline:
            table = processes()
            pid = find_app(table)
            if pid != last_pid:
                restarts.append((datetime.now(), last_pid, pid))
                last_pid = pid
            if pid is not None:
                children = descendants(table, pid)
                app = rss_mb(pid)
                web = sum(rss_mb(child) for child in children)
                runs.setdefault(pid, []).append((time.time(), app, web))
                writer.writerow([datetime.now().isoformat(timespec="seconds"), pid, f"{app:.1f}", f"{web:.1f}",
                                 f"{app + web:.1f}", 1 + len(children)])
                handle.flush()
            time.sleep(arguments.interval)

    print(f"Samples: {arguments.csv}")
    for when, old, new in restarts:
        if old is None and new is not None:
            print(f"{when:%Y-%m-%d %H:%M}  S2 Watch running (pid {new})")
        elif new is None:
            print(f"{when:%Y-%m-%d %H:%M}  S2 Watch stopped (pid {old}) — closed, or it crashed")
        else:
            print(f"{when:%Y-%m-%d %H:%M}  S2 Watch restarted (pid {old} -> {new})")
    for pid, samples in runs.items():
        # Skip the first 30 minutes: the backlog, map tiles and radar frames load then.
        settled = [s for s in samples if s[0] - samples[0][0] >= 1800] or samples
        hours = (samples[-1][0] - samples[0][0]) / 3600
        totals = [(t, a + w) for t, a, w in settled]
        growth = slope_per_hour(totals)
        print(f"pid {pid}: {hours:.1f} h; total memory {samples[0][1] + samples[0][2]:.0f} MB at start, "
              f"{samples[-1][1] + samples[-1][2]:.0f} MB at end, peak {max(a + w for _t, a, w in samples):.0f} MB; "
              f"after warm-up {growth:+.1f} MB/hour (app {slope_per_hour([(t, a) for t, a, _w in settled]):+.1f}, "
              f"map {slope_per_hour([(t, w) for t, _a, w in settled]):+.1f})")
        verdict = ("too short to judge (run at least 2 hours)" if hours < 2
                   else "steady" if abs(growth) < 10 else "slow growth — worth watching" if growth < 40
                   else "growing — likely a leak")
        print(f"          verdict: {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
