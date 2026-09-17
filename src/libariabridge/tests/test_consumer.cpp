// Tests for libariabridge.
//
//   ariabridge_tests --unit                     parsing only, no sockets
//   ariabridge_tests --integration <ep> <n>     receive n frames from a live
//                                               endpoint (the Python mock)
//
// No test framework on purpose: one dependency (libzmq) is already one more
// than this repo had, and the assertions here are simple enough that a macro
// reporting file:line is all that is missing.
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <span>
#include <string>
#include <vector>

#include "aria_bridge/consumer.hpp"
#include "aria_bridge/protocol.hpp"

namespace {

int g_failures = 0;
int g_checks = 0;

#define CHECK(cond)                                                          \
    do {                                                                     \
        ++g_checks;                                                          \
        if (!(cond)) {                                                       \
            std::printf("  FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond);    \
            ++g_failures;                                                    \
        }                                                                    \
    } while (0)

using aria_bridge::Camera;
using aria_bridge::Sensor;

// Build a frame header exactly as Python's struct.pack("<4sB3xQIII", ...) does.
std::vector<std::uint8_t> make_frame_header(std::uint8_t cam, std::uint64_t ts,
                                            std::uint32_t w, std::uint32_t h,
                                            std::uint32_t ch,
                                            const char* magic = "ARI2") {
    std::vector<std::uint8_t> buf(aria_bridge::kFrameHeaderSize, 0);
    std::memcpy(buf.data(), magic, 4);
    buf[4] = cam;
    std::memcpy(buf.data() + 8, &ts, 8);
    std::memcpy(buf.data() + 16, &w, 4);
    std::memcpy(buf.data() + 20, &h, 4);
    std::memcpy(buf.data() + 24, &ch, 4);
    return buf;
}

void test_frame_header_round_trip() {
    const auto buf = make_frame_header(0, 1234567890123ULL, 1408, 1408, 3);
    const auto h = aria_bridge::parse_frame_header(buf);
    CHECK(h.has_value());
    CHECK(h->camera == Camera::Rgb);
    CHECK(h->timestamp_ns == 1234567890123ULL);
    CHECK(h->width == 1408 && h->height == 1408 && h->channels == 3);
    CHECK(h->expected_bytes() == 1408ULL * 1408ULL * 3ULL);  // 5.9 MB
}

void test_every_camera_id_maps() {
    for (std::uint8_t id = 0; id <= 3; ++id) {
        const auto h = aria_bridge::parse_frame_header(make_frame_header(id, 0, 4, 4, 1));
        CHECK(h.has_value());
        CHECK(static_cast<std::uint8_t>(h->camera) == id);
    }
}

void test_malformed_headers_are_rejected() {
    // Wrong magic — this is what makes ARS1 sensor batches skippable by an
    // ARI2-only consumer instead of being misread as frames.
    CHECK(!aria_bridge::parse_frame_header(make_frame_header(0, 0, 4, 4, 3, "ARS1")).has_value());
    // Undefined camera id
    CHECK(!aria_bridge::parse_frame_header(make_frame_header(9, 0, 4, 4, 3)).has_value());
    // Zero dimension: expected_bytes() would be 0 and every payload would "match"
    CHECK(!aria_bridge::parse_frame_header(make_frame_header(0, 0, 0, 4, 3)).has_value());
    CHECK(!aria_bridge::parse_frame_header(make_frame_header(0, 0, 4, 4, 0)).has_value());
    // Truncated header
    auto truncated = make_frame_header(0, 0, 4, 4, 3);
    truncated.resize(12);
    CHECK(!aria_bridge::parse_frame_header(truncated).has_value());
    // Empty buffer must not read out of bounds
    CHECK(!aria_bridge::parse_frame_header({}).has_value());
}

void test_sensor_header_and_imu_samples() {
    std::vector<std::uint8_t> buf(aria_bridge::kSensorHeaderSize, 0);
    std::memcpy(buf.data(), "ARS1", 4);
    buf[4] = static_cast<std::uint8_t>(Sensor::Imu1);
    const std::uint32_t count = 2;
    std::memcpy(buf.data() + 8, &count, 4);

    const auto h = aria_bridge::parse_sensor_header(buf);
    CHECK(h.has_value());
    CHECK(h->sensor == Sensor::Imu1);
    CHECK(h->sample_count == 2);
    CHECK(aria_bridge::is_sensor_message(buf));
    CHECK(!aria_bridge::is_frame_message(buf));

    // Two samples: <q6f> each, 32 bytes.
    std::vector<std::uint8_t> payload(2 * aria_bridge::kImuSampleSize, 0);
    const std::int64_t ts = 42;
    const float ax = 9.81F;
    std::memcpy(payload.data(), &ts, 8);
    std::memcpy(payload.data() + 8, &ax, 4);
    const auto s = aria_bridge::parse_imu_sample(payload, 0);
    CHECK(s.has_value());
    CHECK(s->timestamp_ns == 42);
    CHECK(s->accel[0] > 9.8F && s->accel[0] < 9.82F);
    // A third sample does not exist — must report that, not read past the end.
    CHECK(!aria_bridge::parse_imu_sample(payload, 2).has_value());

    // A torn batch (one and a half samples) yields the whole one and stops.
    payload.resize(aria_bridge::kImuSampleSize + 7);
    CHECK(aria_bridge::parse_imu_sample(payload, 0).has_value());
    CHECK(!aria_bridge::parse_imu_sample(payload, 1).has_value());
}

void test_consumer_constructs_and_times_out() {
    // PULL connects fine with nothing publishing; poll must simply time out
    // rather than block forever or throw.
    aria_bridge::Consumer consumer("tcp://127.0.0.1:5599");
    CHECK(consumer.endpoint() == "tcp://127.0.0.1:5599");
    const auto msg = consumer.poll(std::chrono::milliseconds{50});
    CHECK(!msg.has_value());
    CHECK(consumer.messages_received() == 0);
}

int run_unit_tests() {
    std::printf("unit tests\n");
    test_frame_header_round_trip();
    test_every_camera_id_maps();
    test_malformed_headers_are_rejected();
    test_sensor_header_and_imu_samples();
    test_consumer_constructs_and_times_out();
    return g_failures;
}

// Receives real frames from the Python mock receiver, which sends 1408x1408x3
// RGB frames whose blue channel is constant across the whole frame (it encodes
// the animation phase). That gives a content check that does not depend on the
// exact gradient: every blue byte in a frame must be identical.
int run_integration(const std::string& endpoint, int wanted) {
    std::printf("integration: %d frames from %s\n", wanted, endpoint.c_str());
    aria_bridge::Consumer consumer(endpoint);

    int got = 0;
    std::uint64_t previous_ts = 0;
    while (got < wanted) {
        const auto frame = consumer.poll_frame(std::chrono::milliseconds{5000});
        if (!frame) {
            std::printf("  FAIL timed out after %d frames\n", got);
            return 1;
        }
        CHECK(frame->header.camera == Camera::Rgb);
        CHECK(frame->header.channels == 3);
        CHECK(frame->pixels.size() == frame->header.expected_bytes());
        CHECK(frame->header.timestamp_ns > previous_ts);
        previous_ts = frame->header.timestamp_ns;

        // Blue channel constant across the frame.
        const auto& px = frame->pixels;
        const std::uint8_t blue = px[2];
        bool uniform = true;
        for (std::size_t i = 2; i < px.size(); i += 3) {
            if (px[i] != blue) { uniform = false; break; }
        }
        CHECK(uniform);
        ++got;
    }

    std::printf("  received %d frames, %llu messages, %llu dropped\n", got,
                static_cast<unsigned long long>(consumer.messages_received()),
                static_cast<unsigned long long>(consumer.messages_dropped()));
    return g_failures;
}

}  // namespace

int main(int argc, char** argv) {
    const std::string mode = argc > 1 ? argv[1] : "--unit";
    int rc = 0;
    if (mode == "--integration") {
        const std::string endpoint = argc > 2 ? argv[2] : std::string(aria_bridge::kDefaultEndpoint);
        const int frames = argc > 3 ? std::atoi(argv[3]) : 5;
        rc = run_integration(endpoint, frames);
    } else {
        rc = run_unit_tests();
    }
    std::printf("%s: %d checks, %d failures\n", rc == 0 ? "PASS" : "FAIL", g_checks, g_failures);
    return rc == 0 ? 0 : 1;
}
