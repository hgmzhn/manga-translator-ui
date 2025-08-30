from .kumikolib import Kumiko
import tempfile, cv2, os
from ..generic import imwrite_unicode

def get_panels_from_array(img_rgb, rtl=True, logger=None):

    tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
    path = tmp.name
    tmp.close()  

    # Always use the unicode-safe writer.
    # If no logger is passed, create a default one.
    if not logger:
        import logging
        logger = logging.getLogger(__name__)
    imwrite_unicode(path, img_rgb, logger)

    k = Kumiko({'rtl': rtl})
    k.parse_image(path)
    infos = k.get_infos()

    os.unlink(path)

    return infos[0]['panels']