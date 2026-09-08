#!/usr/bin/env python3
import subprocess
import sys
import time

# Run with --dry-run and --poll-interval 3 to observe consecutive scan reports
proc = subprocess.Popen(
    [sys.executable, "run_bybit_bot.py", "--dry-run", "--poll-interval", "3"],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    bufsize=1,
)

time.sleep(8)
proc.terminate()
try:
    stdout, _ = proc.communicate(timeout=3)
except Exception:
    proc.kill()
    stdout, _ = proc.communicate()

print("--- LIVE SCANNER REFRESH OUTPUT ---")
print(stdout)
