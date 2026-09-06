#pragma once
#include <cstddef>
#include <string>
class Framing {
    public:
        void append(const char* data, std::size_t sz);
        bool next(std::string& frame);
        std::size_t remains() const;
    private:
        std::string buf_;
        std::size_t scan_ = 0;
};