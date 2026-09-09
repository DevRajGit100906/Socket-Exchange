#!/usr/bin/env python3
"""Market-Data Client.

    market_data.py <host> <port> [instrument ...]

Subscribes to whatever instruments are named on the command line and then just
listens.  After the subscription there is nothing to poll for: the exchange
pushes a TRADE line whenever a trade touches one of them.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import session

HELP = """Market-Data Client. Commands:
  SUBSCRIBE <JNST|IMCT>
  UNSUBSCRIBE <JNST|IMCT>
  QUIT
"""


def main(argv):
    if len(argv) < 3:
        sys.stderr.write("usage: market_data.py <host> <port> "
                         "[instrument ...]\n")
        return 2

    host, port = argv[1], int(argv[2])
    greeting = ["SUBSCRIBE %s" % name for name in argv[3:]]

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
