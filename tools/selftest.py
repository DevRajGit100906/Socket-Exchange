#!/usr/bin/env python3
"""Protocol conformance checks for the Exchange Server.

Starts a server on a spare port, drives it with plain sockets, and checks the
replies against the protocol specification.  Run it from the submission root:

    python3 tools/selftest.py
"""

import os
import signal
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOST = "127.0.0.1"
PORT = 5399

passed = 0
failed = []


def check(name, got, want):
    global passed
    if got == want:
        passed += 1
        print("  ok    %s" % name)
    else:
        failed.append(name)
        print("  FAIL  %s\n          got  %r\n          want %r"
              % (name, got, want))


class Client:
    def __init__(self, label):
        self.label = label
        self.sock = socket.socket()
        self.sock.connect((HOST, PORT))
        self.sock.settimeout(0.6)
        self.buf = b""

    def send(self, text):
        self.sock.sendall(text.encode())

    def raw(self, data):
        self.sock.sendall(data)

    def lines(self, wait=0.35):
        time.sleep(wait)
        self.sock.settimeout(0.05)
        while True:
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                break
            except OSError:
                break
            if not chunk:
                break
            self.buf += chunk
        out = self.buf.decode().split("\n")
        self.buf = b""
        return [x for x in out if x]

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def main():
    server = subprocess.Popen(
        [os.path.join(ROOT, "server", "run-server"), HOST, str(PORT),
         "--quiet"],
        start_new_session=True,
        stderr=subprocess.DEVNULL)

    for _ in range(50):
        try:
            probe = socket.create_connection((HOST, PORT), 0.2)
            probe.close()
            break
        except OSError:
            time.sleep(0.1)

    try:
        run_checks()
    finally:
        os.killpg(server.pid, signal.SIGTERM)
        server.wait(timeout=5)

    print()
    print("%d checks passed, %d failed" % (passed, len(failed)))
    for name in failed:
        print("  failed: %s" % name)
    return 1 if failed else 0


