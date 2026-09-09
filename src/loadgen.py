#!/usr/bin/env python3
"""Connection generator for the scalability bonus.

Opens a requested number of TCP connections to the Exchange Server, sends no
application data at all, and then holds them open until it is killed.

One source address only gives us about 55k usable ephemeral ports on FreeBSD,
which is not enough for 70k connections to a single destination.  A TCP
connection is identified by the four-tuple, so the extra connections come from
extra source addresses:

    ifconfig lo0 alias 127.0.0.2/32
    ifconfig lo0 alias 127.0.0.3/32

and then --sources 127.0.0.1,127.0.0.2,127.0.0.3.

    loadgen.py <host> <port> <count> [--sources a,b,c] [--report N]
"""

import os
import resource
import socket
import struct
import sys
import time


def raise_fd_limit():
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft < hard:
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))
            soft = hard
        except (ValueError, OSError):
            pass
    return soft


def main(argv):
    positional = [a for a in argv[1:] if not a.startswith("--")]
    if len(positional) != 3:
        sys.stderr.write(__doc__)
        return 2

    host = positional[0]
    port = int(positional[1])
    count = int(positional[2])

    sources = ["0.0.0.0"]
    report_every = 5000
    # --reset closes with RST at the end so a measurement sweep is not left
    # waiting on tens of thousands of TIME_WAIT four-tuples before the next
    # step can reuse the ports.
    reset = "--reset" in argv
    for arg in argv[1:]:
        if arg.startswith("--sources="):
            sources = arg.split("=", 1)[1].split(",")
        elif arg.startswith("--report="):
            report_every = int(arg.split("=", 1)[1])

    limit = raise_fd_limit()
    print("pid %d, fd limit %d, target %d connections via %d source address(es)"
          % (os.getpid(), limit, count, len(sources)))
    if limit < count + 64:
        print("warning: fd limit is below the target; raise kern.maxfilesperproc")

    socks = []
    started = time.time()
    failure = None

    try:
        for i in range(count):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                src = sources[i % len(sources)]
                if src != "0.0.0.0":
                    s.bind((src, 0))
                s.connect((host, port))
            except OSError as exc:
                s.close()
                failure = (i, exc)
                break

            socks.append(s)

            if report_every and len(socks) % report_every == 0:
                print("%6d connections up after %.1fs"
                      % (len(socks), time.time() - started))
                sys.stdout.flush()

    except KeyboardInterrupt:
        pass

    if failure:
        i, exc = failure
        print("stopped at connection %d: %s" % (i + 1, exc))

    print("established %d connections in %.1fs"
          % (len(socks), time.time() - started))
    print("holding them open, Ctrl-C to release")
    sys.stdout.flush()

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\nclosing %d connections" % len(socks))

    for s in socks:
        try:
            if reset:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                             struct.pack("ii", 1, 0))
            s.close()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
