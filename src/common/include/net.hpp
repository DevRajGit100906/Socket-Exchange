#pragma once
#include <cstddef>
#include <cstdint>
#include <string>
namespace net {
    enum class IoStatus {
        Ok,
        WouldBlock,
        Closed,
        Reset,
        Error
    };
    struct IoResult {
        IoStatus status;
        std::size_t bytes;
        int error;
    };
    int listen_on(std::uint16_t port, int backlog = 128);
    int accept_one(int listen_fd, std::string* peer = nullptr);
    int connect_to(const std::string& host, std::uint16_t port);
    int set_nonblocking(int fd);
    IoResult recv_bytes(int fd, char* buf, std::size_t cap);
    IoResult send_bytes(int fd, const char* buf, std::size_t len);
    void close_fd(int& fd);
}