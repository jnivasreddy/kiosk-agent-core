"""Build the AgentCore ZIP: python scripts/build_runtime.py (no AWS upload)."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ["runtime.py", "api.py", "agent.py", "tools.py", "database.py", "history.py",
           "authentication.py", "sessions.py"]
OUTPUT = ROOT / "dist" / "kiosk-runtime.zip"


def main():
    OUTPUT.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="kiosk-package-") as temporary:
        package = Path(temporary)
        subprocess.run([
            sys.executable, "-m", "pip", "install",
            "--platform", "manylinux2014_aarch64", "--implementation", "cp",
            "--python-version", "3.12", "--abi", "cp312",
            "--only-binary=:all:", "--no-compile", "--target", str(package),
            "-r", str(ROOT / "requirements-agentcore.txt"),
        ], check=True)
        for name in SOURCES:
            shutil.copy2(ROOT / name, package / name)
        with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED) as archive:
            for file in package.rglob("*"):
                if file.is_file():
                    archive.write(file, file.relative_to(package))
    with zipfile.ZipFile(OUTPUT) as archive:
        assert archive.testzip() is None
        assert all(name in archive.namelist() for name in SOURCES)
        assert not any(Path(name).name == ".env" or ".sqlite3" in name
                       for name in archive.namelist())
    print(f"Verified {OUTPUT} ({OUTPUT.stat().st_size / 1024**2:.1f} MB)")
    print("Python 3.12 / Linux ARM64. No secrets included; nothing uploaded.")


if __name__ == "__main__":
    main()
