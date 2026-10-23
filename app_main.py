import os
import sys


def run():
    if "--screenshot" in sys.argv:
        index = sys.argv.index("--screenshot")
        output = sys.argv[index + 1]
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
        from baidutransfer.gui import DesktopApp

        DesktopApp(demo=True, headless=True).capture(output)
        return 0

    from baidutransfer.gui import main

    return main()


if __name__ == "__main__":
    raise SystemExit(run())
