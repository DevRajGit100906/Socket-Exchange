#!/usr/bin/env python3
"""Trader Client.

    trader.py <host> <port> [username]

With a username it logs in straight away; otherwise type LOGIN yourself.
Everything you type is sent as one protocol message.  Lines coming back from
the exchange are printed as they arrive, including the asynchronous BOUGHT and
SOLD notifications that show up long after the matching ORDER_ACCEPTED.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import session

HELP = """Trader Client. Commands:
  LOGIN <username>
  BUY  <JNST|IMCT> <quantity> <price>
  SELL <JNST|IMCT> <quantity> <price>
  CANCEL <order_id>
  QUIT
"""


def main(argv):
    if len(argv) not in (3, 4):
        sys.stderr.write("usage: trader.py <host> <port> [username]\n")
        return 2

    host, port = argv[1], int(argv[2])
    greeting = ["LOGIN %s" % argv[3]] if len(argv) == 4 else []

    try:
        sock = session.connect(host, port)
    except OSError as exc:
        sys.stderr.write("could not connect to %s:%s - %s\n"
                         % (host, port, exc))
        return 1

    try:
        return session.run(sock, banner=HELP, greeting=greeting)
    except KeyboardInterrupt:
        print()
        return 0
    finally:
        sock.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
