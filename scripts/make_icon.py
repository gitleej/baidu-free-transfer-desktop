import os
import sys

from PIL import Image, ImageDraw


def main():
    output = sys.argv[1] if len(sys.argv) > 1 else "assets/app.ico"
    os.makedirs(os.path.dirname(output), exist_ok=True)
    size = 256
    image = Image.new("RGBA", (size, size), "#0b1220")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((32, 32, 224, 224), radius=48, fill="#172a46")
    draw.rounded_rectangle((43, 82, 213, 202), radius=24, fill="#4f7cff")
    draw.polygon(((88, 70), (125, 70), (144, 92), (88, 92)), fill="#6f98ff")
    draw.line((83, 153, 164, 153), fill="white", width=15)
    draw.polygon(((164, 124), (198, 153), (164, 182)), fill="white")
    image.save(output, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    image.save(os.path.join(os.path.dirname(output), "app.png"))
    print(os.path.abspath(output))


if __name__ == "__main__":
    main()
