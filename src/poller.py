"""Readiness notification behind one small interface.

The exchange runs on FreeBSD, so kqueue is the real target.  epoll is its
Linux equivalent and most of the development happened there, and poll() is the
portable fallback that works on both.  All three expose the same four calls
and hand back events as (fd, readable, writable, closed) tuples, so server.py
never has to know which one it got.

Keeping poll() around is not only for portability: it is the cheap way to show
what the scalability bonus is about.  poll() hands the kernel the whole set of
descriptors on every call and gets the whole set back, so its cost grows with
the number of connections whether or not anything happened on them.  kqueue
and epoll register interest once and report only what is ready.  Set
EXCHANGE_POLLER=poll|epoll|kqueue to force a particular backend and measure
the difference.
"""

import os
import select

HAVE_KQUEUE = hasattr(select, "kqueue")
HAVE_EPOLL = hasattr(select, "epoll")


class KqueuePoller:
    name = "kqueue"

    def __init__(self):
        self.kq = select.kqueue()
        self.want_write = {}

    def add(self, fd):
        self.kq.control(
            [select.kevent(fd, select.KQ_FILTER_READ, select.KQ_EV_ADD)],
            0)
        self.want_write[fd] = False

    def set_write(self, fd, on):
        if self.want_write.get(fd) == on:
            return
        flag = select.KQ_EV_ADD if on else select.KQ_EV_DELETE
        try:
            self.kq.control(
                [select.kevent(fd, select.KQ_FILTER_WRITE, flag)], 0)
        except OSError:
            pass
        self.want_write[fd] = on

    def remove(self, fd):
        # Closing the descriptor removes its knotes; we only have to forget it.
        self.want_write.pop(fd, None)

    def wait(self, timeout):
        events = self.kq.control(None, 64, timeout)
        out = {}
        for ev in events:
            fd = ev.ident
            r, w, closed = out.get(fd, (False, False, False))
            if ev.filter == select.KQ_FILTER_READ:
                r = True
            elif ev.filter == select.KQ_FILTER_WRITE:
                w = True
            if ev.flags & select.KQ_EV_EOF:
                closed = True
            out[fd] = (r, w, closed)
        return [(fd, r, w, c) for fd, (r, w, c) in out.items()]

    def close(self):
        self.kq.close()


class PollPoller:
    name = "poll"

    READ = select.POLLIN | select.POLLPRI
    ERR = select.POLLHUP | select.POLLERR | select.POLLNVAL

    def __init__(self):
        self.p = select.poll()
        self.want_write = {}

    def add(self, fd):
        self.want_write[fd] = False
        self.p.register(fd, self.READ)

    def set_write(self, fd, on):
        if self.want_write.get(fd) == on:
            return
        self.want_write[fd] = on
        mask = self.READ | (select.POLLOUT if on else 0)
        self.p.modify(fd, mask)

    def remove(self, fd):
        self.want_write.pop(fd, None)
        try:
            self.p.unregister(fd)
        except KeyError:
            pass

    def wait(self, timeout):
        ms = -1 if timeout is None else int(timeout * 1000)
        out = []
        for fd, ev in self.p.poll(ms):
            out.append((fd,
                        bool(ev & self.READ),
                        bool(ev & select.POLLOUT),
                        bool(ev & self.ERR)))
        return out

    def close(self):
        pass


class EpollPoller:
    name = "epoll"

    READ = select.EPOLLIN | select.EPOLLPRI if HAVE_EPOLL else 0
    ERR = select.EPOLLHUP | select.EPOLLERR if HAVE_EPOLL else 0

    def __init__(self):
        self.ep = select.epoll()
        self.want_write = {}

    def add(self, fd):
        self.want_write[fd] = False
        self.ep.register(fd, self.READ)

    def set_write(self, fd, on):
        if self.want_write.get(fd) == on:
            return
        self.want_write[fd] = on
        self.ep.modify(fd, self.READ | (select.EPOLLOUT if on else 0))

    def remove(self, fd):
        self.want_write.pop(fd, None)
        try:
            self.ep.unregister(fd)
        except OSError:
            pass

    def wait(self, timeout):
        out = []
        for fd, ev in self.ep.poll(-1 if timeout is None else timeout, 64):
            out.append((fd,
                        bool(ev & self.READ),
                        bool(ev & select.EPOLLOUT),
                        bool(ev & self.ERR)))
        return out

    def close(self):
        self.ep.close()


def make_poller():
    forced = os.environ.get("EXCHANGE_POLLER", "").lower()
    if forced == "poll":
        return PollPoller()
    if forced == "epoll" and HAVE_EPOLL:
        return EpollPoller()
    if forced == "kqueue" and HAVE_KQUEUE:
        return KqueuePoller()

    if HAVE_KQUEUE:
        return KqueuePoller()
    if HAVE_EPOLL:
        return EpollPoller()
    return PollPoller()
