import functools
import os
from typing import Callable, List, Optional, Tuple

import cv2
import numpy as np
import tqdm
from PIL import Image, ImageOps

# 解除 PIL 图片大小限制（防止 DecompressionBombWarning）
# 可通过环境变量 PIL_MAX_IMAGE_PIXELS 自定义，设为 0 表示无限制
_max_pixels = os.environ.get('PIL_MAX_IMAGE_PIXELS', '0')
Image.MAX_IMAGE_PIXELS = int(_max_pixels) if _max_pixels != '0' else None

# 注册 HEIC/HEIF 格式支持（iPhone 默认图片格式）
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass  # pillow-heif 未安装时静默跳过

import hashlib
import json
import re
import sys
import unicodedata

import einops
import requests
from shapely import affinity
from shapely.geometry import MultiPoint, Polygon

try:
    functools.cached_property
except AttributeError: # Supports Python versions below 3.8
    from backports.cached_property import cached_property
    functools.cached_property = cached_property

MODULE_PATH = os.path.dirname(os.path.realpath(__file__))
EXIF_ORIENTATION_TAG = 274

# PyInstaller compatibility: use sys._MEIPASS if available
if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    # Running in PyInstaller bundle
    BASE_PATH = sys._MEIPASS
else:
    # Running in normal Python environment
    BASE_PATH = os.path.abspath(os.path.join(MODULE_PATH, '..', '..'))

# Adapted from argparse.Namespace
class Context(dict):
    def __init__(self, **kwargs):
        for name in kwargs:
            setattr(self, name, kwargs[name])
    
    def __getattr__(self, item):
        return self.get(item)
    
    def __delattr__(self, key) -> None:
        return self.__delitem__(key)

    def __setattr__(self, key, value):
        return self.__setitem__(key, value)

    def __getstate__(self):
        return self.copy()

    def __setstate__(self, state):
        self.update(state)

    def __eq__(self, other):
        if not isinstance(other, Context):
            return NotImplemented
        return dict(self) == dict(other)

    def __contains__(self, key):
        return key in self.keys()
    
    def __repr__(self):
        type_name = type(self).__name__
        arg_strings = []
        star_args = {}
        for arg in self._get_args():
            arg_strings.append(repr(arg))
        for name, value in self._get_kwargs():
            if name.isidentifier():
                arg_strings.append('%s=%r' % (name, value))
            else:
                star_args[name] = value
        if star_args:
            arg_strings.append('**%s' % repr(star_args))
        return '%s(%s)' % (type_name, ', '.join(arg_strings))

    def _get_kwargs(self):
        return list(self.items())

    def _get_args(self):
        return []

# TODO: Add TranslationContext for type linting

def atoi(text: str) -> int | str:
    return int(text) if text.isdigit() else text

def natural_sort(l: List[str]):
    return sorted(l, key=lambda text: [atoi(c) for c in re.split(r'(\d+)', text)])

