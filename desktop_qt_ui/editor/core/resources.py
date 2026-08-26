"""Editor image resource records."""

import time
from dataclasses import dataclass, field
from typing import Any

from PIL import Image


@dataclass(slots=True)
class ImageResource:
    """Eagerly decoded image held by the shared image LRU."""

    path: str
    image: Image.Image
    width: int
    height: int
    load_time: float = field(default_factory=time.time)
    last_access: float = field(default_factory=time.time)
    qimage: Any = None

    def touch(self) -> None:
        self.last_access = time.time()

    def release(self) -> None:
        # The active EditorDocument can still own the same PIL object. Dropping
        # this cache reference must therefore never close the image.
        self.image = None
        self.qimage = None
