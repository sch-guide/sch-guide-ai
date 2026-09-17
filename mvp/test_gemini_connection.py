"""Manual smoke test: python -m mvp.test_gemini_connection (no guideline/retrieval)."""
import os

from mvp.gemini_provider import completion_response
from mvp.settings import GuideError, load_settings


def main():
    if not os.environ.get('GEMINI_API_KEY', '').strip():
        print('SKIPPED: GEMINI_API_KEY is unavailable in this process. Run in your configured PowerShell.')
        return 0
    try:
        settings = load_settings(use_streamlit=False)
        if settings.llm_provider != 'gemini':
            print('Set GUIDE_LLM_PROVIDER=gemini before running this test.')
            return 2
        response = completion_response(settings,
            [{'role': 'user', 'content': '연결 테스트입니다. OK라고 답하세요.'}],
            temperature=0, max_output_tokens=768, json_mode=False)
        if response.status_code != 200:
            print(f'Connection test returned HTTP {response.status_code}. Provider details omitted.')
            return 1
        choice = response.json()['choices'][0]
        if choice['finish_reason'] == 'stop' and choice['message']['content'].strip() == 'OK':
            print('OK')
            return 0
        print('Response received, but it was not a complete OK response. Content omitted.')
        return 1
    except GuideError as exc:
        print(str(exc))  # Only fixed, sanitized application errors.
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
