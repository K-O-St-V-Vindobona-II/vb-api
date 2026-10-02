"""Inspection of uploaded profile and gallery images."""

import io
from dataclasses import dataclass

from PIL import Image as PILImage

# Pillow format name -> content type. These are the only formats an upload may
# be decoded as. Image.open() would otherwise try every format Pillow knows
# (EPS, JPEG 2000, PDF, ...), whatever content type the client declared, and
# each of those decoders is attack surface for a file that any member can send.
IMAGE_TYPE_BY_FORMAT = {"JPEG": "image/jpeg", "PNG": "image/png"}


@dataclass(frozen=True)
class UploadedImage:
    content_type: str
    width: int
    height: int


def inspect_upload_image(content: bytes) -> UploadedImage:
    """Type and size of an uploaded JPEG or PNG, taken from the bytes.

    The content type comes from what the file is, never from the header the
    client sent. Raises OSError or ValueError for everything else, including a
    file whose pixel count exceeds Pillow's decompression-bomb limit.
    """
    try:
        with PILImage.open(
            io.BytesIO(content), formats=list(IMAGE_TYPE_BY_FORMAT)
        ) as image:
            content_type = IMAGE_TYPE_BY_FORMAT.get(image.format or "")
            if content_type is None:
                msg = "Unsupported image format."
                raise ValueError(msg)
            width, height = image.size
    except PILImage.DecompressionBombError as error:
        msg = "The image has too many pixels."
        raise ValueError(msg) from error
    return UploadedImage(content_type, width, height)
