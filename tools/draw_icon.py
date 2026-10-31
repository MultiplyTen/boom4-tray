"""Draw the local utility's original icon; no network or manufacturer artwork."""
from pathlib import Path
from PIL import Image, ImageDraw


def main():
    scale = 4
    image = Image.new('RGBA', (256 * scale, 256 * scale))
    draw = ImageDraw.Draw(image)

    def box(bounds, radius, fill):
        draw.rounded_rectangle(tuple(value * scale for value in bounds), radius * scale, fill=fill)

    def line(points, fill, width):
        draw.line(tuple((x * scale, y * scale) for x, y in points), fill=fill, width=width * scale)

    box((6, 6, 250, 250), 56, '#17233E')
    box((127, 39, 207, 215), 33, '#9585F7')
    box((45, 68, 128, 231), 33, '#56BFFF')
    box((137, 48, 197, 62), 7, '#C5B9FF')
    box((55, 77, 118, 91), 7, '#A8E5FF')
    for center_x, top in ((86, 110), (167, 84)):
        line(((center_x - 12, top + 15), (center_x + 12, top + 15)), '#F9FCFF', 7)
        line(((center_x, top + 3), (center_x, top + 27)), '#F9FCFF', 7)
        line(((center_x - 12, top + 55), (center_x + 12, top + 55)), '#F9FCFF', 7)
    for offset in (0, 1, 2):
        draw.ellipse(((73 + offset * 13) * scale, 205 * scale, (78 + offset * 13) * scale, 210 * scale), fill='#267BC2')
        draw.ellipse(((154 + offset * 13) * scale, 188 * scale, (159 + offset * 13) * scale, 193 * scale), fill='#6856BD')

    target = Path(__file__).resolve().parents[1] / 'assets' / 'boom4.ico'
    target.parent.mkdir(parents=True, exist_ok=True)
    image = image.resize((256, 256), Image.Resampling.LANCZOS)
    image.save(target, format='ICO', sizes=[(size, size) for size in (16, 24, 32, 48, 64, 128, 256)])
    print(target)


if __name__ == '__main__':
    main()
