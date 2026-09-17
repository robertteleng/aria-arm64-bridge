// Native ARM64 consumer for the Aria bridge — frames into C++/CUDA without Python.
//
// The Python observer and this class are peers: both connect a ZMQ PULL socket
// to the receiver running under FEX-Emu and decode the same wire protocol. Use
// this one when the consumer is C++ (a CUDA pipeline, a native SLAM front end)
// and crossing into Python would mean an extra copy and the GIL.
//
// Threading: a Consumer is NOT thread-safe. Poll it from one thread. The bytes
// handed out by poll() point into the socket's own buffer and stay valid only
// until the next poll() on the same object — copy them if you need to keep them.
#pragma once

#include <chrono>
#include <memory>
#include <optional>
#include <span>
#include <string>
#include <variant>

#include "aria_bridge/protocol.hpp"

namespace aria_bridge {

// A frame borrowed from the socket buffer. Zero-copy: `pixels` is not owned.
struct FrameView {
    FrameHeader header{};
    std::span<const std::uint8_t> pixels{};
};

// A sensor batch borrowed from the socket buffer.
struct SensorView {
    SensorHeader header{};
    std::span<const std::uint8_t> payload{};

    // Number of whole IMU samples in the payload (0 for non-IMU sensors).
    [[nodiscard]] std::size_t imu_sample_count() const noexcept {
        const bool is_imu = header.sensor == Sensor::Imu1 || header.sensor == Sensor::Imu2;
        return is_imu ? payload.size() / kImuSampleSize : 0;
    }
};

using Message = std::variant<FrameView, SensorView>;

class Consumer {
public:
    // Connects immediately. ZMQ PULL tolerates connecting before the receiver
    // binds, so construction does not fail just because nothing is publishing.
    explicit Consumer(std::string endpoint = std::string(kDefaultEndpoint),
                      int receive_high_water_mark = 64);
    ~Consumer();

    // Owns a socket and a context: copying would double-close them.
    Consumer(const Consumer&) = delete;
    Consumer& operator=(const Consumer&) = delete;
    Consumer(Consumer&&) noexcept;
    Consumer& operator=(Consumer&&) noexcept;

    // Waits up to `timeout` for one message.
    //
    // Returns nullopt on timeout AND on a malformed message — both mean "nothing
    // usable this round", and a caller polling in a loop handles them the same
    // way. Counters below separate the two when it matters.
    [[nodiscard]] std::optional<Message> poll(std::chrono::milliseconds timeout);

    // Convenience: poll until a frame arrives or the deadline passes, skipping
    // sensor batches.
    [[nodiscard]] std::optional<FrameView> poll_frame(std::chrono::milliseconds timeout);

    [[nodiscard]] const std::string& endpoint() const noexcept { return endpoint_; }
    [[nodiscard]] std::uint64_t messages_received() const noexcept { return received_; }
    [[nodiscard]] std::uint64_t messages_dropped() const noexcept { return dropped_; }

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;   // keeps <zmq.h> out of this header
    std::string endpoint_;
    std::uint64_t received_{0};
    std::uint64_t dropped_{0};
};

}  // namespace aria_bridge
