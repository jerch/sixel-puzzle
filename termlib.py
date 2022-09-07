from contextlib import contextmanager
import sys
import os
from select import select
from fcntl import ioctl
from termios import tcgetattr, tcsetattr, TCSADRAIN, TIOCGWINSZ
from tty import setcbreak
from struct import pack, unpack
from typing import Optional, Tuple, Union


class TerminalContext:
    def __init__(self, fd: int, close_fd=False) -> None:
        if not os.isatty(fd):
            raise TypeError('fd is not a terminal')
        self.close_fd = close_fd
        self.fd = fd
        self.is_cbreak = False
        self._initial_attr = self.termios_attributes

    @classmethod
    def from_cterm(cls):
        fd = os.open(os.ctermid(), os.O_RDWR)
        return TerminalContext(fd, True)

    def close(self) -> None:
        tcsetattr(self.fd, TCSADRAIN, self._initial_attr)
        if self.close_fd:
            os.close(self.fd)

    @property
    def termios_attributes(self) -> list:
        return tcgetattr(self.fd)
    
    @property
    def ttyname(self) -> str:
        return os.ttyname(self.fd)
    
    @contextmanager
    def cbreak_mode(self):
        """
        Enter cbreak mode context.
        """
        if self.is_cbreak:
            yield
            return
        tattr = self.termios_attributes
        try:
            setcbreak(self.fd, TCSADRAIN)
            self.is_cbreak = True
            yield
        finally:
            tcsetattr(self.fd, TCSADRAIN, tattr)
            self.is_cbreak = False

    @contextmanager
    def custom_state(self, undo=None):
        """
        Enter custom terminal state, that needs to to be undone by ``undo``.
        Useful, if you want to apply a custom terminal state and
        have to make sure, that it gets properly reset to previous state.
        """
        try:
            yield
        finally:
            if undo:
                undo()

    def query(self, s: Union[str, bytes], timeout: Optional[float] = 0.05) -> bytes:
        """
        Query terminal report. ``s`` should be a terminal function,
        that generates some sort of a response from the terminal.
        Returns the report as bytes. If the terminal does not response
        within ``timeout``, empty bytes are returned.
        """
        with self.cbreak_mode():
            os.write(self.fd, s if isinstance(s, bytes) else s.encode('utf-8'))
            can_read, _, _ = select([self.fd], [], [], timeout)
            return os.read(self.fd, 1024) if can_read else b''

    def get_size(self) -> Tuple[int, int, int, int]:
        """
        Return terminal size as (cols, rows, xpixel, ypixel).
        Reports 0 for values that cannot be retrieved.
        The method first tries ioctl with TIOCGWINSZ and fills
        missing values from CSI 14 t and CSI 18 t query attempts.
        """
        cols, rows, xpixel, ypixel = [0, 0, 0, 0]
        try:
            packed = ioctl(self.fd, TIOCGWINSZ, pack('HHHH', 0, 0, 0, 0))
            rows, cols, xpixel, ypixel = unpack('HHHH', packed)
        except:
            pass
        if not rows or not cols:
            report = self.query('\x1b[18t')
            if report.startswith(b'\x1b[8;') and report.endswith(b't'):
                try:
                    rows, cols = [int(v) for v in report[4:-1].split(b';')]
                except:
                    pass
        if not xpixel or not ypixel:
            report = self.query('\x1b[14t')
            if report.startswith(b'\x1b[4;') and report.endswith(b't'):
                try:
                    ypixel, xpixel = [int(v) for v in report[4:-1].split(b';')]
                except:
                    pass
        return cols, rows, xpixel, ypixel
    
    def write(self, s: Union[str, bytes]) -> None:
        """
        Write string or bytes directly to the terminal.
        """
        data = s if isinstance(s, bytes) else s.encode('utf-8')
        sent = os.write(self.fd, data)
        while sent:
            data = data[sent:]
            sent = os.write(self.fd, data)
    
    def read(self, amount: int = 1024, timeout: Optional[float] = None) -> bytes:
        """
        Single unbuffered (cbreak mode) blocking read from the terminal.
        If nothing was sent from the terminal within ``timeout``,
        empty bytes are returned.
        """
        with self.cbreak_mode():
            can_read, _, _ = select([self.fd], [], [], timeout)
            return os.read(self.fd, amount) if can_read else b''

    def query_color(self, slot: str) -> Optional[str]:
        """
        Query default color from terminal. ``slot`` can either be 'fg' (foreground color),
        'bg' (background color), or a number in 0-255 (indexed value).
        Returns a hex color string as '#RRGGBB' or None if query failed.
        """
        value = 10 if slot == 'fg' else 11 if slot == 'bg' else f'4;{slot}'
        report = self.query(f'\x1b]{value};?\x1b\\')
        if report.startswith(b'\x1b]') and (report.endswith(b'\x1b\\') or report.endswith(b'\x07')):
            offset = 2 if slot in ('fg', 'bg') else len(f'4;{slot}')
            spec = report[3+offset:].rstrip(b'\x1b\\').rstrip(b'\x07')
            if spec.startswith(b'rgb:'):
                r, g, b = [hex(int(v, 16) >> 8)[2:].zfill(2) for v in spec[4:].split(b'/')]
                return f'#{r}{g}{b}'
        return None


@contextmanager
def terminal_context(fd: int = sys.stdin.fileno()):
    t = TerminalContext(fd)
    try:
        yield t
    finally:
        t.close()


@contextmanager
def cterminal_context():
    t = TerminalContext.from_cterm()
    try:
        yield t
    finally:
        t.close()
