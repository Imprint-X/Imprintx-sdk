"""Read an ImprintX sensor SN over a serial port."""
import argparse

from imprintx.sensor import Sensor


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port", help="sensor serial port, for example /dev/ttyACM0")
    args = parser.parse_args()
    with Sensor(args.port) as sensor:
        print(sensor.get_sn())
