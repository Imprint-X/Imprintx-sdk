"""Display live Touch Glove images and FPS; press q to close."""
from __future__ import annotations

import math
import os
import time
from typing import Optional

import numpy as np
from imprintx import IMAGE_HEIGHT, IMAGE_WIDTH
from client import Client, Control, DATA_ENDPOINT, CONTROL_ENDPOINT, StreamDiscontinuityError
from client import argument_parser


def show_gui(
    data_endpoint: str = DATA_ENDPOINT,
    control_endpoint: str = CONTROL_ENDPOINT,
    window_name: str = "Touch Glove Grid Display",
    display_fps: float = 60.0,
    stream_timeout: float = 2.0,
) -> None:
    if not math.isfinite(display_fps) or display_fps <= 0:
        raise ValueError("display_fps must be finite and greater than zero")
    if not math.isfinite(stream_timeout) or stream_timeout <= 0:
        raise ValueError("stream_timeout must be finite and greater than zero")

    os.environ["QT_LOGGING_RULES"] = "*.warning=false"
    import cv2

    with Client(data_endpoint, control_endpoint) as glove:
        glove.control(Control.START)

        canvas = np.zeros((IMAGE_HEIGHT * 2, IMAGE_WIDTH * 3), dtype=np.uint8)
        render_interval = 1.0 / display_fps
        next_render = time.monotonic()
        data_fps = 0.0
        last_batch_id: Optional[int] = None
        last_timestamp_us: Optional[int] = None

        cv_threads = cv2.getNumThreads()
        cv2.setNumThreads(1)
        try:
            cv2.namedWindow(window_name, cv2.WINDOW_AUTOSIZE)
            while True:
                try:
                    batch = glove.receive(stream_timeout)
                except StreamDiscontinuityError:
                    last_batch_id = None
                    last_timestamp_us = None
                    continue
                if last_timestamp_us is not None:
                    data_fps = (batch.batch_id - last_batch_id) * 1_000_000.0 / (
                        batch.timestamp_us - last_timestamp_us)
                last_batch_id = batch.batch_id
                last_timestamp_us = batch.timestamp_us

                now = time.monotonic()
                if now >= next_render:
                    canvas.fill(0)
                    for frame in batch:
                        channel, image = frame.channel, frame.image
                        row, column = divmod(channel, 3)
                        tile = canvas[
                            row * IMAGE_HEIGHT : (row + 1) * IMAGE_HEIGHT,
                            column * IMAGE_WIDTH : (column + 1) * IMAGE_WIDTH,
                        ]
                        np.copyto(tile, image)
                    status_y, status_x = IMAGE_HEIGHT, IMAGE_WIDTH * 2
                    canvas[status_y:, status_x:] = 20
                    cv2.putText(
                        canvas,
                        f"FPS: {data_fps:.1f}",
                        (status_x + 15, status_y + 30),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.5,
                        255,
                        1,
                    )
                    cv2.imshow(window_name, canvas)
                    next_render = now + render_interval

                if (cv2.waitKey(1) & 0xFF == ord("q")
                        or cv2.getWindowProperty(window_name, cv2.WND_PROP_VISIBLE) < 1):
                    break
        finally:
            cv2.destroyAllWindows()
            cv2.setNumThreads(cv_threads)


if __name__ == "__main__":
    parser = argument_parser(__doc__)
    parser.add_argument("--display-fps", type=float, default=60.0)
    args = parser.parse_args()
    show_gui(args.data_endpoint, args.control_endpoint, display_fps=args.display_fps)
