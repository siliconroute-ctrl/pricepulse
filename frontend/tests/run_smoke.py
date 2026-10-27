"""Start the API with example data on a spare port, then run the browser smoke test.

    python frontend/tests/run_smoke.py [screenshot-folder]
"""

import os
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
    env = {**os.environ, "PP_DATA_DIR": data, "PP_RATE_LIMIT_PER_MIN": "1000"}
    env.pop("PP_DATABASE_URL", None)
    subprocess.run([sys.executable, "make_sample.py", "--regions", "paarl,wellington", "--days", "5"],
                   cwd=BACKEND, env=env, check=True, stdout=subprocess.DEVNULL)
    port = free_port()
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "api:app", "--port", str(port)],
                              cwd=BACKEND, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
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
