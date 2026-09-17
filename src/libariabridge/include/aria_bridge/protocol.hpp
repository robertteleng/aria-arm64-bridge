// Wire protocol v2 / v2.1 — the C++ side of what aria_arm64_bridge/protocol.py
// defines. Pure parsing: no sockets, no allocation, no dependencies beyond the
// standard library, so it can be unit-tested without a running receiver.
//
// One PUSH socket carries two message kinds, each a 2-part multipart message
// [header, payload], told apart by the 4-byte magic:
//
//   ARI2  frame   28-byte header: magic[4] cam(1) pad[3] ts(8) w(4) h(4) ch(4)
//                 payload: w*h*ch bytes, row-major uint8
//   ARS1  sensor  12-byte header: magic[4] id(1) pad[3] count(4)
//                 payload: count fixed-size samples, layout per sensor
//
// All integers are little-endian. Fields are read with memcpy from explicit
// offsets rather than by casting a packed struct: the sender is Python struct,
// and a compiler that pads differently would silently misread every frame.
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <optional>
#include <span>
#include <string_view>

namespace aria_bridge {

inline constexpr std::string_view kFrameMagic = "ARI2";
inline constexpr std::string_view kSensorMagic = "ARS1";
inline constexpr std::size_t kFrameHeaderSize = 28;
inline constexpr std::size_t kSensorHeaderSize = 12;
inline constexpr std::string_view kDefaultEndpoint = "tcp://127.0.0.1:5555";

enum class Camera : std::uint8_t { Rgb = 0, Eye = 1, Slam1 = 2, Slam2 = 3 };
enum class Sensor : std::uint8_t { Imu1 = 0, Imu2 = 1, Mag = 2, Baro = 3 };

constexpr std::string_view to_string(Camera c) noexcept {
    switch (c) {
        case Camera::Rgb:   return "rgb";
        case Camera::Eye:   return "eye";
        case Camera::Slam1: return "slam1";
        case Camera::Slam2: return "slam2";
    }
    return "unknown";
}

constexpr std::string_view to_string(Sensor s) noexcept {
    switch (s) {
        case Sensor::Imu1: return "imu1";
        case Sensor::Imu2: return "imu2";
        case Sensor::Mag:  return "mag";
        case Sensor::Baro: return "baro";
    }
    return "unknown";
}

struct FrameHeader {
    Camera camera{};
    std::uint64_t timestamp_ns{};
    std::uint32_t width{};
    std::uint32_t height{};
    std::uint32_t channels{};

    // Bytes the payload must contain for this header to be consistent.
    [[nodiscard]] constexpr std::size_t expected_bytes() const noexcept {
        return static_cast<std::size_t>(width) * height * channels;
    }
};

struct SensorHeader {
    Sensor sensor{};
    std::uint32_t sample_count{};
};

// One IMU sample: timestamp + accel (m/s^2) + gyro (rad/s).
struct ImuSample {
    std::int64_t timestamp_ns{};
    std::array<float, 3> accel{};
    std::array<float, 3> gyro{};
};

inline constexpr std::size_t kImuSampleSize = 32;   // <q6f
inline constexpr std::size_t kMagSampleSize = 20;   // <q3f
inline constexpr std::size_t kBaroSampleSize = 16;  // <q2f

namespace detail {
template <typename T>
[[nodiscard]] inline T read_le(std::span<const std::uint8_t> buf, std::size_t offset) noexcept {
    T value{};
    std::memcpy(&value, buf.data() + offset, sizeof(T));
    return value;  // x86_64 and aarch64 are both little-endian; the bridge runs on both
}

[[nodiscard]] inline bool has_magic(std::span<const std::uint8_t> buf,
                                    std::string_view magic) noexcept {
    return buf.size() >= magic.size() &&
           std::memcmp(buf.data(), magic.data(), magic.size()) == 0;
}
}  // namespace detail

[[nodiscard]] inline bool is_frame_message(std::span<const std::uint8_t> header) noexcept {
    return detail::has_magic(header, kFrameMagic);
}

[[nodiscard]] inline bool is_sensor_message(std::span<const std::uint8_t> header) noexcept {
    return detail::has_magic(header, kSensorMagic);
}

// Returns nullopt when the header is short, has the wrong magic, or names a
// camera id the protocol does not define. A malformed message is dropped, never
// trusted: the payload length check that follows depends on these fields.
[[nodiscard]] inline std::optional<FrameHeader> parse_frame_header(
    std::span<const std::uint8_t> buf) noexcept {
    if (buf.size() < kFrameHeaderSize || !is_frame_message(buf)) return std::nullopt;
    const auto cam_id = detail::read_le<std::uint8_t>(buf, 4);
    if (cam_id > static_cast<std::uint8_t>(Camera::Slam2)) return std::nullopt;
    FrameHeader h{};
    h.camera = static_cast<Camera>(cam_id);
    h.timestamp_ns = detail::read_le<std::uint64_t>(buf, 8);
    h.width = detail::read_le<std::uint32_t>(buf, 16);
    h.height = detail::read_le<std::uint32_t>(buf, 20);
    h.channels = detail::read_le<std::uint32_t>(buf, 24);
    if (h.width == 0 || h.height == 0 || h.channels == 0) return std::nullopt;
    return h;
}

[[nodiscard]] inline std::optional<SensorHeader> parse_sensor_header(
    std::span<const std::uint8_t> buf) noexcept {
    if (buf.size() < kSensorHeaderSize || !is_sensor_message(buf)) return std::nullopt;
    const auto id = detail::read_le<std::uint8_t>(buf, 4);
    if (id > static_cast<std::uint8_t>(Sensor::Baro)) return std::nullopt;
    SensorHeader h{};
    h.sensor = static_cast<Sensor>(id);
    h.sample_count = detail::read_le<std::uint32_t>(buf, 8);
    return h;
}

// Decode IMU samples in place. Trailing bytes that do not form a whole sample
// are ignored: a torn batch costs samples, never a crash.
[[nodiscard]] inline std::optional<ImuSample> parse_imu_sample(
    std::span<const std::uint8_t> payload, std::size_t index) noexcept {
    const std::size_t offset = index * kImuSampleSize;
    if (payload.size() < offset + kImuSampleSize) return std::nullopt;
    ImuSample s{};
    s.timestamp_ns = detail::read_le<std::int64_t>(payload, offset);
    for (std::size_t i = 0; i < 3; ++i) {
        s.accel[i] = detail::read_le<float>(payload, offset + 8 + i * 4);
        s.gyro[i] = detail::read_le<float>(payload, offset + 20 + i * 4);
    }
    return s;
}

}  // namespace aria_bridge
