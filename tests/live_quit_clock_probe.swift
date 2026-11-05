// Clock-only fixture used to confirm Python/Swift CLOCK_MONOTONIC coordinates.
import Darwin
import Foundation

var sample = timespec()
guard clock_gettime(CLOCK_MONOTONIC, &sample) == 0,
      sample.tv_sec >= 0, sample.tv_nsec >= 0 else {
    fputs("CLOCK_MONOTONIC unavailable\n", stderr)
    exit(1)
}
let nanoseconds = UInt64(sample.tv_sec) * 1_000_000_000 + UInt64(sample.tv_nsec)
print(nanoseconds)
