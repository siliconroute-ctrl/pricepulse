"""Try the whole app on your own computer, with example prices and a TEST sign-in.

    python run_local.py            then open http://127.0.0.1:8000

Nothing here is real: prices are made up, sign-in accepts any test name, and no
payment can be made. It uses its own data folder (data/local) and leaves the real
database alone. Test ID numbers to type in: 8001015009087 (adult), 1503155009089 (child).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    data = HERE / "data" / "local"
    web = Path(tempfile.mkdtemp(prefix="pp-web-")) / "frontend"
    shutil.copytree(HERE.parent / "frontend", web, ignore=shutil.ignore_patterns("tests"))
    (web / "config.js").write_text('window.PRICEPULSE_CONFIG = { apiBase: "", authMode: "dev" };\n')
    env = {
        **os.environ,
        "PP_DATA_DIR": str(data),
        "PP_FRONTEND_DIR": str(web),
        "PP_AUTH_DEV": "1",
        "PP_OPEN_PRICES": "0",
        "PP_ENV": "",
        "PP_ID_SECRET": "local-testing-only-not-a-real-secret",
        "PP_ALLOWED_ORIGINS": "http://127.0.0.1:8000,http://localhost:8000",
    }
    env.pop("PP_DATABASE_URL", None)
    if not (data / "pricepulse.db").exists():
        subprocess.run(
            [sys.executable, "make_sample.py", "--regions", "paarl,wellington"], cwd=HERE, env=env, check=True
        )
    print("\nPricePulse TEST copy: open http://127.0.0.1:8000  (Ctrl+C to stop)\n")
    try:
        return subprocess.call(
            [sys.executable, "-m", "uvicorn", "api:app", "--port", "8000"], cwd=HERE, env=env
        )
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
