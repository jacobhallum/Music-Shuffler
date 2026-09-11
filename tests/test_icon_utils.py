import unittest

from PIL import Image

from icon_utils import make_icon_images


def _solid(size, color, mode='RGB'):
    return Image.new(mode, size, color=color)


class TestMakeIconImages(unittest.TestCase):
    def test_returns_one_image_per_requested_size(self):
        src = _solid((200, 200), (255, 0, 0))
        result = make_icon_images(src, sizes=(16, 32, 48))
        self.assertEqual(len(result), 3)
        self.assertEqual([im.size for im in result], [(16, 16), (32, 32), (48, 48)])

    def test_output_is_rgba(self):
        src = _solid((100, 100), (0, 255, 0), mode='RGB')
        result = make_icon_images(src, sizes=(16,))
        self.assertEqual(result[0].mode, 'RGBA')

    def test_square_source_is_not_distorted(self):
        # A square source resized down should keep pixels uniform (no stretch artifacts
        # possible to detect trivially) - verify size only, distortion-free by construction.
        src = _solid((300, 300), (10, 20, 30))
        result = make_icon_images(src, sizes=(48,))
        self.assertEqual(result[0].size, (48, 48))

    def test_landscape_source_center_crops_to_square(self):
        # 300 wide x 100 tall: left third red, middle third green, right third blue.
        # A center-crop-to-square (using the shorter side, 100) should keep only the
        # middle 100px-wide vertical strip - i.e. the crop should be entirely green,
        # with red/blue edges excluded.
        src = Image.new('RGB', (300, 100))
        pixels = src.load()
        for x in range(300):
            for y in range(100):
                if x < 100:
                    pixels[x, y] = (255, 0, 0)
                elif x < 200:
                    pixels[x, y] = (0, 255, 0)
                else:
                    pixels[x, y] = (0, 0, 255)

        result = make_icon_images(src, sizes=(50,))
        out = result[0]
        self.assertEqual(out.size, (50, 50))
        # Every pixel in the cropped+resized output should be pure green (crop excluded
        # the red/blue thirds entirely) - sample corners and center.
        for x, y in ((0, 0), (49, 0), (0, 49), (49, 49), (25, 25)):
            r, g, b, a = out.getpixel((x, y))
            self.assertEqual((r, g, b), (0, 255, 0), f"pixel {(x, y)} was {(r, g, b)}")

    def test_portrait_source_center_crops_to_square(self):
        # 100 wide x 300 tall: top third red, middle third green, bottom third blue.
        src = Image.new('RGB', (100, 300))
        pixels = src.load()
        for x in range(100):
            for y in range(300):
                if y < 100:
                    pixels[x, y] = (255, 0, 0)
                elif y < 200:
                    pixels[x, y] = (0, 255, 0)
                else:
                    pixels[x, y] = (0, 0, 255)

        result = make_icon_images(src, sizes=(50,))
        out = result[0]
        for x, y in ((0, 0), (49, 0), (0, 49), (49, 49), (25, 25)):
            r, g, b, a = out.getpixel((x, y))
            self.assertEqual((r, g, b), (0, 255, 0), f"pixel {(x, y)} was {(r, g, b)}")

    def test_default_sizes_used_when_not_specified(self):
        src = _solid((64, 64), (1, 2, 3))
        result = make_icon_images(src)
        self.assertEqual([im.size for im in result], [(16, 16), (32, 32), (48, 48)])


if __name__ == '__main__':
    unittest.main()
