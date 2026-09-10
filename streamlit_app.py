"""Streamlit Cloud 진입점. 현재 앱 구현은 mvp/app.py에 유지합니다."""

import runpy
import sys
import types
from pathlib import Path

# Streamlit Cloud는 Git 배포 때 프로세스를 유지하면서 진입 파일만 다시
# 실행할 수 있습니다. 그때 이전 mvp 모듈과 새 app.py가 섞이면 새 함수를
# import하지 못하므로, 배포 버전이 바뀐 첫 실행에서만 모듈 캐시를 비웁니다.
DEPLOYMENT_REVISION = "2026-09-10-operational-rag-v2"
STATE_MODULE = "_sch_guide_deployment_state"
deployment_state = sys.modules.get(STATE_MODULE)
if getattr(deployment_state, "revision", None) != DEPLOYMENT_REVISION:
    for module_name in tuple(sys.modules):
        if module_name == "mvp" or module_name.startswith("mvp."):
            sys.modules.pop(module_name, None)
    if deployment_state is None:
        deployment_state = types.ModuleType(STATE_MODULE)
        sys.modules[STATE_MODULE] = deployment_state
    deployment_state.revision = DEPLOYMENT_REVISION

runpy.run_path(str(Path(__file__).parent / "mvp" / "app.py"), run_name="__main__")
