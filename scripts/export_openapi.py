#!/usr/bin/env python3
"""Export the dashboard OpenAPI schema for the generator's isolated Python process."""

import json
import sys

from butlers.api.app import create_app


def main() -> None:
    json.dump(create_app().openapi(), sys.stdout, sort_keys=True, separators=(",", ":"))
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
