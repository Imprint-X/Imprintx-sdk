"""Publish a Touch Glove control word and wait for its SDK execution result."""
from client import Client, Control
from client import argument_parser


if __name__ == "__main__":
    parser = argument_parser(__doc__)
    parser.add_argument("command", choices=[command.name.lower() for command in Control])
    parser.add_argument("--value", help="value for SET_*_REPORTING or SET_DEVICE_SYNC_STATE")
    args = parser.parse_args()
    command = Control[args.command.upper()]
    value = args.value
    if command in (Control.SET_IMAGE_REPORTING, Control.SET_IMU_REPORTING):
        normalized = (value or "").lower()
        if normalized not in ("0", "1", "false", "true"):
            parser.error("--value must be 0/1 or false/true for reporting controls")
        value = normalized in ("1", "true")
    elif command == Control.SET_DEVICE_SYNC_STATE:
        if value is None:
            parser.error("--value is required for set_device_sync_state")
        try:
            value = int(value, 0)
        except ValueError:
            parser.error("--value must be an integer such as 0x10")
    with Client(args.data_endpoint, args.control_endpoint, subscribe_images=False) as client:
        print(client.control(command, value))
