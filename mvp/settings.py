"""새 MVP 전용 설정. 기존 유료 API 설정과 분리합니다."""

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DIMENSIONS = 384


class GuideError(ValueError):
    """키·원문·개인정보를 포함하지 않는 사용자용 오류입니다."""


@dataclass(frozen=True)
class Settings:
    mode: str = "staff"
    supabase_url: str = ""
    supabase_key: str = ""
    llm_provider: str = "disabled"
    llm_url: str = ""
    llm_key: str = ""
    llm_model: str = ""
    llm_approved: bool = False
    groq_free_confirmed: bool = False
    daily_limit: int = 40
    user_daily_limit: int = 10
    min_similarity: float = 0.38
    data_dir: str = ""
    storage_backend: str = "local"
    storage_bucket: str = "guide-originals"
    ocr_enabled: bool = False

    @property
    def library_dir(self):
        return Path(self.data_dir).expanduser().resolve() if self.data_dir else ROOT / "data" / "library"

    @property
    def cloud_ready(self):
        return bool(self.supabase_url and self.supabase_key)

    def llm_endpoint(self):
        if self.llm_provider == "disabled":
            raise GuideError("AI 서버가 아직 연결되지 않았습니다. 검색된 원문은 확인할 수 있습니다. (AI_SETUP)")
        if not self.llm_approved:
            raise GuideError("관리자가 AI 서버의 자료 처리·이용 조건을 확인한 뒤 연결 설정을 완료해야 합니다. (AI_APPROVAL)")
        if self.llm_provider == "groq_free":
            if not self.groq_free_confirmed or not self.llm_key:
                raise GuideError("Groq 무료 계정 확인과 API 키 설정이 필요합니다. (AI_SETUP)")
            if self.llm_model not in {"openai/gpt-oss-20b", "openai/gpt-oss-120b"}:
                raise GuideError("무료 시험 연결의 모델 설정을 확인해 주세요. (AI_MODEL)")
            return "https://api.groq.com/openai/v1/chat/completions"
        if self.llm_provider != "internal" or not self.llm_url or not self.llm_model:
            raise GuideError("병원 AI 서버 주소와 모델명을 설정해 주세요. (AI_SETUP)")
        parsed = urlparse(self.llm_url)
        if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise GuideError("AI 서버 주소에 인증정보나 쿼리를 넣지 마세요. (AI_URL)")
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        ):
            raise GuideError("AI 서버는 HTTPS를 사용하세요. HTTP는 이 PC의 localhost만 허용합니다. (AI_URL)")
        return self.llm_url.rstrip("/") + "/chat/completions"


def reject_secret_key(key):
    if key.startswith("sb_secret_"):
        raise GuideError("Supabase에는 publishable/anon 키를 사용하세요. 관리자 키는 허용하지 않습니다.")
    try:
        part = key.split(".")[1]
        payload = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (ValueError, IndexError):
        return
    if isinstance(payload, dict) and payload.get("role") == "service_role":
        raise GuideError("Supabase service_role 키는 허용하지 않습니다. publishable/anon 키를 사용하세요.")


def streamlit_secrets():
    """Cloud의 앱 전용 GUIDE_* 값만 읽습니다. CLI 실행에는 Streamlit이 필요 없습니다."""
    from streamlit.runtime.scriptrunner import get_script_run_ctx
    if get_script_run_ctx(suppress_warning=True) is None:
        return {}
    import streamlit as st
    try:
        return {key: value for key, value in st.secrets.to_dict().items() if key.startswith('GUIDE_')}
    except FileNotFoundError:
        return {}
    except Exception:
        raise GuideError('Streamlit Secrets의 TOML 설정 형식을 확인해 주세요. (CONFIG)') from None


def load_settings(*, use_streamlit=True):
    # mvp/.env만 읽습니다. 기존 루트 .env의 OpenAI 키는 사용하지 않습니다.
    secrets = streamlit_secrets() if use_streamlit else {}
    values = {**dotenv_values(ROOT / "mvp" / ".env"), **secrets, **os.environ}
    def get(key, default=""):
        return str(values.get("GUIDE_" + key, default) or "").strip()
    try:
        settings = Settings(
            mode=get("MODE", "staff"), supabase_url=get("SUPABASE_URL"),
            supabase_key=get("SUPABASE_PUBLISHABLE_KEY"), llm_provider=get("LLM_PROVIDER", "disabled"),
            llm_url=get("LLM_BASE_URL"), llm_key=get("LLM_API_KEY"), llm_model=get("LLM_MODEL"),
            llm_approved=get("LLM_APPROVED", "false").lower() == "true",
            groq_free_confirmed=get("GROQ_FREE_CONFIRMED", "false").lower() == "true",
            daily_limit=int(get("DAILY_LLM_LIMIT", "40")),
            user_daily_limit=int(get("USER_DAILY_LLM_LIMIT", "10")),
            min_similarity=float(get("MIN_SIMILARITY", "0.38")),
            data_dir=get("DATA_DIR"), storage_backend=get("STORAGE_BACKEND", "local"),
            storage_bucket=get("STORAGE_BUCKET", "guide-originals"),
            ocr_enabled=get("OCR_ENABLED", "false").lower() == "true",
        )
    except ValueError:
        raise GuideError("mvp/.env의 숫자 설정을 확인해 주세요. (CONFIG)") from None
    if settings.mode not in {"local", "staff"} or not 0 <= settings.min_similarity <= 1:
        raise GuideError("운영 모드 또는 검색 설정이 잘못되었습니다. (CONFIG)")
    if settings.daily_limit < 1 or settings.user_daily_limit < 1:
        raise GuideError("AI 사용 한도는 1 이상이어야 합니다. (CONFIG)")
    reject_secret_key(settings.supabase_key)
    if settings.storage_backend not in {"local", "supabase"}:
        raise GuideError("원본 저장소는 local 또는 supabase를 선택하세요. (CONFIG)")
    if settings.storage_backend == "supabase" and (settings.mode != "staff" or not settings.cloud_ready):
        raise GuideError("Supabase Storage에는 직원 로그인용 Supabase 설정이 필요합니다. (CONFIG)")
    if not settings.storage_bucket or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in settings.storage_bucket):
        raise GuideError("Storage 버킷 이름을 확인해 주세요. (CONFIG)")
    if settings.supabase_url:
        parsed = urlparse(settings.supabase_url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
            raise GuideError("Supabase에는 https://로 시작하는 프로젝트 기본 URL만 입력하세요. (CONFIG)")
    return settings
