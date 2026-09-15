"""Save one Touch Glove batch as PNG images and an optional grid."""
from __future__ import annotations

import datetime
import math
import os
from typing import Dict

import numpy as np
from imprintx import IMAGE_HEIGHT, IMAGE_WIDTH
from client import Client, Control, DATA_ENDPOINT, CONTROL_ENDPOINT
from client import argument_parser

def save_image(
    data_endpoint: str = DATA_ENDPOINT,
    control_endpoint: str = CONTROL_ENDPOINT,
    output_dir: str = ".",
    prefix: str = "tactile_snapshot",
    save_grid: bool = True,
    timeout_sec: float = 3.0,
) -> Dict[str, str]:
    if not math.isfinite(timeout_sec) or timeout_sec <= 0:
        raise ValueError("timeout_sec must be finite and greater than zero")

    import cv2

    try:
        os.makedirs(output_dir, exist_ok=True)
    except OSError as error:
        raise OSError(f"failed to create {output_dir}: {error}")
    timestamp_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    with Client(data_endpoint, control_endpoint) as glove:
        glove.control(Control.START)
        frames = list(glove.receive(timeout_sec))
        images = {int(frame.channel): frame.image for frame in frames}

        saved_paths: Dict[str, str] = {}
        for channel, image in images.items():
            filepath = os.path.join(output_dir, f"{prefix}_{timestamp_str}_ch{channel}.png")
            if not cv2.imwrite(filepath, image):
                raise OSError(f"failed to write {filepath}")
            saved_paths[f"ch{channel}"] = filepath

        if save_grid:
            canvas = np.zeros((IMAGE_HEIGHT * 2, IMAGE_WIDTH * 3), dtype=np.uint8)
            for channel, image in images.items():
                row, column = divmod(channel, 3)
                canvas[
                    row * IMAGE_HEIGHT : (row + 1) * IMAGE_HEIGHT,
                    column * IMAGE_WIDTH : (column + 1) * IMAGE_WIDTH,
                ] = image
            grid_path = os.path.join(output_dir, f"{prefix}_{timestamp_str}_grid.png")
            if not cv2.imwrite(grid_path, canvas):
                raise OSError(f"failed to write {grid_path}")
            saved_paths["grid"] = grid_path

        return saved_paths


if __name__ == "__main__":
    parser = argument_parser(__doc__)
    parser.add_argument("--output-dir", default=".")
    args = parser.parse_args()
    save_image(args.data_endpoint, args.control_endpoint, output_dir=args.output_dir)
    print(f"Images saved to {os.path.abspath(args.output_dir)}")
