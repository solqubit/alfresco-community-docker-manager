"""Curses smoke test: language picker, dashboard and exit in a pseudo-terminal.

Run inside the Linux test runner:  python3 tests/pty_smoke.py [state-dir] [keys...]
Keys are sent one by one; «ESC», «ENTER», «DOWN», «UP» are named keys.
"""
import fcntl
import os
import pty
import re
import select
import struct
import sys
import termios
import time

KEYS = {'ESC': '\x1b', 'ENTER': '\r', 'DOWN': '\x1b[B', 'UP': '\x1b[A', 'RIGHT': '\x1b[C'}


def screen_text(raw):
    text = re.sub(r'\x1b\[[0-9;?]*[A-Za-z]', ' ', raw.decode(errors='replace'))
    return re.sub(r'[ \t]+', ' ', text)


def read(fd, seconds):
    output = b''
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        ready, _, _ = select.select([fd], [], [], 0.2)
        if ready:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            output += chunk
    return output


def main():
    state = sys.argv[1] if len(sys.argv) > 1 else '/root/acdm-pty'
    keys = sys.argv[2:] or ['ENTER', 'ESC', 'ESC']
    pid, fd = pty.fork()
    if pid == 0:
        os.environ.update(TERM='xterm-256color', PYTHONDONTWRITEBYTECODE='1')
        os.execvp('bash', ['bash', '/workspace/start.sh', '--state-dir', state])
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', 30, 100, 0, 0))
    transcript = read(fd, 20)
    for key in keys:
        os.write(fd, KEYS.get(key, key).encode())
        transcript += read(fd, 8)
    _, status = os.waitpid(pid, 0)
    text = screen_text(transcript)
    print(text[-3000:])
    for needle in ('Idioma / Language', 'Alfresco Community Docker Manager'):
        assert needle in text, needle
    assert 'Traceback' not in text
    print('\nexit status:', os.waitstatus_to_exitcode(status))
    return os.waitstatus_to_exitcode(status)


if __name__ == '__main__':
    raise SystemExit(main())
