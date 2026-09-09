"""UI 테스트도 실제 로컬 실행과 같은 주소로 실행합니다."""

import pytest
from streamlit import config


@pytest.fixture(autouse=True)
def local_test_address():
    previous = config.get_option('server.address')
    config.set_option('server.address', '127.0.0.1')
    yield
    config.set_option('server.address', previous)
