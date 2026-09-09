# COL334 Assignment 2 — The Socket Exchange

Shivanshu Aryan (2024CS10237) and DevRaj Das (2024CS10103).

An Exchange Server, a Trader Client and a Market-Data Client speaking the
line-oriented protocol from the handout over TCP.

## Language and runtime

Python 3, standard library only. The only networking module used is `socket`,
together with `select` for readiness notification (`select.kqueue` on FreeBSD,
`select.epoll` on Linux, `select.poll` as the portable fallback). No
third-party library, no `asyncio`, no networking framework of any kind.

Tested on FreeBSD 14.4-RELEASE with the base `python311` package:

    pkg install python311

There is nothing to compile and no configuration file to write. The `Makefile`
is only shorthand for the self-test; `make` is not needed to run anything.

## Running it

Start the exchange:

    ./server/run-server 127.0.0.1 5000

Start a trader (the username is optional; without it, type `LOGIN <name>`
yourself):

    ./client/run-trader 127.0.0.1 5000 alice

Start a market-data client (instrument names on the command line are
subscribed to automatically):

    ./client/run-market-data 127.0.0.1 5000 JNST

Both clients are line-driven. Whatever you type is sent as one protocol
message; lines from the exchange are printed as `<<` as they arrive, including
notifications that turn up long after your own last command. `QUIT` or Ctrl-D
disconnects, Ctrl-C exits immediately.

A short session looks like this:

    $ ./client/run-trader 127.0.0.1 5000 alice
    connected 127.0.0.1:54012 -> 127.0.0.1:5000
    >> LOGIN alice
    << OK
    BUY JNST 100 238
    >> BUY JNST 100 238
    << ORDER_ACCEPTED 1
    << BOUGHT JNST 60 238      <- arrives later, when someone sells

The server writes a log line to stderr for every connection, every `recv()`
and every message in either direction, which is the clearest way to see what
it actually received as opposed to what was sent. Redirect it to keep it:

    ./server/run-server 127.0.0.1 5000 2> server.log

Pass `--quiet` (or set `EXCHANGE_QUIET=1`) to suppress the per-message trace
and keep only connection-level and backpressure events. At high message rates
the trace costs more than the networking does.

`EXCHANGE_POLLER=kqueue|epoll|poll` forces a particular readiness mechanism.
Without it the server picks the best one the platform has.

## Layout

    server/run-server           launcher for the Exchange Server
    client/run-trader           launcher for a Trader Client
    client/run-market-data      launcher for a Market-Data Client

    src/server.py               event loop, protocol dispatch, connection state
    src/orderbook.py            orders, matching, cancellation
    src/protocol.py             framing and value validation, shared
    src/poller.py               kqueue / epoll / poll behind one interface
    src/session.py              shared client loop over stdin and the socket
    src/trader.py               Trader Client
    src/market_data.py          Market-Data Client
    src/loadgen.py              idle-connection generator for the bonus

    tools/selftest.py           protocol conformance checks

## Checking that it works

    python3 tools/selftest.py        # or: make check

Starts a server on port 5399, drives it with plain sockets and checks 47
protocol behaviours: matching and partial fills, orders that must not match,
value validation, cancellation and ownership, role enforcement, framing across
and within `recv()` boundaries, market-data fan-out, and what happens to a
resting order when the trader that placed it disconnects.

## Design summary

One process, one thread, non-blocking sockets and a single readiness loop.
Each connection carries its own input buffer, so a partial message is held
until its newline arrives without stalling anyone else, and its own output
queue, so a client that stops reading grows its own backlog rather than
blocking the server in `send()`. That queue is capped at 8 MiB, past which the
client is dropped. Resting orders survive their trader disconnecting, as the
specification requires.

For the bonus the server raises its own `RLIMIT_NOFILE` to the hard limit at
startup and listens with a backlog of 4096. `src/loadgen.py` opens and holds
the idle connections:

    python3 src/loadgen.py 127.0.0.1 5000 70000 --sources=127.0.0.1,127.0.0.2

A single source address only has about 55k usable ephemeral ports, so reaching
70k needs extra loopback aliases (`ifconfig lo0 alias 127.0.0.2/32`, and so
on) and `kern.maxfiles` / `kern.maxfilesperproc` raised to suit.
