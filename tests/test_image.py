import pytest

from ms43emu import Image
from ms43emu.image import FULL_SIZE


def test_identification(stock):
    ids = stock.ids()
    assert ids["program"] == "430069" and ids["calibration"] == "430069"
    assert ids["hardware"] == "6577355402" and ids["siemens"] == "5WK90027"
    assert ids["program_part"] == "6577355117"
    assert ids["boot"] == ("11143053",) * 3
    assert ids["program_triplet"] == ("111430536901",) * 3
    assert ids["calibration_triplet"] == ("1114300069010000",) * 3
    assert ids["calibration_name"] == "ca430069.DAT"
    assert stock.end_marks() == (True, True)


def test_reset_vector_and_mirror(stock):
    assert stock.reset_target() == 0x002D06
    assert stock.read(0x0E366A, 4) == stock.read(0x06366A, 4)      # the program entry, through either half
    with pytest.raises(ValueError):
        stock.read(0x100000, 2)


def test_rejects_what_is_not_a_read():
    with pytest.raises(ValueError, match="full read"):
        Image(bytes(FULL_SIZE // 2), "half")
    with pytest.raises(ValueError, match="no JMPS"):
        Image(b"\xff" * FULL_SIZE, "blank")
    Image(b"\xff" * FULL_SIZE, "blank", check=False)
