"""Package DBCheck Windows distribution folder into a release package.

Usage: python package_windows.py <distpath> <version>

  distpath  - path to 'dist' directory (contains DBCheck-Windows folder)
  version   - version string, e.g. v2.5.0
"""
import os
import shutil
import zipfile
import sys


def main():
    if len(sys.argv) != 3:
        print("Usage: python package_windows.py <distpath> <version>")
        sys.exit(1)

    distpath = sys.argv[1]
    version = sys.argv[2]

    src = os.path.join(distpath, "DBCheck-Windows")
    dst_name = "DBCheck-Windows-" + version
    dst = os.path.join(distpath, dst_name)
    zip_path = dst + ".zip"

    if not os.path.isdir(src):
        print("[ERROR] Source directory not found: " + src)
        sys.exit(1)

    # Copy source to versioned folder
    shutil.copytree(src, dst, dirs_exist_ok=True)
    print("[OK] Copied files to " + dst_name)

    # Write start.bat
    start_bat = (
        "@echo off\r\n"
        "title DBCheck Web UI\r\n"
        'cd /d "%%~dp0"\r\n'
        "echo ==========================================\r\n"
        "echo   DBCheck Database Inspection Tool\r\n"
        "echo ==========================================\r\n"
        "echo Starting Web UI server...\r\n"
        "echo Open browser: http://localhost:5000\r\n"
        "echo Press Ctrl+C to stop.\r\n"
        "echo ==========================================\r\n"
        '\r\n'
        'dbcheck.exe web\r\n'
    )
    with open(os.path.join(dst, "start.bat"), "w", encoding="utf-8") as f:
        f.write(start_bat)

    # Write start.sh
    start_sh = (
        "#!/bin/bash\n"
        'cd "$(dirname "$0")"\n'
        "echo ==========================================\n"
        "echo \"  DBCheck Database Inspection Tool\"\n"
        "echo ==========================================\n"
        "echo \"Starting Web UI server...\"\n"
        "echo \"Open browser: http://localhost:5000\"\n"
        "exec ./dbcheck.exe web\n"
    )
    with open(os.path.join(dst, "start.sh"), "w", encoding="utf-8") as f:
        f.write(start_sh)

    # Write README.txt
    readme = (
        "DBCheck Windows Distribution\r\n"
        "=============================\r\n"
        "\r\n"
        "To start: double-click start.bat\r\n"
        "Then open http://localhost:5000 in your browser.\r\n"
        "\r\n"
        "Version: " + version + "\r\n"
    )
    with open(os.path.join(dst, "README.txt"), "w", encoding="utf-8") as f:
        f.write(readme)

    # Create ZIP archive
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(dst):
            for fname in files:
                fpath = os.path.join(root, fname)
                arcname = os.path.relpath(fpath, distpath)
                zf.write(fpath, arcname)
    print("[OK] Created " + dst_name + ".zip")

    print("")
    print("Version:  " + version)
    print("Output:   " + zip_path)
    print("Folder:   " + dst)
    print("To start: " + os.path.join(dst, "start.bat"))


if __name__ == "__main__":
    main()
