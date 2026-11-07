import pytest

from brain import strategy
from brain.locations import LocationAdvisor


@pytest.fixture(autouse=True)
def isolated_advisor(tmp_path):
    """测试中遇到的未知场地写到临时目录，不污染 data/unknown_locations.json。"""
    strategy.set_default_advisor(LocationAdvisor(unknown_path=tmp_path / "unknown_locations.json"))
    yield
    strategy.set_default_advisor(None)
