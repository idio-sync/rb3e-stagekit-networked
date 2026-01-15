#!/usr/bin/env python3
"""
Download MPV library for bundling with PyInstaller builds.

This script downloads the libmpv shared library needed for video playback.
Run this before building with PyInstaller if you want MPV bundled in the executable.
"""

import os
import sys
import urllib.request
import zipfile
import tempfile
import shutil

# libmpv download URL (Windows x64)
MPV_URL = "https://sourceforge.net/projects/mpv-player-windows/files/libmpv/mpv-dev-x86_64-20240121-git-a39f9b6.7z/download"
MPV_FILENAME = "mpv-dev.7z"

def download_mpv_windows():
    """Download and extract libmpv for Windows."""
    print("Downloading libmpv for Windows...")
    print(f"URL: {MPV_URL}")
    print()
    print("NOTE: This script provides guidance. For the latest version:")
    print("1. Visit: https://sourceforge.net/projects/mpv-player-windows/files/libmpv/")
    print("2. Download the latest mpv-dev-x86_64-*.7z file")
    print("3. Extract and copy the DLL(s) to this Dashboard directory")
    print()
    print("Expected files: mpv-1.dll, mpv-2.dll, or libmpv-2.dll")
    print()

    # Check if 7z is available for extraction
    if shutil.which('7z') is None and shutil.which('7za') is None:
        print("ERROR: 7-Zip not found. Please install 7-Zip or manually download and extract.")
        print("       On Windows: choco install 7zip")
        print("       Or download from: https://www.7-zip.org/")
        return False

    return True

def check_existing():
    """Check if MPV DLLs already exist."""
    dll_names = ['mpv-1.dll', 'mpv-2.dll', 'libmpv-2.dll', 'libmpv.so.2', 'libmpv.dylib']
    found = []
    for dll in dll_names:
        if os.path.exists(dll):
            found.append(dll)

    if found:
        print(f"Found existing MPV libraries: {', '.join(found)}")
        return True
    return False

def main():
    print("=" * 60)
    print("MPV Library Download Helper")
    print("=" * 60)
    print()

    if check_existing():
        print()
        response = input("MPV libraries already exist. Re-download? [y/N]: ").strip().lower()
        if response != 'y':
            print("Keeping existing libraries.")
            return

    if sys.platform == 'win32':
        download_mpv_windows()
    elif sys.platform == 'darwin':
        print("macOS detected.")
        print()
        print("Install MPV via Homebrew:")
        print("  brew install mpv")
        print()
        print("Then copy the library:")
        print("  cp /opt/homebrew/lib/libmpv.dylib .")
        print("  # or for Intel Macs:")
        print("  cp /usr/local/lib/libmpv.dylib .")
    else:
        print("Linux detected.")
        print()
        print("Install MPV via package manager:")
        print("  Ubuntu/Debian: sudo apt install libmpv-dev")
        print("  Fedora: sudo dnf install mpv-libs-devel")
        print("  Arch: sudo pacman -S mpv")
        print()
        print("Then copy the library:")
        print("  cp /usr/lib/x86_64-linux-gnu/libmpv.so.2 .")
        print("  # Path may vary by distribution")

if __name__ == '__main__':
    main()
