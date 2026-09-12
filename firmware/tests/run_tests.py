#!/usr/bin/env python3
"""Build and run the firmware host tests.

These cover firmware logic that carries no Pico SDK dependency, so they need
no board, no toolchain for ARM, and no network. Prefers a system C compiler
and falls back to `python -m ziglang cc`, which needs no admin install.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TESTS = [
    ("test_sk_queue", ["tests/test_sk_queue.c", "src/sk_queue.c"]),
]

CFLAGS = ["-std=c11", "-Wall", "-Wextra", "-Werror", "-O1"]


def find_compiler():
    """Return an argv prefix that compiles C, or None."""
    for name in ("cc", "gcc", "clang"):
        path = shutil.which(name)
        if path:
            return [path]
    try:
        subprocess.run(
            [sys.executable, "-m", "ziglang", "version"],
            check=True, capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return [sys.executable, "-m", "ziglang", "cc"]


def main():
    firmware_dir = Path(__file__).resolve().parent.parent
    compiler = find_compiler()
    if compiler is None:
        print(
            "No C compiler found. Install one, or run:\n"
            f"    {sys.executable} -m pip install ziglang",
            file=sys.stderr,
        )
        return 2

    print(f"Compiler: {' '.join(compiler)}\n")
    failed = []
    with tempfile.TemporaryDirectory() as tmp:
        for name, sources in TESTS:
            binary = Path(tmp) / name
            if sys.platform == "win32":
                binary = binary.with_suffix(".exe")

            compile_cmd = compiler + CFLAGS + ["-o", str(binary)]
            compile_cmd += [str(firmware_dir / source) for source in sources]
            build = subprocess.run(compile_cmd, capture_output=True, text=True)
            if build.returncode != 0:
                print(f"=== {name}: COMPILE FAILED ===")
                print(build.stdout + build.stderr)
                failed.append(name)
                continue
            if build.stderr.strip():
                print(build.stderr)

            print(f"=== {name} ===")
            run = subprocess.run([str(binary)])
            if run.returncode != 0:
                failed.append(name)
            print()

    if failed:
        print(f"FAILED: {', '.join(failed)}")
        return 1
    print("All firmware host tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
