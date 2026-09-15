"""Record ImprintX Sensor event batches to JSON for a fixed duration."""

import argparse
import json
import math
import os
import time
import uuid
from pathlib import Path

from client import Client
from imprintx.messaging import CONTROL_ENDPOINT, DATA_ENDPOINT, Control


def record(
    data_endpoint=DATA_ENDPOINT,
    control_endpoint=CONTROL_ENDPOINT,
    output_path="sensor_events.json",
    duration=5.0,
):
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("duration must be finite and positive")
    output = Path(output_path)
    if output.suffix.lower() != ".json":
        raise ValueError("output_path must end in .json")
    if output.exists():
        raise FileExistsError(f"Output already exists: {output}")
    partial = output.with_name(f".{output.stem}.{uuid.uuid4().hex}.partial.json")
    batch_count = 0
    event_count = 0
    try:
        with partial.open("x", encoding="utf-8") as file:
            with Client(data_endpoint, control_endpoint) as sensor:
                file.write("[")
                sensor.control(Control.START)
                started = time.monotonic()
                try:
                    while True:
                        remaining = duration - (time.monotonic() - started)
                        if remaining <= 0:
                            break
                        try:
                            batch = sensor.receive(min(2.0, remaining))
                        except TimeoutError:
                            if time.monotonic() - started >= duration:
                                break
                            raise
                        record = {
                            "session": batch.session,
                            "batch_id": batch.batch_id,
                            "timestamp_us": batch.timestamp_us,
                            "events": [
                                [event.x, event.y, event.polarity, event.timestamp_us]
                                for event in batch
                            ],
                        }
                        file.write(",\n" if batch_count else "\n")
                        json.dump(record, file, separators=(",", ":"))
                        batch_count += 1
                        event_count += len(batch)
                finally:
                    sensor.control(Control.STOP)
                file.write("\n]\n")
        os.link(partial, output)
    finally:
        partial.unlink(missing_ok=True)
    return {
        "output_path": str(output),
        "batch_count": batch_count,
        "event_count": event_count,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-endpoint", default=DATA_ENDPOINT)
    parser.add_argument("--control-endpoint", default=CONTROL_ENDPOINT)
    parser.add_argument("--output", default="sensor_events.json")
    parser.add_argument("--duration", type=float, default=5.0)
    args = parser.parse_args()
    result = record(args.data_endpoint, args.control_endpoint, args.output, args.duration)
    print(f"Recording saved to {result['output_path']}")
