from __future__ import annotations

from .governance import generate_transform_key


def main() -> None:
    print(generate_transform_key())


if __name__ == "__main__":
    main()
