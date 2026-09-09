"""Shared client plumbing for the Trader and Market-Data clients.

Both clients have to watch two things at once: the terminal and the socket.
The server can push BOUGHT/SOLD/TRADE at any moment, so a client that blocks
in recv() waiting for a reply to its own last command would go deaf, and one
that blocks reading stdin would never see the pushes.  select() over both
descriptors keeps them equal.
"""

import errno
import os
import select
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from protocol import LineReader


def connect(host, port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((host, port))
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    local = sock.getsockname()
    print("connected %s:%d -> %s:%d" % (local[0], local[1], host, port))
    return sock


def send_line(sock, text):
    data = (text + "\n").encode("ascii", "replace")
    sent = 0
    while sent < len(data):
        try:
            n = sock.send(data[sent:])
        except OSError as exc:
            if exc.errno == errno.EINTR:
                continue
            raise
        if n == 0:
            raise OSError("connection closed while sending")
        sent += n


def run(sock, banner=None, greeting=None):
    """Pump stdin and the socket until either end goes away."""
    if banner:
        print(banner)

    if greeting:
        for line in greeting:
            send_line(sock, line)
            print(">> %s" % line)

    from_server = LineReader()
    from_stdin = LineReader()
    stdin_fd = sys.stdin.fileno()
    watch = [sock, stdin_fd]

    while True:
        try:
            ready, _, _ = select.select(watch, [], [])
        except (OSError, InterruptedError) as exc:
            if getattr(exc, "errno", None) == errno.EINTR:
                continue
            raise

        if sock in ready:
            try:
                chunk = sock.recv(65536)
            except ConnectionResetError:
                print("!! server reset the connection")
                return 1
            except OSError as exc:
                print("!! recv failed: %s" % exc)
                return 1

            if not chunk:
                print("!! server closed the connection")
                return 0

            for line in from_server.feed(chunk):
                print("<< %s" % line)

        if stdin_fd in ready:
            chunk = os.read(stdin_fd, 4096)
            if not chunk:
                # Terminal closed: say goodbye properly instead of vanishing.
                try:
                    send_line(sock, "QUIT")
                    sock.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                watch = [sock]
                continue

            for line in from_stdin.feed(chunk):
                line = line.strip()
                if not line:
                    continue
                try:
                    send_line(sock, line)
                except OSError as exc:
                    print("!! send failed: %s" % exc)
                    return 1
                print(">> %s" % line)
                if line.upper() == "QUIT":
                    try:
                        sock.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass
                    watch = [sock]
