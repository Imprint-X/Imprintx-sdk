"""Load the Touch Glove timestamp sidecar of a recorded MP4."""
import argparse
import json
from pathlib import Path


def load_video_timestamps(path):
    path = Path(path)
    if path.suffix.lower() != ".json":
        stem = path.stem if path.suffix.lower() == ".mp4" else path.name
        path = path.with_name(stem + "_timestamps.json")
    with path.open(encoding="utf-8") as file:
        return json.load(file)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path")
    args = parser.parse_args()
    print(json.dumps(load_video_timestamps(args.path), indent=2))
