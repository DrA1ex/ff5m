// 3D cube rotation demo project
//
// Copyright (C) 2025, Alexander K <https://github.com/drA1ex>
//
// This file may be distributed under the terms of the GNU GPLv3 license

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring> // For memset
#include <fcntl.h>
#include <fstream>
#include <iostream>
#include <unistd.h>
#include <linux/fb.h>
#include <sys/ioctl.h>
#include <sys/mman.h> // For mmap, munmap, and related constants

constexpr float PI = 3.141592653589793238462643383279502884197f;

constexpr int WIDTH = 800;
constexpr int HEIGHT = 480;

constexpr float SIZE = 600;

constexpr int POINTS_CNT = 4;

struct Point3D {
    float x, y, z;
};

struct Point2D {
    int x, y;
};

struct Surface {
    int indexes[POINTS_CNT];
    float depth = 0;
    uint8_t color = 0;
};

void rotateX(Point3D &p, float angle) {
    float rad = angle * PI / 180.0f;
    float y = p.y * std::cos(rad) - p.z * std::sin(rad);
    float z = p.y * std::sin(rad) + p.z * std::cos(rad);
    p.y = y;
    p.z = z;
}

void rotateY(Point3D &p, float angle) {
    float rad = angle * PI / 180.0f;
    float x = p.x * std::cos(rad) + p.z * std::sin(rad);
    float z = -p.x * std::sin(rad) + p.z * std::cos(rad);
    p.x = x;
    p.z = z;
}

void rotateZ(Point3D &p, float angle) {
    float rad = angle * PI / 180.0f;
    float x = p.x * std::cos(rad) - p.y * std::sin(rad);
    float y = p.x * std::sin(rad) + p.y * std::cos(rad);
    p.x = x;
    p.y = y;
}

Point2D project(Point3D p) {
    // Add a small offset to z to avoid division by zero or near-zero values
    float z_offset = 5.0f;
    return {
        (int) (p.x * SIZE / (p.z + z_offset) + WIDTH / 2),
        (int) (p.y * SIZE / (p.z + z_offset) + HEIGHT / 2)
    };
}

void fillPolygon(uint32_t *buffer, const Point2D *points, int numPoints, uint32_t color) {
    // Determine the vertical bounds of the polygon
    int minY = HEIGHT, maxY = 0;
    for (int i = 0; i < numPoints; i++) {
        if (points[i].y < minY) minY = points[i].y;
        if (points[i].y > maxY) maxY = points[i].y;
    }

    minY = std::max(0, minY);
    maxY = std::min(HEIGHT - 1, maxY);

    // Iterate over each scanline within the vertical bounds
    for (int y = minY; y <= maxY; y++) {
        int iCount = 0;
        int intersections[numPoints];

        // Find intersections of the polygon edges with the current scanline
        for (int i = 0; i < numPoints; i++) {
            int next = (i + 1) % numPoints;
            int x1 = points[i].x;
            int x2 = points[next].x;
            int y1 = points[i].y;
            int y2 = points[next].y;

            // Skip horizontal edges
            if (y1 == y2) continue;

            // Ensure y1 <= y2 for consistent intersection calculation
            if (y1 > y2) {
                std::swap(y1, y2);
                std::swap(x1, x2);
            }

            // Check if the scanline intersects the edge
            if (y >= y1 && y < y2) {
                // Compute intersection point's x-coordinate
                intersections[iCount++] = x1 + (y - y1) * (x2 - x1) / (y2 - y1);
            }
        }

        // Sort the intersection points
        std::ranges::sort(intersections, intersections + iCount);

        // Fill pixels between pairs of intersections
        for (size_t i = 0; i + 1 < iCount; i += 2) {
            int x1 = std::max(0, intersections[i]);
            int x2 = std::min(WIDTH - 1, intersections[i + 1]);

            uint32_t *row = buffer + y * WIDTH;
            std::fill(row + x1, row + x2 + 1, color);
        }
    }
}

