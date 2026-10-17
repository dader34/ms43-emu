#!/usr/bin/env python
"""The emulated MS43 on a K line, for a tester.

    ../gs20-emu/.pypy/bin/pypy3 tools/kline.py [--image stock] [--ws 8767] [--trace]

This prints a pseudo-terminal to point a tester on this machine at.
--ws PORT adds a WebSocket on localhost that speaks BMWeb's gateway
protocol: BMWeb takes it as its cable with ?gateway=ws://localhost:PORT
on the page's URL (https://bmweb.danner.ink/?gateway=ws://localhost:8767;
8767 is where BMWeb's desktop app puts a DME). For any other Web Serial
page the same port serves tools/webserial-emu.js, which hands the page the
emulated module as its serial port.

Either way this plays the K line: bytes the tester writes are echoed back
(a K-line cable hears its own transmission) and reach the module's serial
port; bytes the module transmits go out to the tester. The DME speaks DS2
at address 0x12, 9600 baud 8E1, through its own code, with the monitoring
processor answering beside it (ms43emu/monitor.py); what is modelled is in
docs/status.md. Programming (seed/key, erase, write) works as far as
tests/test_flash.py takes it; a whole WinKFP run has not been checked.

--image is `stock`, `patched`, or a 512 KB file. --save FILE writes the
flash there on exit when a tester has changed it.

The module has power to begin with. `p` and Enter here switches it, and so
do http://localhost:PORT/power/off, /on and /toggle on the --ws port
(/power shows it). Off, nothing answers; on again the module starts from
its reset vector with its RAM cleared and its flash as it was left.

The module keeps real time: a tester's timeouts are in real milliseconds
and so are the module's. For that the emulated CPU is clocked at six
million instructions a second (--mips; 12 is the chip at its best and
twice the work; the firmware's tasks run at the same rates at either),
which PyPy does with room to spare and CPython does not: run this with
pypy3 (gs20-emu's .pypy has c166-emu installed). When the machine is too
busy to keep up, the module falls behind and this says so, once, with how
fast it is running.
"""
import argparse
import base64
import hashlib
import json
import os
import select
import socket
import sys
import termios
import time
import tty

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ms43emu import load_file, eeprom        # noqa: E402
from ms43emu.board import Board               # noqa: E402
from ms43emu.sensors import Sensors           # noqa: E402
from ms43emu.throttle import Throttle         # noqa: E402

SLICE_SECONDS = 0.0025   # module time between looks at the line
PTY_ACTIVE_SECONDS = 120
BAUD_RATES = (9600, 10400, 19200, 38400, 50000, 100000, 125000)


class Pty:
    """A pseudo-terminal for a tester on this machine."""

    def __init__(self, link=None):
        self.fd, slave = os.openpty()
        tty.setraw(self.fd)
        os.set_blocking(self.fd, False)           # nobody may be reading the other end
        self.heard = 0.0                          # when the tester last sent something
        attrs = termios.tcgetattr(slave)
        attrs[3] &= ~termios.ECHO                 # the module's side does the echo, not the tty
        termios.tcsetattr(slave, termios.TCSANOW, attrs)
        self.name = os.ttyname(slave)
        self.link = link
        if link:
            try:
                os.unlink(link)
            except FileNotFoundError:
                pass
            os.symlink(self.name, link)

    def fileno(self):
        return self.fd

    def read(self):
        try:
            data = os.read(self.fd, 4096)
        except BlockingIOError:
            return b""
        if data:
            self.heard = time.time()
        return data

    def active(self):
        """A pty cannot tell whether anyone has it open. Bytes written to
        one nobody reads pile up (and would greet the next tester), so the
        module is only passed on while a tester has been talking."""
        return time.time() - self.heard < PTY_ACTIVE_SECONDS

    def write(self, data):
        try:
            os.write(self.fd, bytes(data))
        except BlockingIOError:
            pass                                  # its buffer is full: nobody is reading

    def set_baud(self, baud):
        pass

    def close(self):
        if self.link:
            try:
                os.unlink(self.link)
            except FileNotFoundError:
                pass


