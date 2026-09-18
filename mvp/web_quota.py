"""Web-only local usage scope; staff retains ai.generate's default quota."""
import logging
from mvp.ai import Quota
from mvp.settings import ROOT


def for_web(settings):
    if settings.mode != 'local':
        logging.getLogger(__name__).info('%s', ROOT / 'data' / 'usage.sqlite3')
        return None
    path = ROOT / 'data' / 'web_local_usage.sqlite3'
    # Refuse aliases to production storage instead of opening/modifying it.
    production = ROOT / 'data' / 'usage.sqlite3'
    if path.is_symlink() or (path.exists() and production.exists() and path.samefile(production)):
        raise ValueError('Local quota must not alias production quota.')
    logging.getLogger(__name__).info('%s', path)
    return Quota(path)
