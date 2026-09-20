"""Streamlit Cloud 진입점. 현재 앱 구현은 src/app.py에 유지합니다."""

import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).parent / "src" / "app.py"), run_name="__main__")
