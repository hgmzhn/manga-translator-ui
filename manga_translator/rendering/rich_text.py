import copy
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, List, Literal, Optional, Union


RICH_TEXT_FORMAT = 'richtext.v1'
_BR_RE = re.compile(r'\s*(?:\[BR\]|<br\s*/?>|【BR】|\r\n|\r|\n)\s*', re.IGNORECASE)


@dataclass
class StrokeStyle:
    color: Optional[str] = None
    width: Optional[float] = None

    @classmethod
    def from_dict(cls, value: Optional[dict]) -> Optional["StrokeStyle"]:
        if value is None:
            return None
        value = _expect_dict(value, 'style.stroke')
        _reject_unknown_keys(value, {'color', 'width'}, 'style.stroke')
        if not value:
            return None
        return cls(color=value.get('color'), width=_safe_float(value.get('width'), None))

    def to_dict(self) -> dict:
        return _drop_none({'color': self.color, 'width': self.width})


@dataclass
class GlowStyle:
    color: Optional[str] = None
    blur: float = 0.0

    @classmethod
    def from_dict(cls, value: Optional[dict]) -> Optional["GlowStyle"]:
        if value is None:
            return None
        value = _expect_dict(value, 'style.glow')
        _reject_unknown_keys(value, {'color', 'blur'}, 'style.glow')
        if not value:
            return None
        return cls(color=value.get('color'), blur=float(value.get('blur') or 0.0))

    def to_dict(self) -> dict:
        return _drop_none({'color': self.color, 'blur': self.blur})


@dataclass
class TextTransform:
    offset_x: float = 0.0
    offset_y: float = 0.0
    rotation: float = 0.0
    mirror_x: bool = False
    mirror_y: bool = False

    @classmethod
    def from_dict(cls, value: Optional[dict]) -> "TextTransform":
        if value is None:
            value = {}
        value = _expect_dict(value, 'style.transform')
        _reject_unknown_keys(value, {'offsetX', 'offsetY', 'rotation', 'mirrorX', 'mirrorY'}, 'style.transform')
        return cls(
            offset_x=float(value.get('offsetX', 0.0) or 0.0),
            offset_y=float(value.get('offsetY', 0.0) or 0.0),
            rotation=float(value.get('rotation', 0.0) or 0.0),
            mirror_x=bool(value.get('mirrorX', False)),
            mirror_y=bool(value.get('mirrorY', False)),
        )

    def to_dict(self) -> dict:
        return _drop_none({
            'offsetX': self.offset_x or None,
            'offsetY': self.offset_y or None,
            'rotation': self.rotation or None,
            'mirrorX': self.mirror_x or None,
            'mirrorY': self.mirror_y or None,
        })


