import logging
import mimetypes
import os
from pathlib import Path

from pydantic_ai.messages import BinaryContent, ToolReturn

from shellbot2.tools.util import classproperty

logger = logging.getLogger(__name__)


# Supported image extensions and their canonical media types. Used as a
# fallback when ``mimetypes`` does not recognize an extension.
_IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}


class ImageReader:
    """Reads a local image file and returns it as base64-encoded binary
    content suitable for vision-capable models.
    """

    @property
    def name(self):
        return "image-reader"

    @classproperty
    def toolname(cls):
        return "image-reader"

    @property
    def description(self):
        return (
            "This function reads a local image file from disk and returns its "
            "contents as base64-encoded binary data with the appropriate media "
            "type so that the image can be viewed by a vision-capable model. "
            "Provide a relative or absolute path to a local image file (png, "
            "jpg/jpeg, gif, webp, bmp, or tiff)."
        )

    @property
    def parameters(self):
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative or absolute path to a local image file to load.",
                },
            },
            "required": ["path"],
        }

    @staticmethod
    def _resolve_media_type(path: Path) -> str:
        ext = path.suffix.lower()
        if ext in _IMAGE_MEDIA_TYPES:
            return _IMAGE_MEDIA_TYPES[ext]
        guessed, _ = mimetypes.guess_type(str(path))
        if guessed and guessed.startswith("image/"):
            return guessed
        raise ValueError(
            f"Unsupported image file extension '{ext}' for path {path}. "
            f"Supported extensions: {sorted(_IMAGE_MEDIA_TYPES)}"
        )

    def __call__(self, **kwargs):
        path_arg = kwargs.get("path")
        if not path_arg:
            return f"The function {self.name} was expecting a 'path' keyword argument, but didn't get one"

        path = Path(os.path.expanduser(path_arg))
        if not path.exists():
            return f"Image file not found: {path}"
        if not path.is_file():
            return f"Path is not a file: {path}"

        media_type = self._resolve_media_type(path)
        logger.info(f"Reading image {path} as {media_type}")

        with open(path, "rb") as image_file:
            image_bytes = image_file.read()

        binary_content = BinaryContent(data=image_bytes, media_type=media_type)
        summary = (
            f"Loaded image {path} ({media_type}, {len(image_bytes)} bytes). "
            f"The image is attached as base64-encoded binary content."
        )
        return ToolReturn(return_value=summary, content=[summary, binary_content])


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m shellbot2.tools.imagereader <image_path>")
        sys.exit(1)
    tool = ImageReader()
    result = tool(path=sys.argv[1])
    print(result)
