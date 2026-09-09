"""관리자용 일관된 로컬 백업. 키와 질문·답변을 새로 수집하지 않습니다."""

import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from mvp.settings import GuideError


def create_backup(repository):
    repository.auth.require_admin()
    root = repository.settings.library_dir.resolve()
    if repository.settings.storage_backend != 'local':
        raise GuideError('이 백업 도구는 서버 원본 폴더용입니다. Supabase Storage는 별도 백업이 필요합니다.')
    destination = root.parent / 'backups' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:6])
    with repository.lock:
        repository.auth.require_admin()
        destination.mkdir(parents=True)
        try:
            for name in ('catalog.sqlite3', 'accounts.sqlite3'):
                source = root / name
                if source.exists():
                    # 실행 중인 SQLite의 WAL까지 반영하는 backup API를 사용합니다.
                    with sqlite3.connect(source) as src, sqlite3.connect(destination / name) as dst:
                        src.backup(dst)
                        if dst.execute('pragma integrity_check').fetchone()[0] != 'ok':
                            raise GuideError('백업 무결성 검사에 실패했습니다.')
                        if name == 'accounts.sqlite3':
                            dst.execute('delete from sessions')
            originals = root / 'originals'
            if originals.is_dir():
                if originals.is_symlink() or originals.is_junction() or any(
                    p.is_symlink() or p.is_junction() for p in originals.rglob('*')):
                    raise GuideError('원본 저장소의 연결 경로는 백업할 수 없습니다.')
                shutil.copytree(originals, destination / 'originals')
            manifest = {str(p.relative_to(destination)): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in destination.rglob('*') if p.is_file()}
            (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
            repository.auth.require_admin()
        except Exception:
            # 실패한 백업을 정상 복구본으로 사용하지 않도록 표시합니다. 자동 삭제하지 않습니다.
            (destination / 'INCOMPLETE').touch()
            raise GuideError('백업을 완료하지 못했습니다. 서버 저장 공간과 권한을 확인하세요. (BACKUP)') from None
    return destination


def verify_backup(directory):
    directory = Path(directory)
    if (directory / 'INCOMPLETE').exists():
        return False
    try:
        manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
        for name, digest in manifest.items():
            target = (directory / name).resolve()
            if not target.is_relative_to(directory.resolve()) or not target.is_file():
                return False
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                return False
        return bool(manifest)
    except (OSError, ValueError, TypeError):
        return False