class WebSocket:
    """A WebSocket on localhost for a browser tester. One client at a time.

    Binary frames are the bytes on the line. Text frames are control, in
    either of two forms: BMWeb's gateway calls, {id, op: open | close |
    setSignals | getSignals, ...}, each answered {id, ok}, or the plain
    {baudRate, parity} webserial-emu.js sends (the same port serves that
    file over plain HTTP). Either way the client says which baud rate it
    has its port set to; `baud` is that.

    The port also takes the power switch over plain HTTP: /power/on, /off
    and /toggle are left in `commands` for the bridge, which keeps `power`
    at what the switch is."""

    GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
    SHIM = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webserial-emu.js")

    def __init__(self, port):
        self.name = f"ws://localhost:{port}"
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", port))
        self.server.listen(4)
        self.client = None
        self.buf = b""
        self.baud = None
        self.power = True
        self.commands = []                # power on (True) / off (False), as asked for over HTTP

    def fileno(self):
        return (self.client or self.server).fileno()

    def service(self):
        """While a tester is connected nothing else watches the listening
        socket: take what has come in on it (the power switch, or the
        tester again after a page reload, which then replaces the old one)."""
        if self.client is not None and select.select([self.server], [], [], 0)[0]:
            self._accept()

    def _power_page(self, path):
        want = {"on": True, "off": False, "toggle": not self.power}.get(path.rstrip("/").rpartition("/")[2])
        if want is not None:
            self.commands.append(want)
        state = "on" if (self.power if want is None else want) else "off"
        return (f"<!doctype html><title>ms43-emu power</title><p>Power is <b>{state}</b>.</p>"
                f'<p><a href="/power/on">on</a> | <a href="/power/off">off</a></p>\n').encode()

    def _accept(self):
        conn, _ = self.server.accept()
        conn.settimeout(0.3 if self.client is not None else 2)     # the module stands still meanwhile
        try:
            request = b""
            while b"\r\n\r\n" not in request and len(request) < 8192:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                request += chunk
            head, _, rest = request.partition(b"\r\n\r\n")
            lines = head.decode("latin-1").split("\r\n")
            headers = {k.strip().lower(): v.strip() for k, _, v in (line.partition(":") for line in lines[1:])}
            if headers.get("upgrade", "").lower() == "websocket":
                accept = base64.b64encode(hashlib.sha1(headers["sec-websocket-key"].encode() + self.GUID).digest())
                conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                             b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n")
                conn.settimeout(None)
                if self.client is not None:
                    self._drop()
                self.client, self.buf, self.baud = conn, rest, None
                print(f"{self.name}: connected", flush=True)
                self._send(0x1, json.dumps({"event": "hello", "port": "ms43-emu"}).encode())
                return
            path = (lines[0].split(" ") + ["/"])[1]
            if path.startswith("/power"):
                body, kind = self._power_page(path), b"text/html"
            elif "webserial-emu.js" in path:
                body, kind = open(self.SHIM, "rb").read(), b"text/javascript"
            else:
                body, kind = b"ms43-emu K line; see /webserial-emu.js and /power\n", b"text/plain"
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: " + kind + b"; charset=utf-8\r\nAccess-Control-Allow-Origin: *\r\n"
                         b"Cache-Control: no-store\r\nContent-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
        except (OSError, KeyError):
            pass
        conn.close()

    def _drop(self):
        print(f"{self.name}: disconnected", flush=True)
        self.client.close()
        self.client = None

    def _send(self, opcode, payload):
        n = len(payload)
        head = bytes([0x80 | opcode]) + (bytes([n]) if n < 126 else b"\x7e" + n.to_bytes(2, "big") if n < 65536
                                         else b"\x7f" + n.to_bytes(8, "big"))
        try:
            self.client.sendall(head + bytes(payload))
        except OSError:
            self._drop()

    def read(self):
        if self.client is None:
            self._accept()
            if self.client is None or not self.buf:
                return b""
        else:
            try:
                data = self.client.recv(65536)
            except OSError:
                data = b""
            if not data:
                self._drop()
                return b""
            self.buf += data
        out = b""
        while self.client is not None and len(self.buf) >= 2:
            opcode, n, at = self.buf[0] & 0x0F, self.buf[1] & 0x7F, 2
            if n == 126:
                n, at = int.from_bytes(self.buf[2:4], "big"), 4
            elif n == 127:
                n, at = int.from_bytes(self.buf[2:10], "big"), 10
            masked = bool(self.buf[1] & 0x80)
            if len(self.buf) < at + 4 * masked + n:
                break
            mask = self.buf[at:at + 4] if masked else b"\0\0\0\0"
            at += 4 * masked
            payload = bytes(x ^ mask[i & 3] for i, x in enumerate(self.buf[at:at + n]))
            self.buf = self.buf[at + n:]
            if opcode == 0x2:
                out += payload
            elif opcode == 0x1:
                self._control(payload)
            elif opcode == 0x9:
                self._send(0xA, payload)
            elif opcode == 0x8:
                self._drop()
        return out

    def _control(self, payload):
        try:
            msg = json.loads(payload)
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        if "op" not in msg:                            # webserial-emu.js: the port's settings or its lines
            self.baud = msg.get("baudRate", self.baud)
            return
        reply = {"id": msg.get("id"), "ok": True}
        op = msg["op"]
        if op == "open":
            self.baud = (msg.get("config") or {}).get("baudRate", self.baud)
        elif op == "close":
            self.baud = None
        elif op == "getSignals":
            # the cable reports the ignition (terminal 15) on DSR/DCD: here, whether the module has power
            reply["signals"] = {"dataSetReady": self.power, "dataCarrierDetect": self.power, "clearToSend": False}
        elif op != "setSignals":
            reply = {"id": msg.get("id"), "ok": False, "error": f"unknown call {op}"}
        self._send(0x1, json.dumps(reply).encode())

    def write(self, data):
        if self.client is not None:
            self._send(0x2, data)

    def set_baud(self, baud):
        pass

    def close(self):
        if self.client is not None:
            self.client.close()
        self.server.close()