@dataclass
class TextStyle:
    bold: bool = False
    # italic: True = 默认斜体角度（渲染层按参考实现取 15°）；数字 = 切变角度
    # （度，正值向右倾）。False/0 = 无斜体。
    italic: Union[bool, float] = False
    color: Optional[str] = None
    scale: float = 1.0
    font_size: Optional[float] = None
    font_family: Optional[str] = None
    stroke: Optional[StrokeStyle] = None
    outer_stroke: Optional[StrokeStyle] = None
    glow: Optional[GlowStyle] = None
    emphasis: bool = False
    no_tcy: bool = False
    kerning: float = 0.0
    pre_kerning: float = 0.0
    line_kerning: Optional[float] = None
    next_kerning: Optional[float] = None
    transform: TextTransform = field(default_factory=TextTransform)

    def copy(self) -> "TextStyle":
        return copy.deepcopy(self)

    @classmethod
    def from_dict(cls, value: Optional[dict]) -> "TextStyle":
        if value is None:
            value = {}
        value = _expect_dict(value, 'style')
        _reject_unknown_keys(
            value,
            {
                'bold',
                'italic',
                'color',
                'scale',
                'fontSize',
                'fontFamily',
                'stroke',
                'outerStroke',
                'glow',
                'emphasis',
                'noTcy',
                'kerning',
                'preKerning',
                'lineKerning',
                'nextKerning',
                'transform',
            },
            'style',
        )
        font_size = _safe_float(value.get('fontSize'), None)
        return cls(
            bold=bool(value.get('bold', False)),
            italic=_parse_italic(value.get('italic', False)),
            color=value.get('color'),
            scale=float(value.get('scale', 1.0) or 1.0),
            font_size=font_size,
            font_family=value.get('fontFamily'),
            stroke=StrokeStyle.from_dict(value.get('stroke')),
            outer_stroke=StrokeStyle.from_dict(value.get('outerStroke')),
            glow=GlowStyle.from_dict(value.get('glow')),
            emphasis=bool(value.get('emphasis', False)),
            no_tcy=bool(value.get('noTcy', False)),
            kerning=float(value.get('kerning', 0.0) or 0.0),
            pre_kerning=float(value.get('preKerning', 0.0) or 0.0),
            line_kerning=_safe_float(value.get('lineKerning'), None),
            next_kerning=_safe_float(value.get('nextKerning'), None),
            transform=TextTransform.from_dict(value.get('transform')),
        )

    def to_dict(self) -> dict:
        return _drop_none({
            'bold': self.bold or None,
            'italic': self.italic or None,
            'color': self.color,
            'scale': None if self.scale == 1.0 else self.scale,
            'fontSize': self.font_size,
            'fontFamily': self.font_family,
            'stroke': self.stroke.to_dict() if self.stroke else None,
            'outerStroke': self.outer_stroke.to_dict() if self.outer_stroke else None,
            'glow': self.glow.to_dict() if self.glow else None,
            'emphasis': self.emphasis or None,
            'noTcy': self.no_tcy or None,
            'kerning': self.kerning or None,
            'preKerning': self.pre_kerning or None,
            'lineKerning': self.line_kerning,
            'nextKerning': self.next_kerning,
            'transform': self.transform.to_dict() or None,
        })


@dataclass
class TextRun:
    text: str
    style: TextStyle = field(default_factory=TextStyle)
    type: Literal['text'] = 'text'

    @classmethod
    def from_dict(cls, value: dict) -> "TextRun":
        value = _expect_dict(value, 'text inline')
        _reject_unknown_keys(value, {'type', 'text', 'style'}, 'text inline')
        if value.get('type') != 'text':
            raise ValueError('text inline.type must be "text"')
        if 'text' not in value:
            raise ValueError('text inline.text is required')
        if not isinstance(value['text'], str):
            raise TypeError('text inline.text must be a string')
        return cls(text=value['text'], style=TextStyle.from_dict(value.get('style')))

    def to_dict(self) -> dict:
        return {'type': 'text', 'text': self.text, 'style': self.style.to_dict()}


@dataclass
class RubyRun:
    base: List[TextRun] = field(default_factory=list)
    text: List[TextRun] = field(default_factory=list)
    type: Literal['ruby'] = 'ruby'

    @classmethod
    def from_dict(cls, value: dict) -> "RubyRun":
        value = _expect_dict(value, 'ruby inline')
        _reject_unknown_keys(value, {'type', 'base', 'text'}, 'ruby inline')
        if value.get('type') != 'ruby':
            raise ValueError('ruby inline.type must be "ruby"')
        return cls(
            base=[TextRun.from_dict(item) for item in _required_list(value, 'base', 'ruby inline')],
            text=[TextRun.from_dict(item) for item in _required_list(value, 'text', 'ruby inline')],
        )

    def to_dict(self) -> dict:
        return {'type': 'ruby', 'base': [item.to_dict() for item in self.base], 'text': [item.to_dict() for item in self.text]}