float calculateSurfaceDepth(const Point3D cube[], const int surfaceIndices[4]) {
    float depth = 0.0f;
    for (int i = 0; i < POINTS_CNT; i++) {
        depth += cube[surfaceIndices[i]].z;
    }
    return depth / POINTS_CNT;
}

int main() {
    // Open the framebuffer
    int fbfd = open("/dev/fb0", O_RDWR);
    if (fbfd == -1) {
        std::cerr << "Error: cannot open framebuffer device." << std::endl;
        return 1;
    }

    // Map the framebuffer into memory
    char *fbp = (char *) mmap(nullptr, WIDTH * HEIGHT * 4, PROT_READ | PROT_WRITE, MAP_SHARED, fbfd, 0);
    if (fbp == MAP_FAILED) {
        std::cerr << "Error: failed to map framebuffer device to memory." << std::endl;
        close(fbfd);
        return 1;
    }

    // Create a back buffer
    auto *buffer = new uint32_t[WIDTH * HEIGHT];

    Point3D originalFigure[] = {
        {-1.f, -1.f, -1.f},
        {1.f, -1.f, -1.f},
        {1.f, 1.f, -1.f},
        {-1.f, 1.f, -1.f},
        {-1.f, -1.f, 1.f},
        {1.f, -1.f, 1.f},
        {1.f, 1.f, 1.f},
        {-1.f, 1.f, 1.f}
    };


    uint32_t colors[] = {
        0xFFFF0000, 0xFF00FF00, 0xFF0000FF,
        0xFFFFFF00, 0xFFFF00FF, 0xFF00FFFF
    };

    uint8_t cSize = std::size(colors);
    uint8_t index = 0;

    Surface surfaces[] = {
        {{0, 1, 2, 3}, 0.f, (uint8_t) (++index % cSize)}, // Front
        {{4, 5, 6, 7}, 0.f, (uint8_t) (++index % cSize)}, // Back
        {{0, 1, 5, 4}, 0.f, (uint8_t) (++index % cSize)}, // Bottom
        {{2, 3, 7, 6}, 0.f, (uint8_t) (++index % cSize)}, // Top
        {{0, 3, 7, 4}, 0.f, (uint8_t) (++index % cSize)}, // Left
        {{1, 2, 6, 5}, 0.f, (uint8_t) (++index % cSize)}  // Right
    };

    float angleX = 0, angleY = 0, angleZ = 0;

    while (true) {
        // Clear the back buffer
        memset(buffer, 0, WIDTH * HEIGHT * 4);

        Point3D figure[std::size(originalFigure)];
        memcpy(figure, originalFigure, sizeof(originalFigure));

        // Rotate the figure
        for (auto &p: figure) {
            rotateX(p, angleX);
            rotateY(p, angleY);
            rotateZ(p, angleZ);
        }

        // Sort surfaces by depth
        for (auto &surface: surfaces) {
            surface.depth = calculateSurfaceDepth(figure, surface.indexes);
        }

        std::ranges::sort(surfaces, [](const auto &a, const auto &b) {
            return a.depth > b.depth;
        });

        // Draw the cube faces
        for (auto &surface: surfaces) {
            const auto &indexes = surface.indexes;

            Point2D projectedPoints[POINTS_CNT];
            for (int j = 0; j < POINTS_CNT; j++) {
                projectedPoints[j] = project(figure[indexes[j]]);
            }

            fillPolygon(buffer, projectedPoints, POINTS_CNT, colors[surface.color]);
        }

        // Copy the back buffer to the framebuffer
        memcpy(fbp, buffer, WIDTH * HEIGHT * 4);

        // Increment rotation angles
        angleX += 1.5;
        angleY += 0.8;
        angleZ -= 0.5;

        usleep(1000000 / 60);
    }

    // Clean up
    delete[] buffer;
    munmap(fbp, WIDTH * HEIGHT * 4);
    close(fbfd);
    return 0;
}
