#!/usr/bin/env python3
"""Export the dashboard's static OpenAPI contract without entering its lifespan."""

from __future__ import annotations

import json

from butlers.api.app import create_app


def main() -> None:
    print(json.dumps(create_app().openapi(), sort_keys=True, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
