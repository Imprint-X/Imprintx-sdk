"""Print ImprintX Sensor events published by the SDK process."""
import argparse
import math
import time

from client import Client
from imprintx.messaging import CONTROL_ENDPOINT, DATA_ENDPOINT, Control


def stream(data_endpoint=DATA_ENDPOINT, control_endpoint=CONTROL_ENDPOINT, duration=None):
    if duration is not None and (not math.isfinite(duration) or duration <= 0):
        raise ValueError("duration must be finite and positive")
    with Client(data_endpoint, control_endpoint) as client:
        client.control(Control.START)
        try:
            started = time.monotonic()
            while duration is None or time.monotonic() - started < duration:
                yield client.receive()
        finally:
            client.control(Control.STOP)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-endpoint", default=DATA_ENDPOINT)
    parser.add_argument("--control-endpoint", default=CONTROL_ENDPOINT)
    parser.add_argument("--duration", type=float)
    args = parser.parse_args()
    try:
        for batch in stream(args.data_endpoint, args.control_endpoint, args.duration):
            print(f"batch={batch.batch_id} time={batch.timestamp_us}us "
                  f"events={len(batch)}", flush=True)
    except KeyboardInterrupt:
        pass