def repeating_sequence(s: str):
    """Extracts repeating sequence from string. Example: 'abcabca' -> 'abc'."""
    for i in range(1, len(s) // 2 + 1):
        seq = s[:i]
        if seq * (len(s)//len(seq)) + seq[:len(s)%len(seq)] == s:
            return seq
    return s

def is_whitespace(ch):
    """Checks whether `chars` is a whitespace character."""
    # \t, \n, and \r are technically control characters but we treat them
    # as whitespace since they are generally considered as such.
    if ch == " " or ch == "\t" or ch == "\n" or ch == "\r" or ord(ch) == 0:
        return True
    cat = unicodedata.category(ch)
    if cat == "Zs":
        return True
    return False

def is_control(ch):
    """Checks whether `chars` is a control character."""
    # These are technically control characters but we count them as whitespace
    # characters.
    if ch == "\t" or ch == "\n" or ch == "\r":
        return False
    cat = unicodedata.category(ch)
    if cat in ("Cc", "Cf"):
        return True
    return False

def is_punctuation(ch):
    """Checks whether `chars` is a punctuation character."""
    cp = ord(ch)
    # We treat all non-letter/number ASCII as punctuation.
    # Characters such as "^", "$", and "`" are not in the Unicode
    # Punctuation class but we treat them as punctuation anyways, for
    # consistency.
    if ((cp >= 33 and cp <= 47) or (cp >= 58 and cp <= 64) or
        (cp >= 91 and cp <= 96) or (cp >= 123 and cp <= 126)):
        return True
    cat = unicodedata.category(ch)
    if cat.startswith("P"):
        return True
    return False

def is_valuable_char(ch):
    # return re.search(r'[^\d\W]', ch)
    return not is_punctuation(ch) and not is_control(ch) and not is_whitespace(ch) and not ch.isdigit()

def is_valuable_text(text):
    for ch in text:
        if is_valuable_char(ch):
            return True
    return False

def count_valuable_text(text: str) -> int:
    return sum([1 for ch in text if is_valuable_char(ch)])

def is_right_to_left_char(ch):
    """Checks whether the char belongs to a right to left alphabet."""
    # Arabic (from https://stackoverflow.com/a/49346768)
    if ('\u0600' <= ch <= '\u06FF' or
        '\u0750' <= ch <= '\u077F' or
        '\u08A0' <= ch <= '\u08FF' or
        '\uFB50' <= ch <= '\uFDFF' or
        '\uFE70' <= ch <= '\uFEFF' or
        '\U00010E60' <= ch <= '\U00010E7F' or
        '\U0001EE00' <= ch <= '\U0001EEFF'):
        return True
    return False

def replace_prefix(s: str, old: str, new: str):
    if s.startswith(old):
        s = new + s[len(old):]
    return s

def chunks(lst, n):
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i:i+n]

def get_digest(file_path: str) -> str:
    h = hashlib.sha256()
    BUF_SIZE = 65536

    with open(file_path, 'rb') as file:
        while True:
            # Reading is buffered, so we can read smaller chunks.
            chunk = file.read(BUF_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()

def get_image_md5(image) -> str:
    """计算PIL Image对象的MD5哈希值，确保相同图片内容产生相同的哈希值"""
    import io

    try:
        # 将PIL Image转换为字节数据进行MD5计算
        img_byte_arr = io.BytesIO()
        # 统一转换为RGB格式以确保一致性
        if hasattr(image, 'mode') and image.mode != 'RGB':
            image = image.convert('RGB')
        image.save(img_byte_arr, format='PNG')
        img_bytes = img_byte_arr.getvalue()

        # 计算MD5哈希值
        h = hashlib.md5()
        h.update(img_bytes)
        return h.hexdigest()[:8]  # 只取前8位，避免文件夹名过长
    except Exception:
        # 如果计算失败，返回基于时间戳的fallback值
        import time
        return f"fallback_{int(time.time() * 1000)}"

def _preserve_runtime_image_attrs(src: Image.Image, dst: Image.Image) -> Image.Image:
    for attr in ('name', 'filename', 'format'):
        try:
            value = getattr(src, attr, None)
            if value is not None:
                setattr(dst, attr, value)
        except Exception:
            pass
    try:
        if getattr(dst, 'name', None) is None and getattr(dst, 'filename', None):
            dst.name = dst.filename
    except Exception:
        pass
    return dst

def normalize_pil_image(img: Image.Image, eager: bool = False, apply_exif: bool = True) -> Image.Image:
    """
    统一处理 PIL Image 的方向信息，并按需立即加载像素数据。
    """
    if apply_exif:
        # Pillow 的 PNG getexif() 会触发整图解码；没有 EXIF 块时不要为了方向信息
        # 提前付出这笔成本。JPEG/TIFF 仍然正常读取方向，带 EXIF 的其他格式也保留。
        fmt = (getattr(img, 'format', '') or '').upper()
        has_exif_payload = bool(getattr(img, 'info', {}).get('exif'))
        if fmt in {'JPEG', 'JPG', 'TIFF'} or has_exif_payload:
            try:
                orientation = int(img.getexif().get(EXIF_ORIENTATION_TAG, 1) or 1)
            except Exception:
                orientation = 1
            if orientation != 1:
                normalized = ImageOps.exif_transpose(img)
                normalized = _preserve_runtime_image_attrs(img, normalized)
                if eager:
                    normalized.load()
                return normalized
    if eager:
        img.load()
    return img

def open_pil_image(source, eager: bool = False, apply_exif: bool = True) -> Image.Image:
    """
    统一打开图片入口。

    eager=True: 立即解码，适合文件句柄会马上关闭的场景。
    eager=False: 尽量懒加载，只在需要 EXIF 方向修正时提前解码。
    """
    image = Image.open(source)
    try:
        if getattr(image, 'name', None) is None and getattr(image, 'filename', None):
            image.name = image.filename
    except Exception:
        pass
    normalized = normalize_pil_image(image, eager=eager, apply_exif=apply_exif)
    if normalized is not image:
        try:
            image.close()
        except Exception:
            pass
    return normalized

def get_filename_from_url(url: str, default: str = '') -> str:
    m = re.search(r'/([^/?]+)[^/]*$', url)
    if m:
        return m.group(1)
    return default

def download_url_with_progressbar(url: str, path: str, min_speed_kbps: float = 100, speed_check_interval: int = 10, timeout: int = 30):
    """
    下载文件并显示进度条
    
    Args:
        url: 下载链接
        path: 保存路径
        min_speed_kbps: 最低速度要求（KB/s），低于此速度会抛出异常
        speed_check_interval: 速度检查间隔（秒）
        timeout: 连接超时时间（秒）
    """
    if os.path.basename(path) in ('.', '') or os.path.isdir(path):
        new_filename = get_filename_from_url(url)
        if not new_filename:
            raise Exception('Could not determine filename')
        path = os.path.join(path, new_filename)

    headers = {}
    downloaded_size = 0
    if os.path.isfile(path):
        downloaded_size = os.path.getsize(path)
        headers['Range'] = 'bytes=%d-' % downloaded_size
        headers['Accept-Encoding'] = 'deflate'

    r = requests.get(url, stream=True, allow_redirects=True, headers=headers, timeout=timeout)
    # Resume safety: some servers ignore Range requests and return 200/full body.
    # In that case appending would corrupt the file, so restart from scratch.
    if downloaded_size:
        accept_ranges = (r.headers.get('Accept-Ranges') or '').lower()
        content_range = r.headers.get('Content-Range')
        if accept_ranges != 'bytes' or r.status_code != 206 or not content_range:
            print('Error: Webserver does not reliably support partial downloads. Restarting from the beginning.')
            r.close()
            r = requests.get(url, stream=True, allow_redirects=True, timeout=timeout)
            downloaded_size = 0
    total = int(r.headers.get('content-length', 0))
    chunk_size = 1024

    if r.ok:
        with tqdm.tqdm(
            desc=os.path.basename(path),
            initial=downloaded_size,
            total=total+downloaded_size,
            unit='iB',
            unit_scale=True,
            unit_divisor=chunk_size,
        ) as bar:
            with open(path, 'ab' if downloaded_size else 'wb') as f:
                is_tty = sys.stdout.isatty()
                downloaded_chunks = 0
                
                # 速度监控变量
                import time
                last_check_time = time.time()
                last_check_size = downloaded_size
                
                for data in r.iter_content(chunk_size=chunk_size):
                    size = f.write(data)
                    bar.update(size)
                    downloaded_size += size

                    # Fallback for non TTYs so output still shown
                    downloaded_chunks += 1
                    if not is_tty and downloaded_chunks % 1000 == 0:
                        print(bar)
                    
                    # 速度检查：每隔一定时间检查一次下载速度
                    current_time = time.time()
                    elapsed = current_time - last_check_time
                    if elapsed >= speed_check_interval:
                        downloaded_in_interval = downloaded_size - last_check_size
                        speed_kbps = (downloaded_in_interval / 1024) / elapsed
                        
                        # 如果速度太慢，抛出异常以切换到备用链接
                        if speed_kbps < min_speed_kbps:
                            r.close()
                            raise Exception(
                                f'Download speed too slow: {speed_kbps:.1f} KB/s '
                                f'(minimum required: {min_speed_kbps} KB/s)'
                            )
                        
                        # 更新检查点
                        last_check_time = current_time
                        last_check_size = downloaded_size
        
        # 检查下载的文件大小，如果太小可能是下载失败（如404页面）
        final_size = os.path.getsize(path)
        if final_size < 1024:  # 小于1KB认为下载失败
            os.remove(path)
            raise Exception(f'Downloaded file is too small ({final_size} bytes), possibly a 404 page: "{url}"')
    else:
        raise Exception(f'Couldn\'t resolve url: "{url}" (Error: {r.status_code})')

def prompt_yes_no(query: str, default: bool = None) -> bool:
    s = '%s (%s/%s): ' % (query, 'Y' if default is True else 'y', 'N' if default is False else 'n')
    while True:
        inp = input(s).lower()
        if inp in ('yes', 'y'):
            return True
        elif inp in ('no', 'n'):
            return False
        elif default is not None:
            return default
        if inp:
            print('Error: Please answer with "y" or "n"')

class AvgMeter():
    def __init__(self):
        self.reset()

    def reset(self):
        self.sum = 0
        self.count = 0

    def __call__(self, val = None):
        if val is not None:
            self.sum += val
            self.count += 1
        if self.count > 0:
            return self.sum / self.count
        else:
            return 0

def load_image(img: Image.Image) -> Tuple[np.ndarray, Optional[Image.Image]]:
    """
    将 PIL Image 转换为 RGB numpy 数组，并提取 alpha 通道（如果有）。
    
    Args:
        img: PIL.Image.Image 对象
        
    Returns:
        Tuple[np.ndarray, Optional[Image.Image]]: RGB 数组和 alpha 通道
    """
    img = normalize_pil_image(img, eager=False)
    if img.mode == 'RGBA':
        # from https://stackoverflow.com/questions/9166400/convert-rgba-png-to-rgb-with-pil
        img.load()  # needed for split()
        background = Image.new('RGB', img.size, (255, 255, 255))
        alpha_ch = img.split()[3]
        background.paste(img, mask=alpha_ch)  # 3 is the alpha channel
        return np.array(background), alpha_ch
    elif img.mode == 'P':
        img = img.convert('RGBA')
        img.load()  # needed for split()
        background = Image.new('RGB', img.size, (255, 255, 255))
        alpha_ch = img.split()[3]
        background.paste(img, mask=alpha_ch)  # 3 is the alpha channel
        return np.array(background), alpha_ch
    else:
        return np.array(img.convert('RGB')), None

def dump_image(img_pil: Image.Image, img: np.ndarray, alpha_ch: Image.Image = None):
    img_pil = normalize_pil_image(img_pil, eager=False)
    # 用于 paste 的 mask，可能需要调整尺寸
    mask_for_paste = alpha_ch
    
    if alpha_ch is not None:
        if img.shape[2] != 4 :
            # 将 alpha 通道转换为 numpy 数组
            alpha_array = np.array(alpha_ch).astype(np.uint8)
            
            # 检查尺寸是否匹配，如果不匹配则调整 alpha 通道尺寸
            if alpha_array.shape[0] != img.shape[0] or alpha_array.shape[1] != img.shape[1]:
                # 使用 PIL 调整 alpha 通道尺寸以匹配 img
                alpha_ch_resized = alpha_ch.resize((img.shape[1], img.shape[0]), Image.LANCZOS)
                alpha_array = np.array(alpha_ch_resized).astype(np.uint8)
                mask_for_paste = alpha_ch_resized  # 更新用于 paste 的 mask
            
            img = np.concatenate([img.astype(np.uint8), alpha_array[..., None]], axis = 2)
    else:
        img = img.astype(np.uint8)
    result = img_pil.convert('RGBA').resize((img.shape[1], img.shape[0]))
    result.paste(Image.fromarray(img), mask = mask_for_paste)
    return result


def _infer_pil_save_format(output_path: str, format: Optional[str] = None) -> str:
    if format:
        return format.upper()

    ext = os.path.splitext(output_path)[1].lower()
    format_map = {
        '.jpg': 'JPEG',
        '.jpeg': 'JPEG',
        '.png': 'PNG',
        '.webp': 'WEBP',
        '.bmp': 'BMP',
        '.tif': 'TIFF',
        '.tiff': 'TIFF',
        '.avif': 'AVIF',
        '.heic': 'HEIF',
        '.heif': 'HEIF',
    }
    return format_map.get(ext, 'PNG')


def build_preserved_pil_save_kwargs(source_image: Optional[Image.Image] = None) -> dict:
    if source_image is None:
        return {}

    info = getattr(source_image, 'info', None) or {}
    save_kwargs = {}

    icc_profile = info.get('icc_profile')
    if icc_profile:
        save_kwargs['icc_profile'] = icc_profile

    dpi = info.get('dpi')
    if isinstance(dpi, tuple) and len(dpi) >= 2 and dpi[0] is not None and dpi[1] is not None:
        save_kwargs['dpi'] = dpi[:2]

    return save_kwargs


def save_pil_image(
    image: Image.Image,
    output_path: str,
    source_image: Optional[Image.Image] = None,
    *,
    quality: Optional[int] = None,
    format: Optional[str] = None,
    **save_kwargs,
):
    """
    Save a PIL image while preserving source color metadata when possible.
    """
    target_format = _infer_pil_save_format(output_path, format)
    image_to_save = image
    converted_image = None

    try:
        if target_format in {'JPEG', 'BMP'}:
            if image_to_save.mode != 'RGB':
                converted_image = image_to_save.convert('RGB')
                image_to_save = converted_image
        elif image_to_save.mode == 'CMYK':
            converted_image = image_to_save.convert('RGB')
            image_to_save = converted_image

        for key, value in build_preserved_pil_save_kwargs(source_image).items():
            save_kwargs.setdefault(key, value)

        if quality is not None and target_format in {'JPEG', 'WEBP', 'AVIF', 'HEIF'}:
            save_kwargs.setdefault('quality', quality)

        if format is not None:
            image_to_save.save(output_path, format=target_format, **save_kwargs)
        else:
            image_to_save.save(output_path, **save_kwargs)
    finally:
        if converted_image is not None:
            converted_image.close()

def resize_keep_aspect(img, size):
    ratio = (float(size)/max(img.shape[0], img.shape[1]))
    new_width = round(img.shape[1] * ratio)
    new_height = round(img.shape[0] * ratio)
    return cv2.resize(img, (new_width, new_height), interpolation = cv2.INTER_LINEAR_EXACT)

def image_resize(image, width = None, height = None, inter = cv2.INTER_AREA):
    # initialize the dimensions of the image to be resized and
    # grab the image size
    dim = None
    (h, w) = image.shape[:2]

    # if both the width and height are None, then return the
    # original image
    if width is None and height is None:
        return image

    # check to see if the width is None
    if width is None:
        # calculate the ratio of the height and construct the
        # dimensions
        r = height / float(h)
        dim = (int(w * r), height)

    # otherwise, the height is None
    else:
        # calculate the ratio of the width and construct the
        # dimensions
        r = width / float(w)
        dim = (width, int(h * r))

    # resize the image
    resized = cv2.resize(image, dim, interpolation = inter)

    # return the resized image
    return resized

def resize_polygon(pts, xfact, yfact, origin='center'):
    poly = Polygon(pts)
    poly = affinity.scale(poly, xfact=xfact, yfact=yfact, origin=origin)
    dst_points = np.array(poly.exterior.coords[:4])
    return dst_points

class BBox(object):
    def __init__(self, x: int, y: int, w: int, h: int, text: str, prob: float, fg_r: int = 0, fg_g: int = 0, fg_b: int = 0, bg_r: int = 0, bg_g: int = 0, bg_b: int = 0):
        self.x = x
        self.y = y
        self.w = w
        self.h = h
        self.text = text
        self.prob = prob
        self.fg_r = fg_r
        self.fg_g = fg_g
        self.fg_b = fg_b
        self.bg_r = bg_r
        self.bg_g = bg_g
        self.bg_b = bg_b

    def width(self):
        return self.w

    def height(self):
        return self.h

    def to_points(self):
        tl, tr, br, bl = np.array([self.x, self.y]), np.array([self.x + self.w, self.y]), np.array([self.x + self.w, self.y+ self.h]), np.array([self.x, self.y + self.h])
        return tl, tr, br, bl

    @property
    def xywh(self):
        return np.array([self.x, self.y, self.w, self.h], dtype=np.int32)
    

def sort_pnts(pts: np.ndarray):
    '''
    Direction must be provided for sorting.
    The longer structure vector (mean of long side vectors) of input points is used to determine the direction.
    It is reliable enough for text lines but not for blocks.
    '''

    if isinstance(pts, List):
        pts = np.array(pts)
    assert isinstance(pts, np.ndarray) and pts.shape == (4, 2)
    pairwise_vec = (pts[:, None] - pts[None]).reshape((16, -1))
    pairwise_vec_norm = np.linalg.norm(pairwise_vec, axis=1)
    long_side_ids = np.argsort(pairwise_vec_norm)[[8, 10]]
    long_side_vecs = pairwise_vec[long_side_ids]
    inner_prod = (long_side_vecs[0] * long_side_vecs[1]).sum()
    if inner_prod < 0:
        long_side_vecs[0] = -long_side_vecs[0]
    struc_vec = np.abs(long_side_vecs.mean(axis=0))
    is_vertical = struc_vec[0] <= struc_vec[1]

    if is_vertical:
        pts = pts[np.argsort(pts[:, 1])]
        pts = pts[[*np.argsort(pts[:2, 0]), *np.argsort(pts[2:, 0])[::-1] + 2]]
        return pts, is_vertical
    else:
        pts = pts[np.argsort(pts[:, 0])]
        pts_sorted = np.zeros_like(pts)
        pts_sorted[[0, 3]] = sorted(pts[[0, 1]], key=lambda x: x[1])
        pts_sorted[[1, 2]] = sorted(pts[[2, 3]], key=lambda x: x[1])
        return pts_sorted, is_vertical


class Quadrilateral(object):
    """
    Helper for storing textlines that contains various helper functions.
    """
    def __init__(self, pts: np.ndarray, text: str, prob: float, fg_r: int = 0, fg_g: int = 0, fg_b: int = 0, bg_r: int = 0, bg_g: int = 0, bg_b: int = 0):
        self.pts, is_vertical = sort_pnts(pts)
        if is_vertical:
            self.direction = 'v'
        else:
            self.direction = 'h'
        self.text = text
        self.prob = prob
        self.fg_r = fg_r
        self.fg_g = fg_g
        self.fg_b = fg_b
        self.bg_r = bg_r
        self.bg_g = bg_g
        self.bg_b = bg_b
        self.assigned_direction: str = None
        self.textlines: List[Quadrilateral] = []

    @functools.cached_property
    def structure(self) -> List[np.ndarray]:
        p1 = ((self.pts[0] + self.pts[1]) / 2).astype(int)
        p2 = ((self.pts[2] + self.pts[3]) / 2).astype(int)
        p3 = ((self.pts[1] + self.pts[2]) / 2).astype(int)
        p4 = ((self.pts[3] + self.pts[0]) / 2).astype(int)
        return [p1, p2, p3, p4]

    @functools.cached_property
    def valid(self) -> bool:
        [l1a, l1b, l2a, l2b] = [a.astype(np.float32) for a in self.structure]
        v1 = l1b - l1a
        v2 = l2b - l2a
        unit_vector_1 = v1 / np.linalg.norm(v1)
        unit_vector_2 = v2 / np.linalg.norm(v2)
        dot_product = np.dot(unit_vector_1, unit_vector_2)
        angle = np.arccos(dot_product) * 180 / np.pi
        return abs(angle - 90) < 10

    @property
    def fg_colors(self):
        return np.array([self.fg_r, self.fg_g, self.fg_b])

    @property
    def bg_colors(self):
        return np.array([self.bg_r, self.bg_g, self.bg_b])

    @functools.cached_property
    def aspect_ratio(self) -> float:
        """hor/ver"""
        [l1a, l1b, l2a, l2b] = [a.astype(np.float32) for a in self.structure]
        v1 = l1b - l1a
        v2 = l2b - l2a
        return np.linalg.norm(v2) / np.linalg.norm(v1)

    @functools.cached_property
    def font_size(self) -> float:
        [l1a, l1b, l2a, l2b] = [a.astype(np.float32) for a in self.structure]
        v1 = l1b - l1a
        v2 = l2b - l2a
        return min(np.linalg.norm(v2), np.linalg.norm(v1))

    def width(self) -> int:
        return self.aabb.w

    def height(self) -> int:
        return self.aabb.h

    @functools.cached_property
    def xyxy(self):
        return self.aabb.x, self.aabb.y, self.aabb.x + self.aabb.w, self.aabb.y + self.aabb.h

    def clip(self, width, height):
        self.pts[:, 0] = np.clip(np.round(self.pts[:, 0]), 0, width)
        self.pts[:, 1] = np.clip(np.round(self.pts[:, 1]), 0, height)

    # @functools.cached_property
    # def points(self):
    #     ans = [a.astype(np.float32) for a in self.structure]
    #     return [Point(a[0], a[1]) for a in ans]

    @functools.cached_property
    def aabb(self) -> BBox:
        kq = self.pts
        max_coord = np.max(kq, axis = 0)
        min_coord = np.min(kq, axis = 0)
        return BBox(min_coord[0], min_coord[1], max_coord[0] - min_coord[0], max_coord[1] - min_coord[1], self.text, self.prob, self.fg_r, self.fg_g, self.fg_b, self.bg_r, self.bg_g, self.bg_b)

    def get_transformed_region(self, img, direction, textheight) -> np.ndarray:
        [l1a, l1b, l2a, l2b] = [a.astype(np.float32) for a in self.structure]
        v_vec = l1b - l1a
        h_vec = l2b - l2a
        ratio = np.linalg.norm(v_vec) / np.linalg.norm(h_vec)

        src_pts = self.pts.astype(np.int64).copy()
        im_h, im_w = img.shape[:2]

        x1, y1, x2, y2 = src_pts[:, 0].min(), src_pts[:, 1].min(), src_pts[:, 0].max(), src_pts[:, 1].max()
        x1 = np.clip(x1, 0, im_w)
        y1 = np.clip(y1, 0, im_h)
        x2 = np.clip(x2, 0, im_w)
        y2 = np.clip(y2, 0, im_h)
        
        # 检查裁剪区域是否有效，避免超出边界导致空图像
        if x1 >= x2 or y1 >= y2:
            # 返回一个小的空白图像以避免错误
            if direction == 'h':
                h = max(int(textheight), 2)
                w = max(int(textheight / 8), 2)
                return np.ones((h, w, 3), dtype=np.uint8) * 255
            else:
                w = max(int(textheight), 2)
                h = max(int(textheight * 8), 2)
                region = np.ones((h, w, 3), dtype=np.uint8) * 255
                region = cv2.rotate(region, cv2.ROTATE_90_COUNTERCLOCKWISE)
                return region
        
        # cv2.warpPerspective could overflow if image size is too large, better crop it here
        img_croped = img[y1: y2, x1: x2]
        
        src_pts[:, 0] -= x1
        src_pts[:, 1] -= y1

        self.assigned_direction = direction
        if direction == 'h':
            h = max(int(textheight), 2)
            w = max(int(round(textheight / ratio)), 2)
            dst_pts = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]]).astype(np.float32)
            M, _ = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
            region = cv2.warpPerspective(img_croped, M, (w, h))
            return region
        elif direction == 'v':
            w = max(int(textheight), 2)
            h = max(int(round(textheight * ratio)), 2)
            dst_pts = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]]).astype(np.float32)
            M, _ = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
            region = cv2.warpPerspective(img_croped, M, (w, h))
            region = cv2.rotate(region, cv2.ROTATE_90_COUNTERCLOCKWISE)
            return region

    @functools.cached_property
    def is_axis_aligned(self) -> bool:
        [l1a, l1b, l2a, l2b] = [a.astype(np.float32) for a in self.structure]
        v1 = l1b - l1a
        e1 = np.array([0, 1])
        e2 = np.array([1, 0])
        unit_vector_1 = v1 / np.linalg.norm(v1)
        if abs(np.dot(unit_vector_1, e1)) < 1e-2 or abs(np.dot(unit_vector_1, e2)) < 1e-2:
            return True
        return False

    @functools.cached_property
    def is_approximate_axis_aligned(self) -> bool:
        [l1a, l1b, l2a, l2b] = [a.astype(np.float32) for a in self.structure]
        v1 = l1b - l1a
        v2 = l2b - l2a
        e1 = np.array([0, 1])
        e2 = np.array([1, 0])
        unit_vector_1 = v1 / np.linalg.norm(v1)
        unit_vector_2 = v2 / np.linalg.norm(v2)
        if abs(np.dot(unit_vector_1, e1)) < 0.05 or abs(np.dot(unit_vector_1, e2)) < 0.05 or abs(np.dot(unit_vector_2, e1)) < 0.05 or abs(np.dot(unit_vector_2, e2)) < 0.05:
            return True
        return False

    @functools.cached_property
    def cosangle(self) -> float:
        [l1a, l1b, l2a, l2b] = [a.astype(np.float32) for a in self.structure]
        v1 = l1b - l1a
        e2 = np.array([1, 0])
        unit_vector_1 = v1 / np.linalg.norm(v1)
        return np.dot(unit_vector_1, e2)

    @functools.cached_property
    def angle(self) -> float:
        return np.fmod(np.arccos(self.cosangle) + np.pi, np.pi)

    @functools.cached_property
    def centroid(self) -> np.ndarray:
        return np.average(self.pts, axis = 0)

    def distance_to_point(self, p: np.ndarray) -> float:
        d = 1.0e20
        for i in range(4):
            d = min(d, distance_point_point(p, self.pts[i]))
            d = min(d, distance_point_lineseg(p, self.pts[i], self.pts[(i + 1) % 4]))
        return d

    @functools.cached_property
    def polygon(self) -> Polygon:
        return MultiPoint([tuple(self.pts[0]), tuple(self.pts[1]), tuple(self.pts[2]), tuple(self.pts[3])]).convex_hull

    @functools.cached_property
    def area(self) -> float:
        return self.polygon.area

    def poly_distance(self, other) -> float:
        """计算两个框之间的距离,优先使用平行边的中点距离"""
        # 获取方向
        dir_a = self.assigned_direction if self.assigned_direction is not None else self.direction
        dir_b = other.assigned_direction if other.assigned_direction is not None else other.direction

        # 如果方向一致,计算平行边的中点距离
        if dir_a == dir_b:
            if dir_a == 'h':  # 水平文本
                # 计算上边和下边的中点
                # self.pts: [左上, 右上, 右下, 左下]
                self_top_mid = ((self.pts[0][0] + self.pts[1][0]) / 2, (self.pts[0][1] + self.pts[1][1]) / 2)
                self_bottom_mid = ((self.pts[2][0] + self.pts[3][0]) / 2, (self.pts[2][1] + self.pts[3][1]) / 2)
                other_top_mid = ((other.pts[0][0] + other.pts[1][0]) / 2, (other.pts[0][1] + other.pts[1][1]) / 2)
                other_bottom_mid = ((other.pts[2][0] + other.pts[3][0]) / 2, (other.pts[2][1] + other.pts[3][1]) / 2)

                # 计算四种组合的距离,取最小值
                distances = [
                    np.sqrt((self_top_mid[0] - other_top_mid[0])**2 + (self_top_mid[1] - other_top_mid[1])**2),
                    np.sqrt((self_top_mid[0] - other_bottom_mid[0])**2 + (self_top_mid[1] - other_bottom_mid[1])**2),
                    np.sqrt((self_bottom_mid[0] - other_top_mid[0])**2 + (self_bottom_mid[1] - other_top_mid[1])**2),
                    np.sqrt((self_bottom_mid[0] - other_bottom_mid[0])**2 + (self_bottom_mid[1] - other_bottom_mid[1])**2),
                ]
                return min(distances)
            else:  # 垂直文本 (dir_a == 'v')
                # 计算左边和右边的中点
                self_left_mid = ((self.pts[0][0] + self.pts[3][0]) / 2, (self.pts[0][1] + self.pts[3][1]) / 2)
                self_right_mid = ((self.pts[1][0] + self.pts[2][0]) / 2, (self.pts[1][1] + self.pts[2][1]) / 2)
                other_left_mid = ((other.pts[0][0] + other.pts[3][0]) / 2, (other.pts[0][1] + other.pts[3][1]) / 2)
                other_right_mid = ((other.pts[1][0] + other.pts[2][0]) / 2, (other.pts[1][1] + other.pts[2][1]) / 2)

                # 计算四种组合的距离,取最小值
                distances = [
                    np.sqrt((self_left_mid[0] - other_left_mid[0])**2 + (self_left_mid[1] - other_left_mid[1])**2),
                    np.sqrt((self_left_mid[0] - other_right_mid[0])**2 + (self_left_mid[1] - other_right_mid[1])**2),
                    np.sqrt((self_right_mid[0] - other_left_mid[0])**2 + (self_right_mid[1] - other_left_mid[1])**2),
                    np.sqrt((self_right_mid[0] - other_right_mid[0])**2 + (self_right_mid[1] - other_right_mid[1])**2),
                ]
                return min(distances)

        # 如果方向不一致,使用Shapely的多边形距离
        return self.polygon.distance(other.polygon)

    def distance(self, other, rho = 0.5) -> float:
        return self.distance_impl(other, rho)# + 1000 * abs(self.angle - other.angle)

    def distance_impl(self, other, rho = 0.5) -> float:
        # assert self.assigned_direction == other.assigned_direction
        #return gjk_distance(self.points, other.points)
        # b1 = self.aabb
        # b2 = b2.aabb
        # x1, y1, w1, h1 = b1.x, b1.y, b1.w, b1.h
        # x2, y2, w2, h2 = b2.x, b2.y, b2.w, b2.h
        # return rect_distance(x1, y1, x1 + w1, y1 + h1, x2, y2, x2 + w2, y2 + h2)

        # 如果没有assigned_direction,使用direction作为回退
        dir_a = self.assigned_direction if self.assigned_direction is not None else self.direction
        dir_b = other.assigned_direction if other.assigned_direction is not None else other.direction

        pattern = ''
        if dir_a == 'h' and dir_b == 'h':
            pattern = 'h_left'
        else:
            pattern = 'v_top'
        fs = max(self.font_size, other.font_size)
        if dir_a == 'h' and dir_b == 'h':
            poly1 = MultiPoint([tuple(self.pts[0]), tuple(self.pts[3]), tuple(other.pts[0]), tuple(other.pts[3])]).convex_hull
            poly2 = MultiPoint([tuple(self.pts[2]), tuple(self.pts[1]), tuple(other.pts[2]), tuple(other.pts[1])]).convex_hull
            poly3 = MultiPoint([
                tuple(self.structure[0]),
                tuple(self.structure[1]),
                tuple(other.structure[0]),
                tuple(other.structure[1]),
            ]).convex_hull
            dist1 = poly1.area / fs
            dist2 = poly2.area / fs
            dist3 = poly3.area / fs
            if dist1 < fs * rho:
                pattern = 'h_left'
            if dist2 < fs * rho and dist2 < dist1:
                pattern = 'h_right'
            if dist3 < fs * rho and dist3 < dist1 and dist3 < dist2:
                pattern = 'h_middle'
            if pattern == 'h_left':
                return dist(self.pts[0][0], self.pts[0][1], other.pts[0][0], other.pts[0][1])
            elif pattern == 'h_right':
                return dist(self.pts[1][0], self.pts[1][1], other.pts[1][0], other.pts[1][1])
            else:
                return dist(self.structure[0][0], self.structure[0][1], other.structure[0][0], other.structure[0][1])
        else:
            poly1 = MultiPoint([tuple(self.pts[0]), tuple(self.pts[1]), tuple(other.pts[0]), tuple(other.pts[1])]).convex_hull
            poly2 = MultiPoint([tuple(self.pts[2]), tuple(self.pts[3]), tuple(other.pts[2]), tuple(other.pts[3])]).convex_hull
            dist1 = poly1.area / fs
            dist2 = poly2.area / fs
            if dist1 < fs * rho:
                pattern = 'v_top'
            if dist2 < fs * rho and dist2 < dist1:
                pattern = 'v_bottom'
            if pattern == 'v_top':
                return dist(self.pts[0][0], self.pts[0][1], other.pts[0][0], other.pts[0][1])
            else:
                return dist(self.pts[2][0], self.pts[2][1], other.pts[2][0], other.pts[2][1])

    def copy(self, new_pts: np.ndarray):
        return Quadrilateral(new_pts, self.text, self.prob, *self.fg_colors, *self.bg_colors)

