import os
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from baidutransfer.gui import DesktopApp


def main():
    output = sys.argv[1] if len(sys.argv) > 1 else "docs/images/main-window.png"
    app = DesktopApp(demo=True, headless=True)
    print(app.capture(output))


if __name__ == "__main__":
    main()
