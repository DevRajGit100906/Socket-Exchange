CXX      = c++
CXXFLAGS = -std=c++17 -O2 -Wall -Wextra \
           -Isrc/common/include -Isrc/server/include -Isrc/client/include
COMMON  = src/common/src/framing.cpp src/common/src/net.cpp
SERVER  = src/server/src/main.cpp src/server/src/eventloop.cpp \
          src/server/src/conn.cpp src/server/src/proto.cpp \
          src/server/src/book.cpp src/server/src/session.cpp \
          src/server/src/mdfeed.cpp
CLIENT  = src/client/src/client_common.cpp
HEADERS = src/common/include/protocol.hpp src/common/include/framing.hpp \
          src/common/include/net.hpp src/server/include/eventloop.hpp \
          src/server/include/conn.hpp src/server/include/proto.hpp \
          src/server/include/book.hpp src/server/include/session.hpp \
          src/server/include/mdfeed.hpp src/client/include/client_common.hpp
all: build/exchange_server build/trader_client build/market_data_client
build/exchange_server: $(SERVER) $(COMMON) $(HEADERS)
	mkdir -p build
	$(CXX) $(CXXFLAGS) -o build/exchange_server $(SERVER) $(COMMON)
build/trader_client: src/client/src/trader.cpp $(CLIENT) $(COMMON) $(HEADERS)
	mkdir -p build
	$(CXX) $(CXXFLAGS) -o build/trader_client src/client/src/trader.cpp $(CLIENT) $(COMMON)
build/market_data_client: src/client/src/market_data.cpp $(CLIENT) $(COMMON) $(HEADERS)
	mkdir -p build
	$(CXX) $(CXXFLAGS) -o build/market_data_client src/client/src/market_data.cpp $(CLIENT) $(COMMON)
clean:
	rm -rf build
.PHONY: all clean