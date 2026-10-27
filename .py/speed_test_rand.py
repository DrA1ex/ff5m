## Speed testing with variable block size
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
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
_drop_caches_warning_shown = False


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


def drop_caches(file_path=None):
    """Flush pending writes and evict cached file data before a storage test."""
    global _drop_caches_warning_shown

    if platform.system() != "Linux":
        return False

    os.sync()

    try:
        with open("/proc/sys/vm/drop_caches", "w") as f:
            f.write("3\n")
        return True
    except OSError as global_exc:
        # Non-root environments often cannot use /proc/sys/vm/drop_caches.
        # Fall back to evicting just the test file from page cache when the
        # platform exposes posix_fadvise().
        if (
            file_path
            and hasattr(os, "posix_fadvise")
            and hasattr(os, "POSIX_FADV_DONTNEED")
        ):
            try:
                with open(file_path, "rb", buffering=0) as f:
                    os.posix_fadvise(
                        f.fileno(), 0, 0, os.POSIX_FADV_DONTNEED
                    )
                return True
            except OSError:
                pass

        if not _drop_caches_warning_shown:
            sys.stderr.write(
                f"Warning: unable to drop filesystem caches ({global_exc}). "
                "Read results may be affected by page cache.\n"
            )
            _drop_caches_warning_shown = True
        return False


def make_random_blocks(file_path, block_size, num_operations):
    file_size = os.path.getsize(file_path)
    block_count = file_size // block_size

    if block_count < 1:
        raise ValueError(
            f"Test file ({file_size} bytes) is smaller than block size ({block_size} bytes)"
        )

    if num_operations is None:
        num_operations = block_count

    num_operations = max(1, min(num_operations, block_count))

    # Use unique blocks spread across the whole test file. This avoids repeated
    # accesses to the same block being served from cache during one test pass.
    return random.sample(range(block_count), num_operations)


def test_disk_speed(file_path, block_size=16 * 1024, num_operations=None):
    write_blocks = make_random_blocks(file_path, block_size, num_operations)
    read_blocks = make_random_blocks(file_path, block_size, len(write_blocks))
    num_operations = len(write_blocks)

    block_size_in_kb = block_size / 1024
    i = 0
    count = num_operations * 2

    # Start every random-write test from a clean cache state so that a previous
    # read test does not influence the next block-size test.
    drop_caches(file_path)

    # Generate the payload before starting the timer.  Do not generate data
    # between buffered writes and subtract that time afterwards: Linux may be
    # flushing dirty pages in parallel while Python is in os.urandom(), so
    # subtracting generation time would also subtract real storage work.
    data_to_write = generate_random_data(block_size)

    write_start_time = time.perf_counter()
    with open(file_path, 'rb+', buffering=0) as f:
        for random_block in write_blocks:
            f.seek(random_block * block_size)
            f.write(data_to_write)

            i += 1
            print_progress_bar(f"Write Block-Size: {block_size_in_kb:.1f}KB", i, count)

        # Wait until the complete write pass has reached stable storage.
        # Random I/O is opened unbuffered, so Python's own read/write buffer
        # cannot distort small-block measurements.
        os.fsync(f.fileno())

    write_end_time = time.perf_counter()
    write_duration = write_end_time - write_start_time
    write_speed = (block_size * num_operations / write_duration) / (1024 * 1024)  # MB/s

    # The write pass has just populated page cache, so explicitly evict it
    # before measuring random reads.
    drop_caches(file_path)

    read_start_time = time.perf_counter()
    bytes_read = 0
    with open(file_path, 'rb', buffering=0) as f:
        if hasattr(os, 'posix_fadvise') and hasattr(os, 'POSIX_FADV_RANDOM'):
            os.posix_fadvise(f.fileno(), 0, 0, os.POSIX_FADV_RANDOM)

        for random_block in read_blocks:
            f.seek(random_block * block_size)
            data = f.read(block_size)
            bytes_read += len(data)

            i += 1
            print_progress_bar(f"Read Block Size: {block_size_in_kb:.1f}KB", i, count)

    read_end_time = time.perf_counter()

    if not NO_PROGRESS:
        sys.stdout.write("\033[1K\r")
    sys.stdout.write(f"Block Size {block_size_in_kb:.1f}KB:\n")
    sys.stdout.flush()

    read_duration = read_end_time - read_start_time
    read_speed = (bytes_read / read_duration) / (1024 * 1024)  # MB/s

    print(f"Random write speed: {write_speed:.2f} MB/s")
    print(f"Random read speed: {read_speed:.2f} MB/s\n")


def main():
    target_size = TARGET_FILE_SIZE
    base_path = "/data"
    if len(sys.argv) >= 2:
        base_path = sys.argv[1]

    if len(sys.argv) >= 3:
        target_size = int(sys.argv[2]) * 1024 * 1024

    if not os.path.exists(base_path):
        sys.stderr.write(f"Path \"{base_path}\" doesn't exists!\n")
        exit(1)

    path_stats = os.statvfs(base_path)
    path_free_space = path_stats.f_frsize * path_stats.f_bavail
    if path_free_space <= target_size:
        sys.stderr.write(
            f"Path \"{base_path}\" should have at least {target_size / 1024 / 1024}MB"
            f"but got {path_free_space / 1024 / 1024}MB!\n")
        exit(2)

    print("Speed test started. Please be patient!\n")

    file_path = os.path.join(base_path, f"speed_test.{random.randint(int(1e6), int(1e7 - 1))}")
    print(f"Using temporary test file \"{file_path}\" of size {target_size / 1024 / 1024}MB\n")

    try:
        block_count = target_size // DEFAULT_FILE_BLOCK_SIZE
        bytes_written = 0

        # Prepare data outside the timed section. Reusing a random 1 MiB payload is
        # fine for an eMMC/filesystem throughput test and avoids mixing RNG cost
        # with I/O without incorrectly subtracting overlapping writeback time.
        data = generate_random_data(DEFAULT_FILE_BLOCK_SIZE)

        create_start_time = time.perf_counter()
        with open(file_path, 'wb') as f:
            for i in range(block_count):
                bytes_written += f.write(data)
                print_progress_bar("Generating test file", i + 1, block_count)

            f.flush()
            os.fsync(f.fileno())

        create_end_time = time.perf_counter()
        create_duration = create_end_time - create_start_time

        if not NO_PROGRESS:
            sys.stdout.write("\033[1K\r")
        sys.stdout.write(
            f"Sequential write speed: "
            f"{(bytes_written / 1024 / 1024 / create_duration):0.2f}MB/S\n\n"
        )

        # Do not let the sequential read consume pages cached by file creation.
        drop_caches(file_path)

        bytes_read = 0
        read_start_time = time.perf_counter()
        with open(file_path, 'rb') as f:
            if hasattr(os, 'posix_fadvise') and hasattr(os, 'POSIX_FADV_SEQUENTIAL'):
                os.posix_fadvise(f.fileno(), 0, 0, os.POSIX_FADV_SEQUENTIAL)

            for i in range(block_count):
                data = f.read(DEFAULT_FILE_BLOCK_SIZE)
                bytes_read += len(data)
                print_progress_bar("Reading test file", i + 1, block_count)
        read_end_time = time.perf_counter()

        if not NO_PROGRESS:
            sys.stdout.write("\033[1K\r")
        sys.stdout.write(
            f"Sequential read speed: "
            f"{(bytes_read / 1024 / 1024 / (read_end_time - read_start_time)):0.2f}MB/S\n\n"
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


if __name__ == "__main__":
    main()