# def merge_quadrilaterals(q1: Quadrilateral, q2: Quadrilateral):
#     min_rect = np.array(Polygon([*q1.pts, *q2.pts]).minimum_rotated_rectangle.exterior.coords[:4])
#     if q1.centroid[0] < q2.centroid[0] or q1.centroid[1] < q1.centroid[1]:
#         text = q1.text + ' ' + q2.text
#         # if q1.centroid[0] < q2.centroid[0]:
#         #     min_rect = np.array([q1.pts[0], q2.pts[1], q2.pts[2], q1.pts[3]])
#     else:
#         text = q2.text + ' ' + q1.text
#     prob = (q1.prob + q2.prob) / 2
#     fg_colors = (q1.fg_colors + q2.fg_colors) // 2
#     bg_colors = (q1.bg_colors + q2.bg_colors) // 2
#     return Quadrilateral(min_rect, text, prob, *fg_colors, *bg_colors)

def dist(x1, y1, x2, y2):
    return np.sqrt((x1 - x2)**2 + (y1 - y2)**2)

def rect_distance(x1, y1, x1b, y1b, x2, y2, x2b, y2b):
    left = x2b < x1
    right = x1b < x2
    bottom = y2b < y1
    top = y1b < y2
    if top and left:
        return dist(x1, y1b, x2b, y2)
    elif left and bottom:
        return dist(x1, y1, x2b, y2b)
    elif bottom and right:
        return dist(x1b, y1, x2, y2b)
    elif right and top:
        return dist(x1b, y1b, x2, y2)
    elif left:
        return x1 - x2b
    elif right:
        return x2 - x1b
    elif bottom:
        return y1 - y2b
    elif top:
        return y2 - y1b
    else:             # rectangles intersect
        return 0

