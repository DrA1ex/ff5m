## Speed testing with variable block size
##
## Copyright (C) 2025, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import os
import platform
import sys
import time
import random

PROGRESS_BAR_LENGTH = 50
DEFAULT_FILE_BLOCK_SIZE = 1 * 1024 * 1024
TARGET_FILE_SIZE = 256 * 1024 * 1024

NO_PROGRESS = int(os.environ.get('NO_PROGRESS', "0"))


def print_progress_bar(label, current_iteration, total_iterations):
    if NO_PROGRESS:
        return

    filled_length = PROGRESS_BAR_LENGTH * current_iteration // total_iterations
    percentage = current_iteration * 100 // total_iterations

    bar = "=" * filled_length + " " * (PROGRESS_BAR_LENGTH - filled_length)
    sys.stdout.write(f"\r{label}: [{bar}] {percentage}%")
    sys.stdout.flush()


def generate_random_data(size):
    return os.urandom(size)


def test_disk_speed(file_path, block_size=16 * 1024, num_operations=None):
    if num_operations is None:
        num_operations = TARGET_FILE_SIZE // block_size

    if num_operations < 1:
        num_operations = 1

    block_size_in_kb = block_size / 1024
    i = 0
    count = num_operations * 2

    write_start_time = time.time()
    generate_duration = 0
    with open(file_path, 'rb+') as f:
        for _ in range(num_operations):
            random_block = random.randint(0, num_operations - 1)
            f.seek(random_block * block_size)

            gen_start = time.time()
            data_to_write = generate_random_data(block_size)
            generate_duration += time.time() - gen_start

            f.write(data_to_write)
            f.flush()

            i += 1
            print_progress_bar(f"Write Block-Size: {block_size_in_kb:.1f}KB", i, count)

    write_end_time = time.time()
    write_duration = write_end_time - write_start_time - generate_duration
    write_speed = (block_size * num_operations / write_duration) / (1024 * 1024)  # MB/s

    if platform.system() == "Linux":
        os.system("echo 3 > /proc/sys/vm/drop_caches")

    read_start_time = time.time()
    with open(file_path, 'rb') as f:
        for _ in range(num_operations):
            random_block = random.randint(0, num_operations - 1)
            f.seek(random_block * block_size)

            f.read(block_size)

            i += 1
            print_progress_bar(f"Read Block Size: {block_size_in_kb:.1f}KB", i, count)

    if not NO_PROGRESS: sys.stdout.write("\033[1K\r")
    sys.stdout.write(f"Block Size {block_size_in_kb:.1f}KB:\n")
    sys.stdout.flush()

    read_end_time = time.time()
    read_duration = read_end_time - read_start_time
    read_speed = (block_size * num_operations / read_duration) / (1024 * 1024)  # MB/s

    print(f"Random write speed: {write_speed:.2f} MB/s")
    print(f"Random read speed: {read_speed:.2f} MB/s\n")


base_path = "/data"
if len(sys.argv) >= 2:
    base_path = sys.argv[1]

if len(sys.argv) >= 3:
    TARGET_FILE_SIZE = int(sys.argv[2]) * 1024 * 1024

if not os.path.exists(base_path):
    sys.stderr.write(f"Path \"{base_path}\" doesn't exists!\n")
    exit(1)

path_stats = os.statvfs(base_path)
path_free_space = path_stats.f_frsize * path_stats.f_bavail
if path_free_space <= TARGET_FILE_SIZE:
    sys.stderr.write(
        f"Path \"{base_path}\" should have at least {TARGET_FILE_SIZE / 1024 / 1024}MB"
        f"but got {path_free_space / 1024 / 1024}MB!\n")
    exit(2)

print("Speed test started. Please be patient!\n")

file_path = os.path.join(base_path, f"speed_test.{random.randint(int(1e6), int(1e7 - 1))}")
print(f"Using temporary test file \"{file_path}\" of size {TARGET_FILE_SIZE / 1024 / 1024}MB\n")

try:
    with open(file_path, 'wb') as f:
        block_count = TARGET_FILE_SIZE // DEFAULT_FILE_BLOCK_SIZE
        create_start_time = time.time()
        for i in range(block_count):
            f.write(generate_random_data(DEFAULT_FILE_BLOCK_SIZE))
            print_progress_bar("Generating test file", i, block_count)

        if not NO_PROGRESS: sys.stdout.write("\033[1K\r")
        sys.stdout.write(
            f"Sequential write speed: "
            f"{(TARGET_FILE_SIZE / 1024 / 1024 / (time.time() - create_start_time)):0.2f}MB/S\n\n"
        )

    with open(file_path, 'rb') as f:
        block_count = TARGET_FILE_SIZE // DEFAULT_FILE_BLOCK_SIZE
        create_start_time = time.time()
        for i in range(block_count):
            f.read(DEFAULT_FILE_BLOCK_SIZE)
            print_progress_bar("Reading test file", i, block_count)
        if not NO_PROGRESS: sys.stdout.write("\033[1K\r")
        sys.stdout.write(
            f"Sequential read speed: "
            f"{(TARGET_FILE_SIZE / 1024 / 1024 / (time.time() - create_start_time)):0.2f}MB/S\n\n"
        )

    test_disk_speed(file_path, block_size=1 * 1024, num_operations=4000)
    test_disk_speed(file_path, block_size=4 * 1024, num_operations=4000)
    test_disk_speed(file_path, block_size=16 * 1024, num_operations=2000)
    test_disk_speed(file_path, block_size=32 * 1024, num_operations=1000)
    test_disk_speed(file_path, block_size=1024 * 1024, num_operations=50)

    print("\n Done!")
except KeyboardInterrupt:
    print("\nAborted!")
finally:
    if os.path.exists(file_path):
        os.remove(file_path)
