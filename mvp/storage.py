"""원본 저장소. 검색용 DB와 분리하며 모든 원본 작업은 관리자만 가능합니다."""

import re
from pathlib import Path

import httpx

from mvp.documents import MAX_FILE_BYTES
from mvp.settings import GuideError


def valid_key(key):
    # 파일명이나 사용자 입력을 경로로 사용하지 않습니다.
    if not re.fullmatch(r"[0-9a-f-]{36}\.(pdf|docx|xlsx)", key):
        raise GuideError("원본 저장 경로가 잘못되었습니다. (STORAGE_KEY)")
    return key


class LocalSourceStore:
    def __init__(self, root, auth):
        self.root, self.auth = Path(root).resolve(), auth

    def path(self, key):
        candidate = self.root / valid_key(key)
        if candidate.is_symlink() or candidate.resolve().parent != self.root:
            raise GuideError("허용되지 않은 원본 경로입니다. (STORAGE_KEY)")
        return candidate

    def put(self, key, content):
        self.auth.require_admin()
        if len(content) > MAX_FILE_BYTES:
            raise GuideError("원본 파일이 너무 큽니다. (STORAGE_SIZE)")
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with self.path(key).open("xb") as file:
                file.write(content)
        except OSError:
            raise GuideError("원본을 저장하지 못했습니다. 저장소 공간·권한을 확인하세요. (STORAGE)") from None

    def read(self, key):
        self.auth.require_admin()
        try:
            with self.path(key).open("rb") as file:
                content = file.read(MAX_FILE_BYTES + 1)
            if len(content) > MAX_FILE_BYTES:
                raise GuideError("원본 파일 크기가 제한을 넘었습니다. (STORAGE_SIZE)")
            return content
        except OSError:
            raise GuideError("원본 파일을 읽지 못했습니다. 저장소를 확인하세요. (STORAGE)") from None

    def delete(self, key):
        self.auth.require_admin()
        try:
            self.path(key).unlink(missing_ok=True)
        except OSError:
            raise GuideError("검색에서 제외했지만 원본 삭제에 실패했습니다. 삭제를 다시 시도하세요. (STORAGE)") from None


class SupabaseSourceStore:
    def __init__(self, settings, auth):
        self.settings, self.auth = settings, auth

    def request(self, method, key, content=None):
        self.auth.require_admin()
        valid_key(key)
        url = self.settings.supabase_url.rstrip("/") + "/storage/v1/object/"
        headers = {"apikey": self.settings.supabase_key, "Authorization": "Bearer " + self.auth.token}
        if content is not None:
            headers["Content-Type"] = {"pdf": "application/pdf", "docx":
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "xlsx":
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}[key.rsplit(".", 1)[1]]
        if method == "GET":
            url += "authenticated/"
        url += self.settings.storage_bucket
        try:
            if method == "DELETE":
                response = self.auth.client.request(method, url, headers=headers, json={"prefixes": [key]})
            else:
                response = self.auth.client.request(method, url + "/" + key, headers=headers, content=content)
            if response.status_code >= 400:
                raise GuideError("비공개 Storage 버킷·관리자 정책·연결을 확인하세요. (STORAGE)")
            if len(response.content) > MAX_FILE_BYTES:
                raise GuideError("원본 파일 크기가 제한을 넘었습니다. (STORAGE_SIZE)")
            return response.content
        except httpx.HTTPError:
            raise GuideError("원본 저장소에 연결하지 못했습니다. (STORAGE)") from None

    def put(self, key, content):
        if len(content) > MAX_FILE_BYTES:
            raise GuideError("원본 파일이 너무 큽니다. (STORAGE_SIZE)")
        self.request("POST", key, content)

    def read(self, key):
        return self.request("GET", key)

    def delete(self, key):
        self.request("DELETE", key)


def source_store(settings, auth):
    if settings.storage_backend == "supabase":
        return SupabaseSourceStore(settings, auth)
    return LocalSourceStore(settings.library_dir / "originals", auth)
