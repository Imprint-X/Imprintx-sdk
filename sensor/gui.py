"""Display live ImprintX Sensor events; press q to close."""
import argparse
import os
import time

os.environ["QT_LOGGING_RULES"] = "*.warning=false"
import cv2
import numpy as np

from imprintx import SENSOR_HEIGHT, SENSOR_WIDTH
from imprintx.messaging import CONTROL_ENDPOINT, DATA_ENDPOINT, Control

from client import Client


def show_gui(data_endpoint=DATA_ENDPOINT, control_endpoint=CONTROL_ENDPOINT,
             display_fps=30.0):
    image = np.zeros((SENSOR_HEIGHT, SENSOR_WIDTH, 3), dtype=np.uint8)
    next_display = time.monotonic()

    with Client(data_endpoint, control_endpoint) as sensor:
        sensor.control(Control.SET_LIGHT, True)
        try:
            sensor.control(Control.START)
            while True:
                for event in sensor.receive():
                    image[event.y, event.x] = (
                        (0, 0, 255) if event.polarity else (255, 0, 0)
                    )

                now = time.monotonic()
                if now >= next_display:
                    cv2.imshow("ImprintX Sensor", image)
                    image.fill(0)
                    next_display = now + 1.0 / display_fps

                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
        finally:
            try:
                sensor.control(Control.STOP)
            finally:
                sensor.control(Control.SET_LIGHT, False)
                cv2.destroyAllWindows()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-endpoint", default=DATA_ENDPOINT)
    parser.add_argument("--control-endpoint", default=CONTROL_ENDPOINT)
    parser.add_argument("--display-fps", type=float, default=30.0)
    args = parser.parse_args()
    if not np.isfinite(args.display_fps) or args.display_fps <= 0:
        parser.error("--display-fps must be finite and positive")
    try:
        show_gui(args.data_endpoint, args.control_endpoint, args.display_fps)
    except KeyboardInterrupt:
        pass
