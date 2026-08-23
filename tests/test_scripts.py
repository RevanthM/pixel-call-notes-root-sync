import importlib.util
import io
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


extractor = load("extract_init_boot", "scripts/extract_init_boot.py")


class FactoryImageExtractionTests(TestCase):
    def test_extracts_init_boot_from_nested_image_zip(self):
        image = io.BytesIO()
        with zipfile.ZipFile(image, "w") as nested:
            nested.writestr("init_boot.img", b"synthetic-init-boot")

        with TemporaryDirectory() as temporary:
            factory = Path(temporary) / "factory.zip"
            with zipfile.ZipFile(factory, "w") as outer:
                outer.writestr("image-synthetic.zip", image.getvalue())

            self.assertEqual(
                extractor.extract(factory),
                b"synthetic-init-boot",
            )
