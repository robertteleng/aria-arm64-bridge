#include "aria_bridge/consumer.hpp"

#include <zmq.h>

#include <stdexcept>
#include <utility>

namespace aria_bridge {
namespace {

// RAII wrapper for a zmq_msg_t. zmq_msg_close must run exactly once per
// successful init, including on the error paths, or the socket leaks buffers
// for every malformed message it receives.
class Msg {
public:
    Msg() { zmq_msg_init(&msg_); }
    ~Msg() { zmq_msg_close(&msg_); }
    Msg(const Msg&) = delete;
    Msg& operator=(const Msg&) = delete;

    zmq_msg_t* get() noexcept { return &msg_; }

    [[nodiscard]] std::span<const std::uint8_t> bytes() noexcept {
        return {static_cast<const std::uint8_t*>(zmq_msg_data(&msg_)), zmq_msg_size(&msg_)};
    }

    // Reuse the same message object for the next part: close + re-init.
    void reset() noexcept {
        zmq_msg_close(&msg_);
        zmq_msg_init(&msg_);
    }

private:
    zmq_msg_t msg_{};
};

}  // namespace

struct Consumer::Impl {
    void* context{nullptr};
    void* socket{nullptr};
    // The two parts stay alive between poll() calls because the spans handed to
    // the caller point straight into them — that is what makes this zero-copy.
    Msg header_part;
    Msg payload_part;

    ~Impl() {
        if (socket) zmq_close(socket);
        if (context) zmq_ctx_term(context);
    }
};

Consumer::Consumer(std::string endpoint, int receive_high_water_mark)
    : impl_(std::make_unique<Impl>()), endpoint_(std::move(endpoint)) {
    impl_->context = zmq_ctx_new();
    if (!impl_->context) throw std::runtime_error("zmq_ctx_new failed");

    impl_->socket = zmq_socket(impl_->context, ZMQ_PULL);
    if (!impl_->socket) throw std::runtime_error("zmq_socket failed");

    // Match the receiver's SNDHWM. Too small and SLAM pairs plus sensor bursts
    // get dropped on any consumer hiccup; this is the value the Python side
    // settled on after measuring exactly that.
    if (zmq_setsockopt(impl_->socket, ZMQ_RCVHWM, &receive_high_water_mark,
                       sizeof(receive_high_water_mark)) != 0) {
        throw std::runtime_error("zmq_setsockopt(ZMQ_RCVHWM) failed");
    }

    if (zmq_connect(impl_->socket, endpoint_.c_str()) != 0) {
        throw std::runtime_error("zmq_connect failed for " + endpoint_);
    }
}

Consumer::~Consumer() = default;
Consumer::Consumer(Consumer&&) noexcept = default;
Consumer& Consumer::operator=(Consumer&&) noexcept = default;

std::optional<Message> Consumer::poll(std::chrono::milliseconds timeout) {
    zmq_pollitem_t item{impl_->socket, 0, ZMQ_POLLIN, 0};
    const int ready = zmq_poll(&item, 1, static_cast<long>(timeout.count()));
    if (ready <= 0) return std::nullopt;  // timeout, or interrupted

    impl_->header_part.reset();
    impl_->payload_part.reset();

    if (zmq_msg_recv(impl_->header_part.get(), impl_->socket, 0) < 0) return std::nullopt;

    // Every message in this protocol is exactly two parts. A first part with no
    // second is a truncated send — drop it rather than block waiting.
    if (zmq_msg_more(impl_->header_part.get()) == 0) {
        ++dropped_;
        return std::nullopt;
    }
    if (zmq_msg_recv(impl_->payload_part.get(), impl_->socket, 0) < 0) {
        ++dropped_;
        return std::nullopt;
    }

    const auto header = impl_->header_part.bytes();
    const auto payload = impl_->payload_part.bytes();
    ++received_;

    if (is_frame_message(header)) {
        const auto parsed = parse_frame_header(header);
        // A payload whose size disagrees with the header would make every
        // downstream span a buffer overrun. Drop it.
        if (!parsed || payload.size() != parsed->expected_bytes()) {
            ++dropped_;
            return std::nullopt;
        }
        return Message{FrameView{*parsed, payload}};
    }

    if (is_sensor_message(header)) {
        const auto parsed = parse_sensor_header(header);
        if (!parsed) {
            ++dropped_;
            return std::nullopt;
        }
        return Message{SensorView{*parsed, payload}};
    }

    ++dropped_;  // unknown magic — a newer protocol version, or noise
    return std::nullopt;
}

std::optional<FrameView> Consumer::poll_frame(std::chrono::milliseconds timeout) {
    const auto deadline = std::chrono::steady_clock::now() + timeout;
    do {
        const auto remaining = std::chrono::duration_cast<std::chrono::milliseconds>(
            deadline - std::chrono::steady_clock::now());
        if (auto msg = poll(remaining.count() > 0 ? remaining : std::chrono::milliseconds{0})) {
            if (auto* frame = std::get_if<FrameView>(&*msg)) return *frame;
        }
    } while (std::chrono::steady_clock::now() < deadline);
    return std::nullopt;
}

}  // namespace aria_bridge
