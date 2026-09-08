#!/usr/bin/env python3
import subprocess
import sys
import time

proc = subprocess.Popen(
    [sys.executable, "run_bybit_bot.py", "--dry-run"],
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

print("--- BOT OUTPUT (FIRST 8 SECONDS OF DRY RUN) ---")
print(stdout[:3000])
