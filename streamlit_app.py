"""Streamlit Cloud 진입점. 현재 앱 구현은 mvp/app.py에 유지합니다."""

import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).parent / "mvp" / "app.py"), run_name="__main__")
