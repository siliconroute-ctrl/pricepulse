"""Start the API with example data and TEST sign-in on a spare port, then run the browser test.

    python frontend/tests/run_smoke.py [screenshot-folder]
"""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    data = tempfile.mkdtemp(prefix="pp-smoke-")
    web = Path(tempfile.mkdtemp(prefix="pp-web-")) / "frontend"
    shutil.copytree(ROOT / "frontend", web, ignore=shutil.ignore_patterns("tests"))
    # Test sign-in: the real server (PP_AUTH_DEV off) refuses these sign-ins.
    (web / "config.js").write_text('window.PRICEPULSE_CONFIG = { apiBase: "", authMode: "dev" };\n')
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "PP_DATA_DIR": data,
        "PP_FRONTEND_DIR": str(web),
        "PP_RATE_LIMIT_PER_MIN": "5000",
        "PP_AUTH_DEV": "1",
        "PP_OPEN_PRICES": "0",
        "PP_ID_SECRET": "smoke-test-secret-0123456789abcdef",
        "PP_PAYFAST_MERCHANT_ID": "10000100",
        "PP_PAYFAST_MERCHANT_KEY": "46f0cd694581a",
        "PP_PAYFAST_PASSPHRASE": "smoke-passphrase",
        "PP_PUBLIC_URL": base,
    }
    env.pop("PP_DATABASE_URL", None)
    subprocess.run(
        [sys.executable, "make_sample.py", "--regions", "paarl,wellington", "--days", "5"],
        cwd=BACKEND, env=env, check=True, stdout=subprocess.DEVNULL,
    )
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api:app", "--port", str(port)],
        cwd=BACKEND, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(base + "/api/v1/health", timeout=1)
                break
            except OSError:
                time.sleep(0.2)
        args = ["node", str(Path(__file__).parent / "smoke.mjs"), base] + sys.argv[1:2]
        return subprocess.run(args).returncode
    finally:
        server.terminate()
        server.wait(timeout=10)


if __name__ == "__main__":
    sys.exit(main())
