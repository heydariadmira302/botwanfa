import argparse
import importlib


def main() -> None:
    parser = argparse.ArgumentParser(prog="botwanfa")
    parser.add_argument("service", choices=("bot", "scheduler", "worker", "sender"))
    args = parser.parse_args()
    module = importlib.import_module(f"botwanfa.apps.{args.service}")
    module.main()


if __name__ == "__main__":
    main()
