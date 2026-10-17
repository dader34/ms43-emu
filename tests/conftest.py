import pytest

from ms43emu import load_image, find_image


@pytest.fixture(scope="session")
def stock():
    if find_image("stock") is None:
        pytest.skip("no stock image (MS43_FULL or images/stock_512k.bin)")
    return load_image("stock")
