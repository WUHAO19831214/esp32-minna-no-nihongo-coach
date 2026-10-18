"""Small DFN1 display emulator for ESP-Mosaico (480x480)."""
import struct
from pathlib import Path
from PIL import Image, ImageDraw


class BitmapFont:
    def __init__(self, path):
        self.data = Path(path).read_bytes()
        magic, self.height, _, _, _, count, fallback = struct.unpack_from('<4shhhHII', self.data)
        assert magic == b'DFN1'
        self.glyphs = {}
        self.bitmap_start = 20 + count * 18
        for index in range(count):
            code, offset, advance, x, y, width, height = struct.unpack_from('<IIhhhHH', self.data, 20 + index * 18)
            mask = Image.new('1', (width, height))
            stride = (width + 7) // 8
            for row in range(height):
                for col in range(width):
                    byte = self.data[self.bitmap_start + offset + row * stride + col // 8]
                    if byte & (0x80 >> (col % 8)):
                        mask.putpixel((col, row), 1)
            self.glyphs[code] = (advance, x, y, mask)
        self.fallback = fallback

    def close(self):
        pass


class Screen:
    def __init__(self):
        self.im = Image.new('RGB', (480, 480))
        self.d = ImageDraw.Draw(self.im)

    @staticmethod
    def color(value):
        return ((value >> 16) & 255, (value >> 8) & 255, value & 255)

    def begin(self, options):
        self.d.rectangle((0, 0, 479, 479), fill=self.color(options['clear']))

    def present(self):
        pass

    def text(self, x, y, value, options):
        font = options['font']
        for char in str(value):
            advance, dx, dy, mask = font.glyphs.get(ord(char), font.glyphs[font.fallback])
            self.im.paste(self.color(options['color']), (int(x + dx), int(y + dy)), mask)
            x += advance

    def fill_rect(self, x, y, w, h, color):
        assert all(isinstance(v, int) for v in (x, y, w, h)), 'Device requires Lua integer coordinates'
        self.d.rectangle((x, y, x + w - 1, y + h - 1), fill=self.color(color))

    def fill_round_rect(self, x, y, w, h, radius, color):
        assert all(isinstance(v, int) for v in (x, y, w, h, radius)), 'Device requires Lua integer coordinates'
        self.d.rounded_rectangle((x, y, x + w - 1, y + h - 1), radius=radius, fill=self.color(color))

    def fill_circle(self, x, y, radius, color):
        assert all(isinstance(v, int) for v in (x, y, radius)), 'Device requires Lua integer coordinates'
        self.d.ellipse((x - radius, y - radius, x + radius, y + radius), fill=self.color(color))

    def line(self, x1, y1, x2, y2, color):
        assert all(isinstance(v, int) for v in (x1, y1, x2, y2)), 'Device requires Lua integer coordinates'
        self.d.line((x1, y1, x2, y2), fill=self.color(color))
