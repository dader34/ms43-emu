"""The MS43 board around the CPU: the peripherals the firmware has asked
for so far, from c166-emu's periph.py. docs/status.md says what is
modelled and why.

Time is the CPU's instruction count. The CPU clock is 24 MHz: the K line's
baud rate reloads in the firmware are 0x4D (9615 baud, DS2's 9600) and
0x47 (10417, ISO 9141's 10400) at that clock and at no other in reach.
`ips` is how many instructions make a second of the module's time: 12
million takes every instruction as two CPU clocks, the chip at its best,
as gs20-emu does; 6 million (four clocks) is half the work. The firmware
keeps its schedule at either, since its time comes from the timers.
"""
from c166 import sfr
from c166.cpu import NEVER
from c166.periph import Timer, Asc0, Ssc, Can, Adc, Pwm, FlashChip, CapcomTimers, Capin

from .machine import Machine, LATCH
from .monitor import Monitor
from .eeprom import Eeprom
from .engine import Engine

FCPU = 24_000_000                # the CPU clock
IPS = 12_000_000                 # instructions per second of the module's time, unless a board is told otherwise


class Board(Machine):
    def __init__(self, image, echo=True, hardware_traps=True, ips=IPS):
        """hardware_traps: an undefined opcode or an odd word address does
        what it does in the module (the class B trap) and is noted in
        `cpu.faults`. Off, it stops the emulation with CpuError at the
        instruction.

        ips: instructions per second of the module's time, a multiple of 6 million."""
        super().__init__(image)
        cpu = self.cpu
        self.ips = ips
        self.fcpu = FCPU
        cpu.hardware_traps = hardware_traps
        self.unsupported = set()                 # things the firmware configured that are not modelled
        self.resets = 0
        # CPU clocks per count with the prescaler at 0: fCPU/8 for T2-T4, fCPU/4 for T5 and T6
        self.timers = [Timer(self, "T2", 8), Timer(self, "T3", 8), Timer(self, "T4", 8),
                       Timer(self, "T5", 4), Timer(self, "T6", 4, reload=(0x8000, sfr.CAPREL))]
        self.capcom = CapcomTimers(self)
        # the second CAPCOM unit (T7, T8, CC16-CC31): after sync T7 counts
        # T6's underflows, the virtual teeth (docs/status.md "The engine")
        self.capcom2 = CapcomTimers(self, second_unit=True)
        t6 = self.timers[4]
        t6.on_wrap += [self.capcom.t6_wrapped, self.capcom2.t6_wrapped]
        # GPT2's capture input: the crank again (T5 into CAPREL, the CRIC request)
        self.capin = Capin(self, self.timers[3])
        self.kline = Asc0(self, echo=echo)
        self.adc = Adc(self)
        self.pwm = Pwm(self)
        self.spi = Ssc(self)
        self.monitor = Monitor(self)
        self.eeprom = Eeprom(self)            # after the serial port: it shares P3's read
        cpu.hook_ext_write(LATCH[0], self._latch)
        self.spi.device = self._spi
        self.can = Can(self)
        self.flash = FlashChip(self)
        self.engine = Engine(self)            # the crank and cam signals; standing until started
        self.devices = self.timers + [self.capcom, self.capcom2, self.kline, self.adc, self.pwm, self.spi, self.can, self.engine]
        cpu.on_event = self._event
        cpu.on_reset = self._reset
        self.plan()

    def _latch(self, addr, value, size):
        """The external latch (chip selects, multiplexer address) written."""
        self.eeprom.latch(value)

    def _spi(self, word):
        """Who answers on the SPI: the monitor or the EEPROM while selected, nobody (zeros) otherwise."""
        if self.monitor.selected():
            return self.monitor.exchange(word)
        if self.eeprom.selected():
            return self.eeprom.exchange(word, (self.cpu.rw(sfr.SSCCON) & 0xF) + 1)
        return 0

    @property
    def seconds(self):
        """The module's time."""
        return self.cpu.instructions / self.ips

    def plan(self):
        self.cpu.next_event = min(d.next_at for d in self.devices)

    def _event(self):
        now = self.cpu.instructions
        for d in self.devices:
            if now >= d.next_at:
                d.service(now)
        self.plan()

    def _reset(self):
        """What a reset does to the peripherals: timers stop, the serial port forgets what it was sending."""
        self.resets += 1
        for t in self.timers:
            t.running, t.next_at = False, NEVER
        for unit in (self.capcom, self.capcom2):
            unit.reset()
        k = self.kline
        k.shifting = k.waiting = None
        k.tx_at = NEVER
        k._plan()
        self.adc.next_at = self.spi.next_at = NEVER
        self.pwm.running, self.pwm.next_at = [False] * 4, NEVER
        self.can.reset()
        self.flash.state, self.flash.erase_armed = 0, False
        self.flash._array()
        self.plan()

    def power_cycle(self):
        """Off and on again: the RAM is lost, the flash keeps what it holds,
        and the CPU starts from the reset vector."""
        low = self.cpu.low
        low[0xC000:0xEF00] = bytes(0x2F00)
        low[0xF200:0xFE00] = bytes(0xC00)
        self.cpu.ext_ram[0x040000:0x042000] = bytes(0x2000)
        self.cpu.reset()

    # ---- the K line ---------------------------------------------------------------
    def exchange(self, telegram, seconds=0.5):
        """Send bytes and run until the module has answered and the line is quiet. Returns its bytes."""
        k = self.kline
        k.tx.clear()
        k.receive(telegram)
        frame = k.frame()
        end = self.cpu.instructions + int(seconds * self.ips)
        quiet_since = quiet_len = None
        while self.cpu.instructions < end:
            self.cpu.run(frame)
            if k.idle and k.tx:
                if quiet_since is None or len(k.tx) != quiet_len:
                    quiet_since, quiet_len = self.cpu.instructions, len(k.tx)
                elif self.cpu.instructions - quiet_since > 30 * frame:
                    break
            if self.cpu.idle and self.cpu.next_event == NEVER:
                break
        return bytes(k.tx)
