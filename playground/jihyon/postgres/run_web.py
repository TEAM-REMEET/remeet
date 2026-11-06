"""web 앱을 PostgreSQL 데이터로 실행.

web 폴더에서 (테마 설정 .streamlit/config.toml 을 쓰려면 web 폴더에서 실행해야 해요):
    streamlit run ../playground/<이름>/postgres/run_web.py
"""

import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import paths  # noqa: E402
import web_patch  # noqa: E402

web_patch.apply()
runpy.run_path(str(paths.WEB_DIR / "app.py"), run_name="__main__")
