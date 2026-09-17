// Minimal native consumer: print what arrives on the bridge socket.
//
//   ./consume_frames [endpoint]
//
// Try it without glasses:
//   python3 -m aria_arm64_bridge.mock_receiver --fps 15 --width 640 --height 480
//
// This is where a CUDA pipeline would start: `frame->pixels` is a contiguous
// borrowed buffer, so it feeds cudaMemcpyAsync directly, no Python and no
// intermediate copy.
#include <chrono>
#include <cstdio>
#include <string>

#include "aria_bridge/consumer.hpp"

int main(int argc, char** argv) {
    const std::string endpoint =
        argc > 1 ? argv[1] : std::string(aria_bridge::kDefaultEndpoint);

    aria_bridge::Consumer consumer(endpoint);
    std::printf("connected to %s — Ctrl+C to stop\n", endpoint.c_str());

    const auto started = std::chrono::steady_clock::now();
    std::uint64_t frames = 0;

    while (true) {
        const auto msg = consumer.poll(std::chrono::milliseconds{1000});
        if (!msg) {
            std::printf("...waiting\n");
            continue;
        }

        if (const auto* frame = std::get_if<aria_bridge::FrameView>(&*msg)) {
            ++frames;
            const auto elapsed = std::chrono::duration<double>(
                std::chrono::steady_clock::now() - started).count();
            std::printf("frame %-6llu %-6s %ux%ux%u  %.2f MB  %.1f fps\n",
                        static_cast<unsigned long long>(frames),
                        std::string(to_string(frame->header.camera)).c_str(),
                        frame->header.width, frame->header.height,
                        frame->header.channels,
                        static_cast<double>(frame->pixels.size()) / (1024.0 * 1024.0),
                        elapsed > 0 ? static_cast<double>(frames) / elapsed : 0.0);
        } else if (const auto* sensor = std::get_if<aria_bridge::SensorView>(&*msg)) {
            std::printf("sensor %-5s %u samples\n",
                        std::string(to_string(sensor->header.sensor)).c_str(),
                        sensor->header.sample_count);
        }
    }
}
