"""Official Gemini SDK adapter; preserve existing prompt and answer validation."""
import httpx

from mvp.settings import GuideError


def completion_response(settings, messages, *, temperature, max_output_tokens, json_mode=True):
    """Map SDK response to the existing completion envelope, without exposing errors/keys."""
    settings.llm_endpoint()
    try:
        from google import genai
        from google.genai import errors, types
    except ImportError:
        raise GuideError("google-genai 패키지를 설치해 주세요. (AI_SDK)") from None
    try:
        system = [m['content'] for m in messages if m['role'] == 'system']
        contents = [types.Content(role='user' if m['role'] == 'user' else 'model',
                                  parts=[types.Part.from_text(text=m['content'])])
                    for m in messages if m['role'] != 'system']
        config = types.GenerateContentConfig(
            system_instruction='\n'.join(system) if system else None,
            temperature=temperature, max_output_tokens=max_output_tokens,
            response_mime_type='application/json' if json_mode else 'text/plain',
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
        # Explicit API key prevents GOOGLE_API_KEY/Vertex credentials taking precedence.
        # One attempt matches the existing service's no-automatic-retry contract.
        with genai.Client(api_key=settings.llm_key, vertexai=False,
                          http_options=types.HttpOptions(timeout=30000,
                              retry_options=types.HttpRetryOptions(attempts=1))) as client:
            result = client.models.generate_content(model=settings.llm_model, contents=contents, config=config)
        candidate = result.candidates[0] if result.candidates else None
        finish = getattr(candidate, 'finish_reason', None)
        finish = getattr(finish, 'value', finish)
        parts = candidate.content.parts if candidate and candidate.content else []
        text = ''.join(p.text for p in (parts or []) if p.text and not p.thought)
        usage = result.usage_metadata
        return httpx.Response(200, json={
            'choices': [{'finish_reason': 'stop' if finish == 'STOP' and text else 'incomplete',
                         'message': {'content': text}}],
            'usage': {'total_tokens': usage.total_token_count if usage else None}})
    except errors.APIError as exc:
        # Reuse existing quota cancellation and sanitized 401/403/429/5xx handling.
        code = exc.code if isinstance(exc.code, int) and 400 <= exc.code <= 599 else 502
        return httpx.Response(code, json={})
    except httpx.TimeoutException:
        raise GuideError("AI 서버 응답이 30초 안에 완료되지 않았습니다. (AI_TIMEOUT)") from None
    except Exception:
        # SDK exception strings may contain request metadata; never forward them.
        raise GuideError("Gemini 연결 또는 응답 형식을 확인해 주세요. (AI_RESPONSE)") from None
