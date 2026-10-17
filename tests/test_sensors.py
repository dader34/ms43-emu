"""The analogue sensors, the EEPROM and the electronic throttle, as the
program sees them (docs/status.md "The sensors" and "The throttle"):

- the A/D inputs read back as the values the sensor model was given;
- the EEPROM answers bit-exact through the program's own read job;
- with a learnt throttle in the EEPROM and the monitor agreeing on the
  pedal, the program runs its throttle controller, learns the closed stop
  at key-on and makes the plate follow its setpoint."""
import pytest

from ms43emu import find_image, load_image, live, eeprom
from ms43emu.board import Board, IPS
from ms43emu.sensors import Sensors
from ms43emu.throttle import Throttle

E62E_WORD = 0xE62E              # the EEPROM read job's last word
E630_COMMAND = 0xE630           # its command: 0x1800 | address
FD0C = 0xFD0C                   # bit 9: throttle controller off (0x0922A0)
E176_STOP = 0xE176              # sensor 1's count at the closed stop, learnt at key-on
E14E_SETPOINT = 0xE14E          # the throttle setpoint, 0.0073 degrees a count


def image():
    if find_image("stock") is None:
        pytest.skip("no stock image")
    return load_image("stock")


@pytest.fixture(scope="module")
def idling():
    """Key on with a learnt EEPROM, 1 s; then the engine at 800 rpm with
    20 kg/h of air, 0.5 s."""
    img = image()
    b = Board(img)
    s = Sensors(b)
    t = Throttle(b, s)
    s.coolant_c, s.intake_c = 20.0, 20.0
    b.eeprom.words = eeprom.learnt(img, 0x68, 0x39C, 0x98, 0x36C)
    b.cpu.reset()
    b.run(IPS)
    stop = b.cpu.rw(E176_STOP)
    b.engine.start(800)
    s.maf_kgh = 20.0
    b.run(int(0.5 * IPS))
    return b, s, t, stop


def test_the_eeprom_reads_bit_exact():
    img = image()
    b = Board(img)
    Sensors(b)
    b.eeprom.words = [(i * 0x0101 + 0x1234) & 0xFFFF for i in range(eeprom.WORDS)]
    b.cpu.reset()
    b.run(int(0.1 * IPS))
    address = b.cpu.rw(E630_COMMAND) & 0x3FF
    assert b.cpu.rw(E630_COMMAND) & 0xFC00 == 0x1800
    assert b.cpu.rw(E62E_WORD) == b.eeprom.words[address]


def test_the_header_and_throttle_records_check():
    img = image()
    words = eeprom.learnt(img, 0x68, 0x39C, 0x98, 0x36C)
    assert words[eeprom.THROTTLE_ADAPTATION + 4] == eeprom.VALID
    rec = words[eeprom.THROTTLE_ADAPTATION:eeprom.THROTTLE_ADAPTATION + 6]
    assert rec == eeprom.throttle_adaptation(img, 0x68, 0x39C, 0x98, 0x36C)


def test_the_sensors_read_back(idling):
    b, s, t, _ = idling
    v = live.read(b)
    assert abs(v["coolant_c"] - 20) <= 1.5
    assert abs(v["intake_air_c"] - 20) <= 1.5
    assert abs(v["maf_kgh"] - 20) <= 1
    assert abs(v["throttle_deg"] - t.deg) <= 0.3


def test_the_throttle_controller_runs_and_learnt_the_stop(idling):
    b, _, _, stop = idling
    assert not b.cpu.rw(FD0C) & 0x200
    assert stop == 0x68


def test_the_pedal_opens_the_throttle_and_it_follows(idling):
    b, s, t, _ = idling
    s.pedal = 0.5
    b.run(int(0.6 * IPS))
    setpoint = b.cpu.rw(E14E_SETPOINT) * 0.0073
    assert setpoint > 30
    assert abs(t.deg - setpoint) <= 2
    s.pedal = 0.0
    b.run(int(0.6 * IPS))
    assert t.deg < 3