def distance_point_point(a: np.ndarray, b: np.ndarray) -> float:
    return np.linalg.norm(a - b)

# from https://stackoverflow.com/questions/849211/shortest-distance-between-a-point-and-a-line-segment
def distance_point_lineseg(p: np.ndarray, p1: np.ndarray, p2: np.ndarray):
    x = p[0]
    y = p[1]
    x1 = p1[0]
    y1 = p1[1]
    x2 = p2[0]
    y2 = p2[1]
    A = x - x1
    B = y - y1
    C = x2 - x1
    D = y2 - y1

    dot = A * C + B * D
    len_sq = C * C + D * D
    param = -1
    if len_sq != 0:
        param = dot / len_sq

    if param < 0:
        xx = x1
        yy = y1
    elif param > 1:
        xx = x2
        yy = y2
    else:
        xx = x1 + param * C
        yy = y1 + param * D

    dx = x - xx
    dy = y - yy
    return np.sqrt(dx * dx + dy * dy)

def quadrilateral_can_merge_region(a: Quadrilateral, b: Quadrilateral, ratio = 1.9, discard_connection_gap = 2, char_gap_tolerance = 0.6, char_gap_tolerance2 = 1.5, font_size_ratio_tol = 2.0, aspect_ratio_tol = 3.0, debug = False) -> bool:
    b1 = a.aabb
    b2 = b.aabb
    char_size = min(a.font_size, b.font_size)
    x1, y1, w1, h1 = b1.x, b1.y, b1.w, b1.h
    x2, y2, w2, h2 = b2.x, b2.y, b2.w, b2.h
    # dist = rect_distance(x1, y1, x1 + w1, y1 + h1, x2, y2, x2 + w2, y2 + h2)
    p1 = Polygon(a.pts)
    p2 = Polygon(b.pts)
    dist = p1.distance(p2)

    # Relaxed constraint: Allow larger gap for vertical text merging (often ellipses are far)
    if dist > discard_connection_gap * char_size * 1.5:
        return False
    
    # === FORCE MERGE ALIGNED BLOCKS ===
    # 特殊逻辑：如果两个块在排列方向上高度对齐（重叠），且距离很近，强制允许合并
    # 解决如 "抱..." 和 "抱歉" 竖排并列未能合并的问题
    if a.assigned_direction == b.assigned_direction:
        direction = a.assigned_direction or a.direction
        
        if direction == 'v': # 竖排文本，检查垂直重叠（Y轴）和水平距离（X轴）
            # 垂直投影重叠率
            y_overlap = max(0, min(y1 + h1, y2 + h2) - max(y1, y2))
            min_h = min(h1, h2)
            y_overlap_ratio = y_overlap / min_h if min_h > 0 else 0
            
            # 水平距离（边缘到边缘）
            x_dist = max(0, max(x1, x2) - min(x1 + w1, x2 + w2))
            
            # 如果垂直高度重叠超过 80%，且水平距离小于 1.5 个字符宽度
            if y_overlap_ratio > 0.8 and x_dist < char_size * 1.5:
                # 进一步放宽条件：如果字体大小也差不多，直接合并
                if max(a.font_size, b.font_size) / char_size < 2.5:
                    return True
                    
        elif direction == 'h': # 横排文本，检查水平重叠（X轴）和垂直距离（Y轴）
            # 水平投影重叠率
            x_overlap = max(0, min(x1 + w1, x2 + w2) - max(x1, x2))
            min_w = min(w1, w2)
            x_overlap_ratio = x_overlap / min_w if min_w > 0 else 0
            
            # 垂直距离
            y_dist = max(0, max(y1, y2) - min(y1 + h1, y2 + h2))
            
            if x_overlap_ratio > 0.8 and y_dist < char_size * 1.5:
                if max(a.font_size, b.font_size) / char_size < 2.5:
                    return True

    if max(a.font_size, b.font_size) / char_size > font_size_ratio_tol:
        return False
    if a.aspect_ratio > aspect_ratio_tol and b.aspect_ratio < 1. / aspect_ratio_tol:
        return False
    if b.aspect_ratio > aspect_ratio_tol and a.aspect_ratio < 1. / aspect_ratio_tol:
        return False
    a_aa = a.is_approximate_axis_aligned
    b_aa = b.is_approximate_axis_aligned
    if a_aa and b_aa:
        if dist < char_size * char_gap_tolerance:
            if abs(x1 + w1 // 2 - (x2 + w2 // 2)) < char_gap_tolerance2:
                return True
            if w1 > h1 * ratio and h2 > w2 * ratio:
                return False
            if w2 > h2 * ratio and h1 > w1 * ratio:
                return False
            if w1 > h1 * ratio or w2 > h2 * ratio : # h
                result = abs(x1 - x2) < char_size * char_gap_tolerance2 or abs(x1 + w1 - (x2 + w2)) < char_size * char_gap_tolerance2
                return result
            elif h1 > w1 * ratio or h2 > w2 * ratio : # v
                result = abs(y1 - y2) < char_size * char_gap_tolerance2 or abs(y1 + h1 - (y2 + h2)) < char_size * char_gap_tolerance2
                return result
            return False
        else:
            return False
    if True:#not a_aa and not b_aa:
        if abs(a.angle - b.angle) < 15 * np.pi / 180:
            fs_a = a.font_size
            fs_b = b.font_size
            fs = min(fs_a, fs_b)
            poly_dist = a.poly_distance(b)
            if poly_dist > fs * char_gap_tolerance2:
                return False
            font_diff = abs(fs_a - fs_b) / fs
            if font_diff > 0.25:
                return False
            return True
    return False

def quadrilateral_can_merge_region_coarse(a: Quadrilateral, b: Quadrilateral, discard_connection_gap = 2, font_size_ratio_tol = 0.7) -> bool:
    if a.assigned_direction != b.assigned_direction:
        return False
    if abs(a.angle - b.angle) > 15 * np.pi / 180:
        return False
    fs_a = a.font_size
    fs_b = b.font_size
    fs = min(fs_a, fs_b)
    if abs(fs_a - fs_b) / fs > font_size_ratio_tol:
        return False
    fs = max(fs_a, fs_b)
    dist = a.poly_distance(b)
    if dist > discard_connection_gap * fs:
        return False
    return True

def findNextPowerOf2(n):
    i = 0
    while n != 0:
        i += 1
        n = n >> 1
    return 1 << i

class Point:
    def __init__(self, x = 0, y = 0):
        self.x = x
        self.y = y

    def length2(self) -> float:
        return self.x * self.x + self.y * self.y

    def length(self) -> float:
        return np.sqrt(self.length2())

    def __str__(self):
        return f'({self.x}, {self.y})'

    def __add__(self, other):
        x = self.x + other.x
        y = self.y + other.y
        return Point(x, y)

    def __sub__(self, other):
        x = self.x - other.x
        y = self.y - other.y
        return Point(x, y)

    def __mul__(self, other):
        if isinstance(other, Point):
            return self.x * other.x + self.y * other.y
        else:
            return Point(self.x * other, self.y * other)

    def __truediv__(self, other):
        return self.x * other.y - self.y * other.x

    def neg(self):
        return Point(-self.x, -self.y)

    def normalize(self):
        return self * (1. / self.length())

def center_of_points(pts: List[Point]) -> Point:
    ans = Point()
    for p in pts:
        ans.x += p.x
        ans.y += p.y
    ans.x /= len(pts)
    ans.y /= len(pts)
    return ans

def support_impl(pts: List[Point], d: Point) -> Point:
    dist = -1.0e-20
    ans = pts[0]
    for p in pts:
        proj = p * d
        if proj > dist:
            dist = proj
            ans = p
    return ans

def support(a: List[Point], b: List[Point], d: Point) -> Point:
    return support_impl(a, d) - support_impl(b, d.neg())

def cross(a: Point, b: Point, c: Point) -> Point:
    return b * (a * c) - a * (b * c)

def closest_point_to_origin(a: Point, b: Point) -> Point:
    da = a.length()
    db = b.length()
    dist = abs(a / b) / (a - b).length()
    ab = b - a
    ba = a - b
    ao = a.neg()
    bo = b.neg()
    if ab * ao > 0 and ba * bo > 0:
        return cross(ab, ao, ab).normalize() * dist
    return a.neg() if da < db else b.neg()

def dcmp(a) -> bool:
    if abs(a) < 1e-8:
        return False
    return True

def gjk_distance(s1: List[Point], s2: List[Point]) -> float:
    d = center_of_points(s2) - center_of_points(s1)
    a = support(s1, s2, d)
    b = support(s1, s2, d.neg())
    d = closest_point_to_origin(a, b)
    s = [a, b]
    for _ in range(8):
        c = support(s1, s2, d)
        a = s.pop()
        b = s.pop()
        da = d * a
        db = d * b
        dc = d * c
        if not dcmp(dc - da) or not dcmp(dc - db):
            return d.length()
        p1 = closest_point_to_origin(a, c)
        p2 = closest_point_to_origin(b, c)
        if p1.length2() < p2.length2():
            s.append(a)
            d = p1
        else:
            s.append(b)
            d = p2
        s.append(c)
    return 0

def color_difference(rgb1: List, rgb2: List) -> float:
    # https://en.wikipedia.org/wiki/Color_difference#CIE76
    color1 = np.array(rgb1, dtype=np.uint8).reshape(1, 1, 3)
    color2 = np.array(rgb2, dtype=np.uint8).reshape(1, 1, 3)
    diff = cv2.cvtColor(color1, cv2.COLOR_RGB2LAB).astype(np.float32) - cv2.cvtColor(color2, cv2.COLOR_RGB2LAB).astype(np.float32)
    diff[..., 0] *= 0.392
    diff = np.linalg.norm(diff, axis=2)
    return diff.item()

def fg_bg_compare(fg, bg):
    """比较前景色和背景色，如果对比度不足则自动修正背景色。
    描边是灰色或色差不足时，根据前景亮度选纯黑或纯白。"""
    fg_avg = np.mean(fg)
    bg_is_gray = (max(bg) - min(bg)) < 10
    if bg_is_gray or color_difference(fg, bg) < 30:
        bg = (255, 255, 255) if fg_avg <= 127 else (0, 0, 0)
    return fg, bg

def rgb2hex(r,g,b):
    return "#{:02x}{:02x}{:02x}".format(r,g,b)

def hex2rgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def get_color_name(rgb: List[int]) -> str:
        try:
            # TODO: Maybe replace with offline alternative
            url = f'https://www.thecolorapi.com/id?format=json&rgb={rgb[0]},{rgb[1]},{rgb[2]}'
            response = requests.get(url)
            if response.status_code == 200:
                return json.loads(response.text)['name']['value']
            else:
                return 'Unnamed'
        except Exception:
            return 'Unnamed'

def square_pad_resize(img: np.ndarray, tgt_size: int):
    h, w = img.shape[:2]
    pad_h, pad_w = 0, 0
    
    # make square image
    if w < h:
        pad_w = h - w
        w += pad_w
    elif h < w:
        pad_h = w - h
        h += pad_h

    pad_size = tgt_size - h
    if pad_size > 0:
        pad_h += pad_size
        pad_w += pad_size

    if pad_h > 0 or pad_w > 0:    
        img = cv2.copyMakeBorder(img, 0, pad_h, 0, pad_w, cv2.BORDER_CONSTANT)

    down_scale_ratio = tgt_size / img.shape[0]
    assert down_scale_ratio <= 1
    if down_scale_ratio < 1:
        img = cv2.resize(img, (tgt_size, tgt_size), interpolation=cv2.INTER_LINEAR)

    return img, down_scale_ratio, pad_h, pad_w

def build_det_rearrange_plan(img: np.ndarray, tgt_size: int = 1280) -> Optional[dict]:
    """
    Build a shared rearrange plan for long-image detection.
    Returns None if rearrangement is not required.
    """
    h, w = img.shape[:2]
    transpose = False
    if h < w:
        transpose = True
        h, w = img.shape[1], img.shape[0]

    asp_ratio = h / w
    down_scale_ratio = h / tgt_size

    # Rearrangement condition shared by all detectors.
    require_rearrange = down_scale_ratio > 2.5 and asp_ratio > 3
    if not require_rearrange:
        return None

    img_for_split = einops.rearrange(img, 'h w c -> w h c') if transpose else img

    pw_num = max(int(np.floor(2 * tgt_size / w)), 2)
    patch_size = ph = pw_num * w

    ph_num = int(np.ceil(h / ph))
    ph_step = int((h - ph) / (ph_num - 1)) if ph_num > 1 else 0
    rel_step_list = []
    patch_list = []
    for ii in range(ph_num):
        t = ii * ph_step
        b = t + ph
        rel_step_list.append(t / h)
        patch_list.append(img_for_split[t:b])

    p_num = int(np.ceil(ph_num / pw_num))
    pad_num = p_num * pw_num - ph_num
    for _ in range(pad_num):
        patch_list.append(np.zeros_like(patch_list[0]))

    return {
        'transpose': transpose,
        'h': h,
        'w': w,
        'pw_num': pw_num,
        'patch_size': patch_size,
        'ph_num': ph_num,
        'ph_step': ph_step,
        'rel_step_list': rel_step_list,
        'patch_list': patch_list,
        'p_num': p_num,
        'pad_num': pad_num,
    }

def det_rearrange_patch_array(plan: dict) -> np.ndarray:
    """
    Rearrange plan patch list into square patch batches.
    Output shape is (p_num, patch_size, patch_size[, c]).
    """
    patch_array = np.stack(plan['patch_list'], axis=0)
    squeeze_channel = False
    if patch_array.ndim == 3:
        patch_array = patch_array[..., None]
        squeeze_channel = True
    if plan['transpose']:
        patch_array = einops.rearrange(
            patch_array,
            '(p_num pw_num) ph pw c -> p_num (pw_num pw) ph c',
            p_num=plan['p_num'],
        )
    else:
        patch_array = einops.rearrange(
            patch_array,
            '(p_num pw_num) ph pw c -> p_num ph (pw_num pw) c',
            p_num=plan['p_num'],
        )
    if squeeze_channel:
        patch_array = patch_array[..., 0]
    return patch_array

def det_rearrange_split_view(arr: np.ndarray, transpose: bool) -> np.ndarray:
    """
    Convert input array into the internal long-side split coordinate view.
    """
    if not transpose:
        return arr
    if arr.ndim == 3:
        return einops.rearrange(arr, 'h w c -> w h c')
    if arr.ndim == 2:
        return einops.rearrange(arr, 'h w -> w h')
    raise ValueError(f'Unsupported array ndim for split view: {arr.ndim}')

def det_restore_split_view(arr: np.ndarray, transpose: bool) -> np.ndarray:
    """
    Restore array from internal split coordinate view back to original orientation.
    """
    # Axis swap is symmetric.
    return det_rearrange_split_view(arr, transpose)

def det_rearrange_patch_spans(plan: dict) -> List[Tuple[int, int]]:
    """
    Returns patch spans (top, bottom) in the internal split coordinate view.
    """
    h = int(plan['h'])
    patch_size = int(plan['patch_size'])
    spans = []
    for ii in range(int(plan['ph_num'])):
        rel_t = float(plan['rel_step_list'][ii])
        t = int(round(rel_t * h))
        b = min(t + patch_size, h)
        spans.append((t, b))
    return spans

def det_collect_plan_patches(arr: np.ndarray, plan: dict) -> List[np.ndarray]:
    """
    Collect per-span patches from an array using a rearrange plan.
    Output includes zero-padding patches to match plan['pad_num'].
    """
    split_view = det_rearrange_split_view(arr, bool(plan['transpose']))
    spans = det_rearrange_patch_spans(plan)
    patch_list = [split_view[t:b].copy() for (t, b) in spans]
    if not patch_list:
        return patch_list
    for _ in range(int(plan['pad_num'])):
        patch_list.append(np.zeros_like(patch_list[0]))
    return patch_list

def _to_chw_patch(patch: np.ndarray, data_format: str) -> np.ndarray:
    if data_format == 'chw':
        if patch.ndim != 3:
            raise ValueError(f'Expected 3D CHW patch, got shape={patch.shape}')
        return patch
    if data_format == 'hwc':
        if patch.ndim != 3:
            raise ValueError(f'Expected 3D HWC patch, got shape={patch.shape}')
        return einops.rearrange(patch, 'h w c -> c h w')
    if data_format == 'hw':
        if patch.ndim != 2:
            raise ValueError(f'Expected 2D HW patch, got shape={patch.shape}')
        return patch[None, ...]
    raise ValueError(f'Unsupported data_format: {data_format}')

def det_unrearrange_patch_maps(
    patch_lst: List[np.ndarray],
    plan: dict,
    data_format: str = 'chw',
) -> np.ndarray:
    """
    Merge rearranged patch outputs back into original image coordinates.
    Supports patch inputs in CHW / HWC / HW.
    """
    if not patch_lst:
        raise ValueError('patch_lst must not be empty')

    transpose = bool(plan['transpose'])
    h = int(plan['h'])
    w = int(plan['w'])
    pw_num = int(plan['pw_num'])
    patch_size = int(plan['patch_size'])
    ph_step = int(plan['ph_step'])
    rel_step_list = plan['rel_step_list']
    pad_num = int(plan['pad_num'])

    patch0 = _to_chw_patch(patch_lst[0], data_format)
    _psize = int(patch0.shape[-1])
    _step = int(ph_step * _psize / patch_size)
    _pw = int(_psize / pw_num)
    _h = int(_pw / w * h)

    tgtmap = np.zeros((patch0.shape[0], _h, _pw), dtype=np.float32)
    num_patches = len(patch_lst) * pw_num - pad_num

    for ii, patch in enumerate(patch_lst):
        p = _to_chw_patch(patch, data_format)
        if transpose:
            p = einops.rearrange(p, 'c h w -> c w h')
        for jj in range(pw_num):
            pidx = ii * pw_num + jj
            if pidx >= len(rel_step_list):
                break
            rel_t = float(rel_step_list[pidx])
            t = int(round(rel_t * _h))
            b = min(t + _psize, _h)
            l = jj * _pw
            r = l + _pw
            tgtmap[..., t:b, :] += p[..., : b - t, l:r]
            if pidx > 0 and _step < _psize:
                interleave = _psize - _step
                tgtmap[..., t:t + interleave, :] /= 2.
            if pidx >= num_patches - 1:
                break

    if transpose:
        tgtmap = einops.rearrange(tgtmap, 'c h w -> c w h')

    if data_format == 'chw':
        return tgtmap
    if data_format == 'hwc':
        return einops.rearrange(tgtmap, 'c h w -> h w c')
    if data_format == 'hw':
        return tgtmap[0]
    raise ValueError(f'Unsupported data_format: {data_format}')

def det_rearrange_forward(
    img: np.ndarray, 
    dbnet_batch_forward: Callable[[np.ndarray, str], Tuple[np.ndarray, np.ndarray]], 
    tgt_size: int = 1280, 
    max_batch_size: int = 4, 
    device='cuda', verbose=False, result_path_fn=None):
    '''
    Rearrange image to square batches before feeding into network if following conditions are satisfied: \n
    1. Extreme aspect ratio
    2. Is too tall or wide for detect size (tgt_size)

    Returns:
        DBNet output, mask or None, None if rearrangement is not required
    '''

    def _patch2batches(patch_arr: np.ndarray):
        batches = [[]]
        down_scale_ratio, pad_size = 1.0, 0
        for ii, patch in enumerate(patch_arr):

            if len(batches[-1]) >= max_batch_size:
                batches.append([])
            p, down_scale_ratio, pad_h, pad_w = square_pad_resize(patch, tgt_size=tgt_size)

            assert pad_h == pad_w
            pad_size = pad_h
            batches[-1].append(p)
            if verbose:
                import logging
                logger = logging.getLogger('manga_translator')
                if result_path_fn:
                    debug_path = result_path_fn(f'rearrange_{ii}.png')
                else:
                    debug_path = f'result/rearrange_{ii}.png'
                imwrite_unicode(debug_path, p[..., ::-1], logger)
        return batches, down_scale_ratio, pad_size

    plan = build_det_rearrange_plan(img, tgt_size=tgt_size)
    if plan is None:
        return None, None

    patch_arr = det_rearrange_patch_array(plan)

    if verbose:
        if result_path_fn:
            print(f'Input image will be rearranged to square batches before fed into network.\n Rearranged batches will be saved to result/{result_path_fn("rearrange_*.png")}')
        else:
            print('Input image will be rearranged to square batches before fed into network.\n Rearranged batches will be saved to result/rearrange_%d.png')

    batches, down_scale_ratio, pad_size = _patch2batches(patch_arr)

    db_lst, mask_lst = [], []
    for batch in batches:
        batch = np.array(batch)
        db, mask = dbnet_batch_forward(batch, device=device)

        for d, m in zip(db, mask):
            if pad_size > 0:
                paddb = int(db.shape[-1] / tgt_size * pad_size)
                padmsk = int(mask.shape[-1] / tgt_size * pad_size)
                d = d[..., :-paddb, :-paddb]
                m = m[..., :-padmsk, :-padmsk]
            db_lst.append(d)
            mask_lst.append(m)

    db = det_unrearrange_patch_maps(db_lst, plan, data_format='chw')[None, ...]
    mask = det_unrearrange_patch_maps(mask_lst, plan, data_format='chw')[None, ...]
    return db, mask


def main():
    s1 = [Point(0, 0), Point(0, 2), Point(2, 2), Point(2, 0)]
    offset = 0
    s2 = [Point(1 + offset, 1 + offset), Point(1 + offset, 3 + offset), Point(3 + offset, 3 + offset + 1.5), Point(3 + offset + 1.5, 3 + offset), Point(3 + offset, 1 + offset)]
    print(gjk_distance(s1, s2))

def imwrite_unicode(path: str, img: np.ndarray, logger, params=None) -> bool:
    """
    Writes an image to a file, handling unicode paths correctly.
    
    Args:
        path: Output file path
        img: Image array
        logger: Logger instance
        params: Optional cv2.imencode parameters (e.g., [cv2.IMWRITE_PNG_COMPRESSION, 9])
    """
    try:
        ext = os.path.splitext(path)[1]
        result, buf = cv2.imencode(ext, img, params) if params is not None else cv2.imencode(ext, img)
        if result:
            with open(path, "wb") as f:
                f.write(buf)
            return True
        else:
            logger.warning(f"Failed to encode image to buffer for path: {path}")
            return False
    except Exception as e:
        logger.error(f"Failed to write image to {path}: {e}")
        return False

if __name__ == '__main__':
    main()