def run_checks():
    print("login and identity")
    a = Client("alice")
    a.send("LOGIN alice\n")
    check("LOGIN is acknowledged", a.lines(), ["OK"])

    b = Client("bob")
    b.send("LOGIN alice\n")
    check("duplicate username rejected", b.lines(), ["ERROR username_in_use"])
    b.send("LOGIN bob\n")
    check("second username accepted", b.lines(), ["OK"])

    print("order ids and matching")
    a.send("BUY JNST 100 238\n")
    check("buy accepted", a.lines(), ["ORDER_ACCEPTED 1"])
    b.send("SELL JNST 60 238\n")
    check("partial fill: seller", b.lines(),
          ["ORDER_ACCEPTED 2", "SOLD JNST 60 238"])
    check("partial fill: buyer", a.lines(), ["BOUGHT JNST 60 238"])

    b.send("SELL JNST 60 238\n")
    check("remainder fills for 40", b.lines(),
          ["ORDER_ACCEPTED 3", "SOLD JNST 40 238"])
    check("buyer sees the second fill", a.lines(), ["BOUGHT JNST 40 238"])

    print("orders that must not match")
    a.send("BUY JNST 10 100\n")
    a.lines()
    b.send("SELL JNST 10 101\n")
    check("different price does not match", b.lines(), ["ORDER_ACCEPTED 5"])
    b.send("SELL IMCT 10 100\n")
    check("different instrument does not match", b.lines(),
          ["ORDER_ACCEPTED 6"])
    a.send("BUY JNST 10 100\n")
    check("same side does not match", a.lines(), ["ORDER_ACCEPTED 7"])

    print("value validation")
    for bad, why in [("BUY JNST 0 238", "invalid_quantity"),
                     ("BUY JNST -5 238", "invalid_quantity"),
                     ("BUY JNST 2.5 238", "invalid_quantity"),
                     ("BUY JNST 2147483648 238", "invalid_quantity"),
                     ("BUY JNST 10 0", "invalid_price"),
                     ("BUY JNST 10 -1", "invalid_price"),
                     ("BUY JNST 10 1e3", "invalid_price"),
                     ("BUY XXXX 10 238", "unknown_instrument"),
                     ("BUY JNST 10", "usage_BUY_instrument_quantity_price")]:
        a.send(bad + "\n")
        check("rejects %r" % bad, a.lines(), ["ERROR " + why])

    a.send("BUY JNST 2147483647 2147483647\n")
    check("accepts the largest legal values", a.lines(),
          ["ORDER_ACCEPTED 8"])
    a.send("CANCEL 8\n")
    a.lines()

    print("cancellation")
    a.send("BUY IMCT 25 517\n")
    check("order to cancel accepted", a.lines(), ["ORDER_ACCEPTED 9"])
    a.send("CANCEL 9\n")
    check("cancel works", a.lines(), ["ORDER_CANCELLED 9"])
    a.send("CANCEL 9\n")
    check("cancelling twice fails", a.lines(), ["ERROR no_such_order"])
    a.send("CANCEL 6\n")
    check("cannot cancel another trader's order", a.lines(),
          ["ERROR not_your_order"])
    a.send("CANCEL 1\n")
    check("cannot cancel a fully filled order", a.lines(),
          ["ERROR no_such_order"])
    a.send("CANCEL abc\n")
    check("non-numeric order id rejected", a.lines(),
          ["ERROR invalid_order_id"])

    print("client roles")
    a.send("SUBSCRIBE JNST\n")
    check("trader cannot subscribe", a.lines(),
          ["ERROR not_allowed_for_trader_client"])
    md = Client("md")
    md.send("SUBSCRIBE JNST\n")
    check("subscribe acknowledged", md.lines(), ["OK"])
    md.send("BUY JNST 1 1\n")
    check("market-data client cannot trade", md.lines(),
          ["ERROR not_allowed_for_market_data_client"])
    a.send("HELLO\n")
    check("unknown command rejected", a.lines(), ["ERROR unknown_command"])

    print("message framing")
    a.raw(b"BUY JN")
    check("nothing happens on a partial message", a.lines(0.3), [])
    a.raw(b"ST 7 4")
    check("still nothing halfway through", a.lines(0.3), [])
    a.raw(b"44\n")
    check("message acted on once the newline arrives", a.lines(),
          ["ORDER_ACCEPTED 10"])
    a.raw(b"BUY JNST 1 900\nBUY JNST 1 901\nBUY JNST 1 902\n")
    check("three messages in one write", a.lines(),
          ["ORDER_ACCEPTED 11", "ORDER_ACCEPTED 12", "ORDER_ACCEPTED 13"])

    print("market data fan-out")
    md2 = Client("md2")
    md2.send("SUBSCRIBE JNST\n")
    md2.lines()
    md.lines()
    b.send("SELL JNST 1 902\n")
    b.lines()
    check("first subscriber notified", md.lines(), ["TRADE JNST 1 902"])
    check("second subscriber notified", md2.lines(), ["TRADE JNST 1 902"])

    md2.send("UNSUBSCRIBE JNST\n")
    check("unsubscribe acknowledged", md2.lines(), ["OK"])
    a.send("BUY IMCT 3 700\n")
    a.lines()
    b.send("SELL IMCT 3 700\n")
    b.lines()
    check("JNST subscriber ignores IMCT trades", md.lines(), [])

    md.send("SUBSCRIBE IMCT\n")
    md.lines()
    a.send("BUY IMCT 3 700\n")
    a.lines()
    b.send("SELL IMCT 3 700\n")
    b.lines()
    check("subscribing to a second instrument works", md.lines(),
          ["TRADE IMCT 3 700"])
    check("unsubscribed client is silent", md2.lines(), [])

    print("disconnection")
    ghost = Client("ghost")
    ghost.send("LOGIN ghost\nBUY JNST 5 1234\n")
    check("ghost order accepted", ghost.lines(), ["OK", "ORDER_ACCEPTED 19"])
    ghost.close()
    time.sleep(0.3)
    b.send("SELL JNST 5 1234\n")
    check("order of a departed trader still matches", b.lines(),
          ["ORDER_ACCEPTED 20", "SOLD JNST 5 1234"])
    check("subscribers still see that trade", md.lines(),
          ["TRADE JNST 5 1234"])

    reuse = Client("reuse")
    reuse.send("LOGIN ghost\n")
    check("username freed by disconnection", reuse.lines(), ["OK"])
    reuse.close()

    print("quit")
    q = Client("quit")
    q.send("LOGIN quitter\n")
    q.lines()
    q.send("QUIT\n")
    time.sleep(0.3)
    check("QUIT closes the connection", q.sock.recv(100), b"")

    print("server survives all of that")
    late = Client("late")
    late.send("LOGIN late\n")
    check("still serving new clients", late.lines(), ["OK"])

    for c in (a, b, md, md2, late):
        c.close()


if __name__ == "__main__":
    sys.exit(main())
