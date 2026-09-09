# Nothing here is needed to run the exchange - it is plain Python with no
# build step. These targets are just shorthand.

PYTHON ?= python3
HOST   ?= 127.0.0.1
PORT   ?= 5000

.PHONY: all check run clean

all: check

check:
	$(PYTHON) tools/selftest.py

run:
	./server/run-server $(HOST) $(PORT)

clean:
	rm -rf src/__pycache__
