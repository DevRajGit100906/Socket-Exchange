#!/usr/bin/env python3
"""The Exchange Server.

One process, one thread, non-blocking sockets, one readiness loop over kqueue
(poll() off FreeBSD).  Nothing the server does on behalf of one client is
allowed to block another, so every recv() and send() is non-blocking and every
connection carries its own input buffer for framing and its own output queue
for the case where the peer is not draining fast enough.

Usage: server.py <host> <port> [--quiet]
"""

import errno
import os
import resource
import signal
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from orderbook import OrderBook
from poller import make_poller
from protocol import (LineReader, parse_order_id, parse_price, parse_quantity,
                      valid_instrument, valid_username)

BACKLOG = 4096
RECV_SIZE = 65536

# If a peer stops reading, its unsent updates pile up here.  Past this we give
# up on it rather than let one stalled subscriber consume the server's memory.
MAX_QUEUED = 8 * 1024 * 1024

TRADER_ONLY = ("LOGIN", "BUY", "SELL", "CANCEL")
MARKET_DATA_ONLY = ("SUBSCRIBE", "UNSUBSCRIBE")

START = time.time()


def log(fmt, *args):
    sys.stderr.write("[%8.3f] %s\n" % (time.time() - START,
                                       fmt % args if args else fmt))
    sys.stderr.flush()


class Conn:
    __slots__ = ("sock", "fd", "peer", "role", "user", "reader", "out",
                 "subs", "alive", "draining", "warned")

    def __init__(self, sock, peer):
        self.sock = sock
        self.fd = sock.fileno()
        self.peer = peer
        self.role = None          # None until the first role-specific command
        self.user = None
        self.reader = LineReader()
        self.out = bytearray()
        self.subs = set()
        self.alive = True
        self.draining = False     # QUIT seen: flush what is queued, then go
        self.warned = 0

    def tag(self):
        who = self.user or self.role or "new"
        return "fd %d %s" % (self.fd, who)


