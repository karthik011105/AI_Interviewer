"""Run the backend and restart it when a backend file changes (Windows dev).

uvicorn's own --reload stops the server on Windows by sending CTRL_C_EVENT,
which reaches every process sharing the console - the reloader included - so
the first code change took the whole backend down instead of restarting it.
This runner restarts the server process directly and never signals itself.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WATCH_DIR = REPO_ROOT / "backend"
POLL_SECONDS = 1.0
SETTLE_SECONDS = 0.5  # editors often write a file in more than one step


def _snapshot() -> dict[Path, float]:
	mtimes: dict[Path, float] = {}
	for path in WATCH_DIR.rglob("*.py"):
		try:
			mtimes[path] = path.stat().st_mtime
		except OSError:
			pass  # deleted between listing and stat
	return mtimes


def _start(port: str) -> subprocess.Popen:
	command = [sys.executable, "-m", "uvicorn", "backend.main:app", "--port", port]
	flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
	return subprocess.Popen(command, cwd=REPO_ROOT, creationflags=flags)


def _stop(process: subprocess.Popen) -> None:
	if process.poll() is not None:
		return
	process.terminate()
	try:
		process.wait(timeout=10)
	except subprocess.TimeoutExpired:
		process.kill()
		process.wait()


def main() -> int:
	port = sys.argv[1] if len(sys.argv) > 1 else "8000"
	print(f"Watching {WATCH_DIR} - the backend restarts when a .py file changes.", flush=True)
	seen = _snapshot()
	process = _start(port)
	try:
		while True:
			time.sleep(POLL_SECONDS)
			current = _snapshot()
			if current == seen:
				continue
			time.sleep(SETTLE_SECONDS)
			current = _snapshot()
			changed = sorted(
				str(path.relative_to(REPO_ROOT))
				for path in set(current) | set(seen)
				if current.get(path) != seen.get(path)
			)
			seen = current
			print(f"Changed: {', '.join(changed[:3])}{' ...' if len(changed) > 3 else ''} - restarting.", flush=True)
			_stop(process)
			process = _start(port)
	except KeyboardInterrupt:
		pass
	finally:
		_stop(process)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