@dataclass
class TcyRun:
    content: List[TextRun] = field(default_factory=list)
    type: Literal['tcy'] = 'tcy'

    @classmethod
    def from_dict(cls, value: dict) -> "TcyRun":
        value = _expect_dict(value, 'tcy inline')
        _reject_unknown_keys(value, {'type', 'content'}, 'tcy inline')
        if value.get('type') != 'tcy':
            raise ValueError('tcy inline.type must be "tcy"')
        return cls(content=[TextRun.from_dict(item) for item in _required_list(value, 'content', 'tcy inline')])

    def to_dict(self) -> dict:
        return {'type': 'tcy', 'content': [item.to_dict() for item in self.content]}


InlineNode = Union[TextRun, RubyRun, TcyRun]


@dataclass
class RenderSpan:
    text: str
    style: TextStyle = field(default_factory=TextStyle)
    ruby: Optional[List[TextRun]] = None
    tcy: bool = False


@dataclass
class Paragraph:
    inlines: List[InlineNode] = field(default_factory=list)
    type: Literal['paragraph'] = 'paragraph'

    @classmethod
    def from_dict(cls, value: dict) -> "Paragraph":
        value = _expect_dict(value, 'paragraph block')
        _reject_unknown_keys(value, {'type', 'inlines'}, 'paragraph block')
        if value.get('type') != 'paragraph':
            raise ValueError('paragraph block.type must be "paragraph"')
        inlines = []
        for item in _required_list(value, 'inlines', 'paragraph block'):
            item = _expect_dict(item, 'paragraph inline')
            item_type = item.get('type', 'text')
            if item_type == 'ruby':
                inlines.append(RubyRun.from_dict(item))
            elif item_type == 'tcy':
                inlines.append(TcyRun.from_dict(item))
            elif item_type == 'text':
                inlines.append(TextRun.from_dict(item))
            else:
                raise ValueError(f'Unsupported rich text inline type: {item_type!r}')
        return cls(inlines=inlines)

    @property
    def spans(self) -> List[RenderSpan]:
        """渲染 span 视图（惰性缓存，F24）。

        解析后的文档视为不可变（全仓无人在解析后原地修改 blocks/inlines），
        首次访问计算并缓存到 self._spans。缓存的 span.style 在构建时已与
        inline.style 脱钩（copy），消费方需要改样式时自行 copy（现有调用方
        _rich_ruby_surface/_draw_vertical_ruby 均如此）。
        """
        cached = getattr(self, '_spans', None)
        if cached is not None:
            return cached
        spans: List[RenderSpan] = []
        for inline in self.inlines:
            if isinstance(inline, TextRun):
                spans.append(RenderSpan(inline.text, inline.style.copy()))
            elif isinstance(inline, RubyRun):
                base_text = ''.join(run.text for run in inline.base)
                style = inline.base[0].style.copy() if inline.base else TextStyle()
                ruby_text = ''.join(run.text for run in inline.text)
                spans.append(RenderSpan(base_text, style, ruby=inline.text if ruby_text else None))
            elif isinstance(inline, TcyRun):
                text = ''.join(run.text for run in inline.content)
                style = inline.content[0].style.copy() if inline.content else TextStyle()
                spans.append(RenderSpan(text, style, tcy=True))
        self._spans = spans
        return spans

    def plain_text(self) -> str:
        return ''.join(span.text for span in self.spans)

    def to_dict(self) -> dict:
        return {'type': 'paragraph', 'inlines': [inline.to_dict() for inline in self.inlines]}


@dataclass
class RichTextDocument:
    blocks: List[Paragraph] = field(default_factory=list)
    format: str = RICH_TEXT_FORMAT

    @classmethod
    def from_dict(cls, value: dict) -> "RichTextDocument":
        value = _expect_dict(value, 'rich text document')
        _reject_unknown_keys(value, {'format', 'blocks'}, 'rich text document')
        if value.get('format') != RICH_TEXT_FORMAT:
            raise ValueError(f'rich text document.format must be "{RICH_TEXT_FORMAT}"')
        return cls(blocks=[Paragraph.from_dict(block) for block in _required_list(value, 'blocks', 'rich text document')])

    @property
    def paragraphs(self) -> List[Paragraph]:
        return self.blocks

    def plain_text(self) -> str:
        return '\n'.join(block.plain_text() for block in self.blocks)

    def to_dict(self) -> dict:
        return {'format': self.format, 'blocks': [block.to_dict() for block in self.blocks]}


