import argparse
import json
from .config import Settings
from .database import migrate, set_diagnostic_marker
from .status import get_status


def main():
    parser = argparse.ArgumentParser(description="Technische Betriebsdiagnose (keine Forschungsaktionen)")
    parser.add_argument("command", choices=["migrate", "status", "synthetic-marker"])
    parser.add_argument("value", nargs="?")
    args = parser.parse_args()
    settings = Settings.from_environment()
    if args.command == "migrate":
        migrate(settings)
    elif args.command == "synthetic-marker":
        if not args.value:
            parser.error("synthetic-marker benötigt einen synthetischen Prüfwert")
        set_diagnostic_marker(settings, args.value)
    else:
        print(json.dumps(get_status(settings), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
