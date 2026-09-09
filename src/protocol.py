"""Shared wire-format helpers for the Socket Exchange.

Everything on the wire is one line of ASCII terminated by '\n'.  TCP gives us
a byte stream, so both the server and the clients push whatever recv() handed
them into a buffer and pull complete lines back out of it.
"""

INSTRUMENTS = ("JNST", "IMCT")

INT_MAX = 2147483647


class LineReader:
    """Turns a TCP byte stream back into application messages.

    feed() takes the raw result of one recv() and returns the complete lines
    that became available, which may be zero, one, or several.
    """

    def __init__(self, limit=64 * 1024):
        self.buf = bytearray()
        self.limit = limit

    def feed(self, chunk):
        self.buf.extend(chunk)

        lines = []
        while True:
            i = self.buf.find(b"\n")
            if i < 0:
                break
            line = bytes(self.buf[:i])
            del self.buf[: i + 1]
            lines.append(line.rstrip(b"\r").decode("utf-8", "replace"))

        if len(self.buf) > self.limit:
            raise ValueError("message too long")

        return lines

    def pending(self):
        return len(self.buf)


def parse_uint(token, low, high):
    """Strict decimal integer parse.

    int() alone is too permissive here: it happily eats '+5', ' 5 ' and
    '1_000', none of which the protocol allows.  Signs, decimal points and
    anything non-numeric have to be rejected, so check the characters first.
    """
    if not token or not token.isascii() or not token.isdigit():
        return None
    value = int(token)
    if value < low or value > high:
        return None
    return value


def parse_quantity(token):
    return parse_uint(token, 1, INT_MAX)


def parse_price(token):
    return parse_uint(token, 1, INT_MAX)


def parse_order_id(token):
    return parse_uint(token, 0, INT_MAX)


def valid_instrument(name):
    return name in INSTRUMENTS


def valid_username(name):
    if not name or len(name) > 64:
        return False
    return all(c.isalnum() or c in "._-" for c in name)
