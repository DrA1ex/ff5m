#include <cstddef>

#include "../framebuffer.h"
#include "test_runner.h"


namespace {

constexpr uint32_t WIDTH = 800;
constexpr uint32_t HEIGHT = 480;
constexpr std::size_t FRAME_BYTES =
    static_cast<std::size_t>(WIDTH) * HEIGHT * sizeof(uint32_t);

typer::framebuffer::Geometry valid_geometry() {
    return {
        .width = WIDTH,
        .height = HEIGHT,
        .virtual_width = WIDTH,
        .virtual_height = HEIGHT * 2,
        .x_offset = 0,
        .y_offset = 0,
        .bits_per_pixel = 32,
        .line_length = WIDTH * sizeof(uint32_t),
        .memory_length = FRAME_BYTES * 2,
    };
}

void visible_page_zero() {
    const auto plan = typer::framebuffer::select_buffers(
        valid_geometry(), WIDTH, HEIGHT);
    TYPER_CHECK(plan.use_framebuffer_backbuffer);
    TYPER_CHECK(plan.mapping_length == FRAME_BYTES * 2);
    TYPER_CHECK(plan.front_offset == 0);
    TYPER_CHECK(plan.back_offset == FRAME_BYTES);
    TYPER_CHECK(plan.fallback_reason.empty());
}

void visible_page_one() {
    auto geometry = valid_geometry();
    geometry.y_offset = HEIGHT;
    const auto plan = typer::framebuffer::select_buffers(
        geometry, WIDTH, HEIGHT);
    TYPER_CHECK(plan.use_framebuffer_backbuffer);
    TYPER_CHECK(plan.front_offset == FRAME_BYTES);
    TYPER_CHECK(plan.back_offset == 0);
}

void one_page_falls_back() {
    auto geometry = valid_geometry();
    geometry.virtual_height = HEIGHT;
    geometry.memory_length = FRAME_BYTES;
    const auto plan = typer::framebuffer::select_buffers(
        geometry, WIDTH, HEIGHT);
    TYPER_CHECK(!plan.use_framebuffer_backbuffer);
    TYPER_CHECK(plan.mapping_length == FRAME_BYTES);
    TYPER_CHECK(!plan.fallback_reason.empty());
}

void invalid_bpp_falls_back() {
    auto geometry = valid_geometry();
    geometry.bits_per_pixel = 16;
    const auto plan = typer::framebuffer::select_buffers(
        geometry, WIDTH, HEIGHT);
    TYPER_CHECK(!plan.use_framebuffer_backbuffer);
    TYPER_CHECK(plan.mapping_length == FRAME_BYTES);
}

void invalid_stride_falls_back() {
    auto geometry = valid_geometry();
    geometry.line_length += 64;
    const auto plan = typer::framebuffer::select_buffers(
        geometry, WIDTH, HEIGHT);
    TYPER_CHECK(!plan.use_framebuffer_backbuffer);
}

void insufficient_memory_falls_back() {
    auto geometry = valid_geometry();
    geometry.memory_length = FRAME_BYTES * 2 - 1;
    const auto plan = typer::framebuffer::select_buffers(
        geometry, WIDTH, HEIGHT);
    TYPER_CHECK(!plan.use_framebuffer_backbuffer);
}

void invalid_offset_falls_back() {
    auto geometry = valid_geometry();
    geometry.y_offset = 1;
    const auto plan = typer::framebuffer::select_buffers(
        geometry, WIDTH, HEIGHT);
    TYPER_CHECK(!plan.use_framebuffer_backbuffer);

    geometry = valid_geometry();
    geometry.x_offset = 1;
    const auto horizontal = typer::framebuffer::select_buffers(
        geometry, WIDTH, HEIGHT);
    TYPER_CHECK(!horizontal.use_framebuffer_backbuffer);
}

void invalid_dimensions_fall_back() {
    auto geometry = valid_geometry();
    geometry.width = WIDTH - 1;
    TYPER_CHECK(!typer::framebuffer::select_buffers(
        geometry, WIDTH, HEIGHT).use_framebuffer_backbuffer);

    geometry = valid_geometry();
    geometry.virtual_width = WIDTH * 2;
    TYPER_CHECK(!typer::framebuffer::select_buffers(
        geometry, WIDTH, HEIGHT).use_framebuffer_backbuffer);
}

} // namespace

int main(int argc, char **argv) {
    return typer::test::run(argc, argv, {
        {"visible_page_zero", visible_page_zero},
        {"visible_page_one", visible_page_one},
        {"one_page_falls_back", one_page_falls_back},
        {"invalid_bpp_falls_back", invalid_bpp_falls_back},
        {"invalid_stride_falls_back", invalid_stride_falls_back},
        {"insufficient_memory_falls_back", insufficient_memory_falls_back},
        {"invalid_offset_falls_back", invalid_offset_falls_back},
        {"invalid_dimensions_fall_back", invalid_dimensions_fall_back},
    });
}