class Exchange:
    def __init__(self, host, port, quiet=False):
        self.quiet = quiet
        self.book = OrderBook()
        self.conns = {}
        self.usernames = {}
        self.subscribers = {}     # instrument -> set of Conn
        self.poller = make_poller()

        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind((host, port))
        self.listener.listen(BACKLOG)
        self.listener.setblocking(False)
        self.lfd = self.listener.fileno()
        self.poller.add(self.lfd)

        self.running = True
        self.accepted = 0

        log("listening on %s:%d, listen fd %d, backlog %d, %s",
            host, port, self.lfd, BACKLOG, self.poller.name)

    # -- plumbing ---------------------------------------------------------

    def trace(self, fmt, *args):
        if not self.quiet:
            log(fmt, *args)

    def accept_ready(self):
        while True:
            try:
                sock, peer = self.listener.accept()
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                    return
                if exc.errno in (errno.EMFILE, errno.ENFILE):
                    log("accept: out of file descriptors (%s)",
                        errno.errorcode.get(exc.errno))
                    return
                if exc.errno in (errno.ECONNABORTED, errno.EINTR):
                    continue
                raise

            try:
                sock.setblocking(False)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError as exc:
                self.trace("fd %d reset before setup: %s", sock.fileno(), exc)
                sock.close()
                continue

            conn = Conn(sock, peer)
            self.conns[conn.fd] = conn
            self.poller.add(conn.fd)
            self.accepted += 1
            self.trace("accept fd %d from %s:%d (%d open)",
                       conn.fd, peer[0], peer[1], len(self.conns))

    def send(self, conn, text):
        if not conn.alive:
            return
        data = text.encode("ascii")

        if not conn.out:
            # Nothing queued yet, so try to hand it straight to the kernel.
            try:
                n = conn.sock.send(data)
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EINTR):
                    n = 0
                else:
                    self.trace("%s send failed: %s", conn.tag(), exc)
                    self.drop(conn, "send error")
                    return
            if n == len(data):
                return
            data = data[n:]
            log("%s socket buffer full, queueing %d bytes",
                conn.tag(), len(data))

        conn.out.extend(data)

        if len(conn.out) > MAX_QUEUED:
            log("%s unread backlog %d bytes exceeds limit, dropping client",
                conn.tag(), len(conn.out))
            self.drop(conn, "slow consumer")
            return

        if len(conn.out) >= conn.warned + 65536:
            conn.warned = len(conn.out)
            log("%s %d bytes still unsent (receiver not reading)",
                conn.tag(), len(conn.out))

        self.poller.set_write(conn.fd, True)

    def flush(self, conn):
        while conn.out:
            try:
                n = conn.sock.send(conn.out)
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EINTR):
                    return
                self.trace("%s send failed: %s", conn.tag(), exc)
                self.drop(conn, "send error")
                return
            if n <= 0:
                return
            del conn.out[:n]

        conn.warned = 0
        self.poller.set_write(conn.fd, False)
        if conn.draining:
            self.drop(conn, "QUIT")

    def drop(self, conn, why):
        if not conn.alive:
            return
        conn.alive = False
        self.trace("close fd %d (%s), %d bytes undelivered",
                   conn.fd, why, len(conn.out))

        for instrument in conn.subs:
            subs = self.subscribers.get(instrument)
            if subs:
                subs.discard(conn)
        conn.subs.clear()

        if conn.user and self.usernames.get(conn.user) is conn:
            del self.usernames[conn.user]

        self.poller.remove(conn.fd)
        self.conns.pop(conn.fd, None)
        try:
            conn.sock.close()
        except OSError:
            pass
        # Resting orders from this trader deliberately stay in the book.

    # -- reading ----------------------------------------------------------

    def read_ready(self, conn):
        while True:
            try:
                chunk = conn.sock.recv(RECV_SIZE)
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EINTR):
                    return
                if exc.errno == errno.ECONNRESET:
                    self.trace("%s recv: connection reset by peer (RST)",
                               conn.tag())
                    self.drop(conn, "reset by peer")
                    return
                self.trace("%s recv failed: %s", conn.tag(), exc)
                self.drop(conn, "recv error")
                return

            if not chunk:
                self.trace("%s recv returned 0 bytes: peer sent FIN",
                           conn.tag())
                self.drop(conn, "peer closed")
                return

            self.trace("%s recv %d bytes %r", conn.tag(), len(chunk), chunk)

            try:
                lines = conn.reader.feed(chunk)
            except ValueError:
                self.send(conn, "ERROR message_too_long\n")
                self.drop(conn, "oversized message")
                return

            if not lines:
                self.trace("%s no newline yet, %d bytes held for later",
                           conn.tag(), conn.reader.pending())

            for line in lines:
                self.trace("%s <- %s", conn.tag(), line)
                self.handle(conn, line)
                if not conn.alive:
                    return

            if len(chunk) < RECV_SIZE:
                return

    # -- protocol ---------------------------------------------------------

    def reply(self, conn, text):
        self.trace("%s -> %s", conn.tag(), text.rstrip())
        self.send(conn, text)

    def handle(self, conn, line):
        if conn.draining:
            # QUIT already seen; we are only waiting for the queue to empty.
            return

        parts = line.split()
        if not parts:
            self.reply(conn, "ERROR empty_message\n")
            return

        cmd = parts[0].upper()
        args = parts[1:]

        if cmd == "QUIT":
            conn.draining = True
            if not conn.out:
                self.drop(conn, "QUIT")
            return

        if cmd in TRADER_ONLY:
            if conn.role == "market-data":
                self.reply(conn, "ERROR not_allowed_for_market_data_client\n")
                return
            conn.role = "trader"
        elif cmd in MARKET_DATA_ONLY:
            if conn.role == "trader":
                self.reply(conn, "ERROR not_allowed_for_trader_client\n")
                return
            conn.role = "market-data"
        else:
            self.reply(conn, "ERROR unknown_command\n")
            return

        if cmd == "LOGIN":
            self.do_login(conn, args)
        elif cmd in ("BUY", "SELL"):
            self.do_order(conn, cmd, args)
        elif cmd == "CANCEL":
            self.do_cancel(conn, args)
        elif cmd == "SUBSCRIBE":
            self.do_subscribe(conn, args)
        elif cmd == "UNSUBSCRIBE":
            self.do_unsubscribe(conn, args)

    def do_login(self, conn, args):
        if len(args) != 1:
            self.reply(conn, "ERROR usage_LOGIN_username\n")
            return
        name = args[0]
        if not valid_username(name):
            self.reply(conn, "ERROR invalid_username\n")
            return
        holder = self.usernames.get(name)
        if holder is not None and holder is not conn:
            self.reply(conn, "ERROR username_in_use\n")
            return
        if conn.user and self.usernames.get(conn.user) is conn:
            del self.usernames[conn.user]
        conn.user = name
        self.usernames[name] = conn
        self.reply(conn, "OK\n")

    def do_order(self, conn, side, args):
        if len(args) != 3:
            self.reply(conn, "ERROR usage_%s_instrument_quantity_price\n"
                       % side)
            return

        instrument, qty_tok, price_tok = args
        if not valid_instrument(instrument):
            self.reply(conn, "ERROR unknown_instrument\n")
            return

        qty = parse_quantity(qty_tok)
        if qty is None:
            self.reply(conn, "ERROR invalid_quantity\n")
            return

        price = parse_price(price_tok)
        if price is None:
            self.reply(conn, "ERROR invalid_price\n")
            return

        order, trades = self.book.submit(side, instrument, qty, price, conn)
        self.reply(conn, "ORDER_ACCEPTED %d\n" % order.oid)

        for trade in trades:
            self.publish(trade)

    def do_cancel(self, conn, args):
        if len(args) != 1:
            self.reply(conn, "ERROR usage_CANCEL_order_id\n")
            return
        oid = parse_order_id(args[0])
        if oid is None:
            self.reply(conn, "ERROR invalid_order_id\n")
            return
        result = self.book.cancel(oid, conn)
        if isinstance(result, str):
            self.reply(conn, "ERROR %s\n" % result)
            return
        self.reply(conn, "ORDER_CANCELLED %d\n" % oid)

    def do_subscribe(self, conn, args):
        if len(args) != 1:
            self.reply(conn, "ERROR usage_SUBSCRIBE_instrument\n")
            return
        instrument = args[0]
        if not valid_instrument(instrument):
            self.reply(conn, "ERROR unknown_instrument\n")
            return
        conn.subs.add(instrument)
        self.subscribers.setdefault(instrument, set()).add(conn)
        self.reply(conn, "OK\n")

    def do_unsubscribe(self, conn, args):
        if len(args) != 1:
            self.reply(conn, "ERROR usage_UNSUBSCRIBE_instrument\n")
            return
        instrument = args[0]
        if not valid_instrument(instrument):
            self.reply(conn, "ERROR unknown_instrument\n")
            return
        if instrument not in conn.subs:
            self.reply(conn, "ERROR not_subscribed\n")
            return
        conn.subs.discard(instrument)
        subs = self.subscribers.get(instrument)
        if subs:
            subs.discard(conn)
        self.reply(conn, "OK\n")

    def publish(self, trade):
        buyer = trade.buy.owner
        seller = trade.sell.owner

        # A trader that has gone away gets no notification, but the trade
        # itself still happened and the subscribers still hear about it.
        if buyer is not None and buyer.alive:
            self.send(buyer, "BOUGHT %s %d %d\n"
                      % (trade.instrument, trade.qty, trade.price))
        if seller is not None and seller.alive:
            self.send(seller, "SOLD %s %d %d\n"
                      % (trade.instrument, trade.qty, trade.price))

        update = "TRADE %s %d %d\n" % (trade.instrument, trade.qty,
                                       trade.price)
        for conn in list(self.subscribers.get(trade.instrument, ())):
            if conn.alive:
                self.send(conn, update)

    # -- loop -------------------------------------------------------------

    def run(self):
        while self.running:
            try:
                events = self.poller.wait(1.0)
            except (OSError, InterruptedError) as exc:
                if getattr(exc, "errno", None) == errno.EINTR:
                    continue
                raise

            for fd, readable, writable, closed in events:
                if fd == self.lfd:
                    self.accept_ready()
                    continue

                conn = self.conns.get(fd)
                if conn is None:
                    continue

                try:
                    if writable and conn.alive:
                        self.flush(conn)
                    if readable and conn.alive:
                        self.read_ready(conn)
                    if closed and conn.alive and not readable:
                        self.trace("%s poller reports hangup", conn.tag())
                        self.drop(conn, "hangup")
                except Exception as exc:      # never let one client win
                    log("%s unhandled error: %r", conn.tag(), exc)
                    self.drop(conn, "internal error")

    def shutdown(self):
        log("shutting down: %d connections open, %d accepted in total, "
            "%d orders resting", len(self.conns), self.accepted,
            self.book.outstanding())
        for conn in list(self.conns.values()):
            self.drop(conn, "server shutdown")
        try:
            self.listener.close()
        except OSError:
            pass
        self.poller.close()


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
    args = [a for a in argv[1:] if not a.startswith("-")]
    quiet = "--quiet" in argv or "-q" in argv or os.environ.get("EXCHANGE_QUIET")

    if len(args) != 2:
        sys.stderr.write("usage: server.py <host> <port> [--quiet]\n")
        return 2

    host, port = args[0], int(args[1])

    limit = raise_fd_limit()
    log("pid %d, RLIMIT_NOFILE soft limit %d", os.getpid(), limit)

    exchange = Exchange(host, port, quiet=quiet)

    caught = []
    def stop(signum, _frame):
        caught.append(signum)
        exchange.running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGPIPE, signal.SIG_IGN)

    try:
        exchange.run()
    finally:
        if caught:
            log("caught signal %d", caught[0])
        exchange.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