class Trace:
    """Prints the line's traffic, a run of bytes in one direction per line,
    with the real time and the module's time in seconds: a tester that sends
    before the module's time has caught up with the wall clock shows here."""

    IDLE = 0.03                    # a pause this long ends a run

    def __init__(self):
        self.who = None
        self.data = bytearray()
        self.started = self.last = 0.0
        self.zero = time.time()

    def add(self, who, data, module_time):
        now = time.time() - self.zero
        if self.data and (who != self.who or now - self.last > self.IDLE):
            self.flush()
        if not self.data:
            self.who, self.started, self.module_time = who, now, module_time
        self.data += data
        self.last = now

    def flush(self):
        if self.data:
            print(f"{self.started:9.3f} (dme {self.module_time:9.3f})  {self.who:>6}> {self.data.hex()}", flush=True)
            self.data.clear()

    def idle(self):
        if self.data and time.time() - self.zero - self.last > self.IDLE:
            self.flush()


def listening(line):
    """Whether there is a tester on the line to pass the module's bytes to."""
    active = getattr(line, "active", None)
    return active() if active else getattr(line, "client", True) is not None


def wrong_rate(line, baud):
    """A line that knows its tester's baud rate, set to another than `baud`."""
    own = getattr(line, "baud", None)
    return own is not None and min(BAUD_RATES, key=lambda r: abs(r - own)) != baud


