"""Print synchronized Touch Glove data published by the SDK process."""
from __future__ import annotations

import math
import time

from client import (
    Client, Control, DATA_ENDPOINT, CONTROL_ENDPOINT, StreamDiscontinuityError,
    argument_parser,
)


def stream(data_endpoint=DATA_ENDPOINT, control_endpoint=CONTROL_ENDPOINT, duration=None):
    if duration is not None and (not math.isfinite(duration) or duration <= 0):
        raise ValueError("duration must be finite and positive")
    with Client(data_endpoint, control_endpoint) as client:
        client.control(Control.START)
        started = time.monotonic()
        while duration is None or time.monotonic() - started < duration:
            try:
                batch = client.receive()
            except StreamDiscontinuityError:
                continue
            yield batch


if __name__ == "__main__":
    parser = argument_parser(__doc__)
    parser.add_argument("--duration", type=float)
    args = parser.parse_args()
    try:
        for batch in stream(args.data_endpoint, args.control_endpoint, args.duration):
            print(f"batch={batch.batch_id} time={batch.timestamp_us}us "
                  f"channels={[frame.channel for frame in batch]}", flush=True)
    except KeyboardInterrupt:
        pass
