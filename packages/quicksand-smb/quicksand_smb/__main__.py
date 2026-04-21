"""Entry point: python -m quicksand_smb --config /path/to/config.json"""

import argparse
import logging
import sys

from . import SMBConfig, serve_stdio


def main() -> None:
    parser = argparse.ArgumentParser(description="quicksand SMB3 server (inetd/stdio)")
    parser.add_argument("--config", required=True, help="Path to JSON config file")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging to stderr")
    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(
            level=logging.DEBUG,
            stream=sys.stderr,
            format="%(name)s %(levelname)s: %(message)s",
        )

    config = SMBConfig.from_json_file(args.config)
    serve_stdio(config, config_path=args.config)


if __name__ == "__main__":
    main()