def module_baud(board):
    """The standard rate nearest to what the module's serial port is set to."""
    rate = board.kline.baud()
    return min(BAUD_RATES, key=lambda r: abs(r - rate))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", default="stock", help="stock, patched, or a 512 KB file")
    ap.add_argument("--ws", type=int, default=None, metavar="PORT",
                    help="also serve the line on a WebSocket (BMWeb: ?gateway=ws://localhost:PORT)")
    ap.add_argument("--link", default=None, help="also create this symlink to the pty (a stable name)")
    ap.add_argument("--attached", action="store_true",
                    help="quit when standard input closes (for a program that starts this one and may die)")
    ap.add_argument("--save", default=None, metavar="FILE", help="write the flash here on exit when it has changed")
    ap.add_argument("--trace", action="store_true", help="print every telegram")
    ap.add_argument("--mips", type=int, default=6, choices=[6, 12],
                    help="million instructions per second of the module's time (default 6)")
    ap.add_argument("--bare", action="store_true",
                    help="the board alone: no sensors, throttle or learnt EEPROM (the tester then sees their faults)")
    args = ap.parse_args()

    lines = [Pty(args.link)]
    if args.ws:
        lines.append(WebSocket(args.ws))
    print("K line on " + ", ".join(line.name for line in lines)
          + (f" (also {args.link})" if args.link else ""), flush=True)

    image = load_file(args.image)
    board = Board(image, ips=args.mips * 1_000_000)
    if not args.bare:
        # a car around the module: its sensors at rest, the throttle it drives, a learnt EEPROM
        Throttle(board, Sensors(board))
        board.eeprom.words = eeprom.learnt(image, 0x68, 0x39C, 0x98, 0x36C)
    cpu = board.cpu
    IPS = board.ips
    slice_instructions = int(SLICE_SECONDS * IPS)
    # What the module sends, each byte with the rate it went out at: it
    # answers a change of rate at the old one and switches right after.
    out = []
    board.kline.on_tx = lambda byte: out.append((byte, module_baud(board)))
    cpu.reset()
    print(f"DME running on {image.name}, program {image.program_ref()} ({sys.implementation.name}); q to quit, p for the power", flush=True)

    trace = Trace() if args.trace else None
    stdin_open = True
    powered = True
    resets = faults = 0
    epoch = (time.time(), cpu.instructions)        # real time and the module's, taken together
    window = epoch                                 # the last few seconds, to say how fast the module is running
    slow = False
    warm_until = time.time() + 5                   # the interpreter is slow at first: not worth a line

    def set_power(on):
        nonlocal powered, epoch, window
        if on == powered:
            return
        powered = on
        for line in lines:
            if hasattr(line, "power"):
                line.power = on
        if on:
            board.power_cycle()
            epoch = window = (time.time(), cpu.instructions)
            print("power on: the module starts from its reset vector", flush=True)
        else:
            out.clear()
            print("power off", flush=True)

    try:
        while True:
            # Keep the module on the wall clock: wait when it is ahead, and
            # when it has fallen behind start counting anew rather than
            # race to catch up.
            now = time.time()
            ahead = (cpu.instructions - epoch[1]) / IPS - (now - epoch[0])
            if ahead < -0.2:
                epoch = (now, cpu.instructions)
            if now - window[0] >= 3:
                rate = (cpu.instructions - window[1]) / IPS / (now - window[0])
                window = (now, cpu.instructions)
                if powered and now > warm_until and (rate < 0.9) != slow:
                    slow = rate < 0.9
                    print(f"the module is running at {rate:.0%} of real time: the machine is too busy to keep up ({sys.implementation.name})"
                          if slow else "the module is keeping real time again", flush=True)
            wait = max(0.0, min(ahead, 0.05)) if powered else 0.05
            r, _, _ = select.select(lines + ([sys.stdin] if stdin_open else []), [], [], wait)
            for line in lines:
                if line in r:
                    data = line.read()
                    if data:
                        line.write(data)                      # the cable's echo
                        if not powered or wrong_rate(line, module_baud(board)):
                            continue                          # nobody there, or the module hears noise, not bytes
                        board.kline.receive(data)
                        if trace:
                            trace.add("tester", data, board.seconds)
            if sys.stdin in r:
                typed = sys.stdin.readline()
                if typed.strip() == "q":
                    break
                if typed.strip() == "p":
                    set_power(not powered)
                elif typed.strip() in ("power on", "power off"):             # for a program that drives this one
                    set_power(typed.strip().endswith("on"))
                if not typed and args.attached:
                    break                                     # whoever started this is gone
                stdin_open = bool(typed)                      # EOF: running detached
            for line in lines:
                if hasattr(line, "service"):
                    line.service()
                    while line.commands:
                        set_power(line.commands.pop(0))
            if not powered:
                if trace:
                    trace.idle()
                continue
            board.run(slice_instructions)
            for line in lines:
                line.set_baud(module_baud(board))
            if out:
                sending = bytes(byte for byte, _ in out)
                for line in lines:
                    if listening(line):
                        heard = bytes(byte for byte, baud in out if not wrong_rate(line, baud))
                        if heard:
                            line.write(heard)
                if trace:
                    trace.add("dme", sending, board.seconds)
                out.clear()
            if cpu.resets != resets or len(cpu.faults) != faults:
                for at, flag, what in cpu.faults[faults:faults + 3]:
                    print(f"fault: {what}", flush=True)
                if cpu.resets != resets:
                    print(f"the module reset itself ({cpu.resets} software resets so far)", flush=True)
                resets, faults = cpu.resets, len(cpu.faults)
            if trace:
                trace.idle()
    except KeyboardInterrupt:
        pass
    finally:
        if args.save and bytes(cpu.flash) != image.data:
            with open(args.save, "wb") as f:
                f.write(bytes(cpu.flash))
            print(f"flash changed: saved as {args.save}", flush=True)
        for line in lines:
            line.close()


if __name__ == "__main__":
    main()
