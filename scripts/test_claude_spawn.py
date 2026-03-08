#!/usr/bin/env python3
"""Standalone test: spawn Claude Code the same way Laya does and watch output."""

import asyncio
import os
import sys

PROMPT = "What files are in the current directory? List them briefly."
REPO_PATH = os.getcwd()


async def main():
    args = [
        "claude",
        "-p",
        PROMPT,
        "--output-format",
        "stream-json",
        "--verbose",
        "--no-session-persistence",
        "--permission-mode",
        "default",
        "--max-budget-usd",
        "5",
    ]

    print(f"[spawn] command: {' '.join(args[:4])}... (prompt truncated)")
    print(f"[spawn] cwd: {REPO_PATH}")
    print(f"[spawn] stdin=PIPE, stdout=PIPE, stderr→stdout")
    print()

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=REPO_PATH,
        env={**os.environ},
    )
    print(f"[spawn] pid={proc.pid} — waiting for output...\n")

    line_count = 0
    buf = ""
    start = asyncio.get_event_loop().time()

    while True:
        try:
            chunk = await asyncio.wait_for(proc.stdout.read(8192), timeout=10.0)
        except asyncio.TimeoutError:
            elapsed = round(asyncio.get_event_loop().time() - start)
            alive = proc.returncode is None
            print(f"[wait] {elapsed}s elapsed, lines_read={line_count}, buf_len={len(buf)}, alive={alive}")
            if buf:
                print(f"[wait] partial buffer: {buf[:500]!r}")
            if elapsed > 120:
                print("[timeout] 2 minutes with no output — killing process")
                proc.kill()
                break
            continue

        if not chunk:
            # EOF
            if buf.strip():
                line_count += 1
                print(f"[line {line_count:>3}] (final) {buf.strip()[:300]}")
            print(f"\n[eof] lines_read={line_count}")
            break

        decoded = chunk.decode("utf-8", errors="replace")
        buf += decoded

        while "\n" in buf:
            line_str, buf = buf.split("\n", 1)
            line_str = line_str.rstrip("\r")
            if not line_str:
                continue
            line_count += 1
            # Show first 10 lines in full, then summaries
            if line_count <= 10:
                print(f"[line {line_count:>3}] {line_str[:500]}")
            else:
                # Just show type for JSON lines
                if line_str.startswith("{"):
                    import json
                    try:
                        t = json.loads(line_str).get("type", "?")
                        print(f"[line {line_count:>3}] <{t}>")
                    except Exception:
                        print(f"[line {line_count:>3}] {line_str[:200]}")
                else:
                    print(f"[line {line_count:>3}] {line_str[:200]}")

    exit_code = await proc.wait()
    print(f"[exit] code={exit_code}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[interrupted]")
        sys.exit(1)