def is_rich_text_document(value: Any) -> bool:
    return isinstance(value, RichTextDocument) or (isinstance(value, dict) and value.get('format') == RICH_TEXT_FORMAT)


def plain_text_of(value: Any) -> str:
    """任意译文值 → 纯文本：richtext.v1 文档取正文，字符串原样，None 归空串。

    这是"富文本值转纯文本"的唯一入口，所有出口（渲染预览、导出、日志、
    过滤）都应调用它，而不是各自手写 isinstance 分支。
    """
    if is_rich_text_document(value):
        return ensure_rich_text_document(value).plain_text()
    return value if isinstance(value, str) else str(value or '')


def has_content(value: Any) -> bool:
    """译文值是否含可渲染文本（纯文本去空白非空 / 文档正文非空）。"""
    return bool(plain_text_of(value).strip())


def plain_equivalent_text(value: Any) -> Optional[str]:
    """若文档只是"纯文本 + 换行"（无任何样式/注音/纵中横），返回等价的
    多行字符串（段落以 \\n 连接）；否则返回 None。

    用于让无样式文档回退到纯字符串渲染路径（如 HQ 超采样）。
    """
    if not is_rich_text_document(value):
        return None
    document = ensure_rich_text_document(value)
    lines = []
    for paragraph in document.blocks:
        for inline in paragraph.inlines:
            if not isinstance(inline, TextRun):
                return None
            if inline.style.to_dict():
                return None
        lines.append(''.join(inline.text for inline in paragraph.inlines))
    return '\n'.join(lines)


def ensure_rich_text_document(value: Any) -> RichTextDocument:
    if isinstance(value, RichTextDocument):
        return value
    if isinstance(value, dict):
        return RichTextDocument.from_dict(value)
    raise TypeError(f'Expected RichTextDocument or {RICH_TEXT_FORMAT} dict, got {type(value).__name__}')


def _expect_dict(value: Any, context: str) -> dict:
    if not isinstance(value, dict):
        raise TypeError(f'{context} must be a dict')
    return value


def _required_list(value: dict, key: str, context: str) -> List[Any]:
    if key not in value:
        raise ValueError(f'{context}.{key} is required')
    items = value[key]
    if not isinstance(items, list):
        raise TypeError(f'{context}.{key} must be a list')
    return items


def _reject_unknown_keys(value: dict, allowed_keys, context: str) -> None:
    unknown = sorted(set(value) - set(allowed_keys))
    if unknown:
        raise ValueError(f'{context} has unsupported keys: {", ".join(unknown)}')


def iter_render_spans(document: RichTextDocument) -> Iterable[RenderSpan]:
    for paragraph in document.paragraphs:
        yield from paragraph.spans


def normalize_rich_linebreaks(text: str) -> str:
    return _BR_RE.sub('\n', text or '')


def has_legacy_line_breaks(text: Any) -> bool:
    return isinstance(text, str) and bool(_BR_RE.search(text))


def legacy_line_breaks_to_document(text: str, style: Optional[TextStyle] = None) -> RichTextDocument:
    style = style or TextStyle()
    blocks = [
        Paragraph(inlines=[TextRun(part, style.copy())] if part else [])
        for part in _BR_RE.split(text or '')
    ]
    return RichTextDocument(blocks=blocks or [Paragraph()])


def _safe_float(value, default):
    try:
        if value is None or value == '':
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_italic(value) -> Union[bool, float]:
    """italic 协议值：bool 原样；数字为切变角度（度），0 归一成 False。"""
    if isinstance(value, bool) or value is None:
        return bool(value)
    number = _safe_float(value, None)
    if number is None:
        raise ValueError('style.italic must be a bool or a number (degrees)')
    return number if number else False


def _drop_none(value: dict) -> dict:
    return {k: v for k, v in value.items() if v is not None}
