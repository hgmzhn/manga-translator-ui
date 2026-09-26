"""Typed, host-independent contracts for the built-in workspace tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath
from threading import Event
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ToolError(Exception):
    def __init__(self, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "details": self.details}


class ToolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


def relative_folder(value: str) -> str:
    if value == ".":
        return value
    if (
        not value
        or any(char in value for char in ("\\", ":", "\x00"))
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ValueError("Folder must be a relative POSIX directory, or '.' for root")
    return value


class PageById(ToolModel):
    id: int = Field(strict=True, gt=0)


class PageByName(ToolModel):
    folder: str = Field(strict=True)
    name: str = Field(strict=True)

    @field_validator("folder")
    @classmethod
    def safe_folder(cls, value: str) -> str:
        return relative_folder(value)

    @field_validator("name")
    @classmethod
    def full_filename(cls, value: str) -> str:
        if (
            not value
            or any(char in value for char in ("/", "\\", ":", "\x00"))
            or not PurePosixPath(value).suffix
        ):
            raise ValueError("Name must be the full filename including its extension")
        return value


PageRef = PageById | PageByName


class ToolScope(ToolModel):
    pages: list[PageRef] | None = None
    folder: str | None = Field(default=None, strict=True)
    recursive: bool = Field(default=True, strict=True)

    @field_validator("folder")
    @classmethod
    def safe_folder(cls, value: str | None) -> str | None:
        return relative_folder(value) if value is not None else None


class ResourceScope(ToolModel):
    work_id: str | None = None
    chapter_id: str | None = None
    page_ids: list[str] | None = None


@dataclass
class AccessGrant:
    layout_pages: set[str]
    translation_pages: set[str]
    geometry_pages: set[str]
    style_scopes: set[str]
    region_ids: dict[str, set[str]] = field(default_factory=dict)
    parent: AccessGrant | None = None


@dataclass
class ToolContext:
    workspace: Any
    grant: AccessGrant
    task_id: str
    cancelled: Event = field(default_factory=Event)
    runtime: Any = None
    renderer: Any = None
    observed_revisions: dict[str, int] = field(default_factory=dict)
    read_snapshots: dict[str, dict] = field(default_factory=dict)
    read_policies: dict[str, dict] = field(default_factory=dict)
    command_payloads: dict[str, dict] = field(default_factory=dict)
    transaction_results: dict[str, dict] = field(default_factory=dict)


Color = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]
Positive = Annotated[float, Field(gt=0)]
Nonnegative = Annotated[float, Field(ge=0)]


class StrokePatch(ToolModel):
    color: Color | None = None
    width: Nonnegative | None = None


class GlowPatch(ToolModel):
    color: Color | None = None
    blur: Nonnegative | None = None


class TransformPatch(ToolModel):
    offsetX: float | None = None
    offsetY: float | None = None
    rotation: float | None = None
    mirrorX: bool | None = None
    mirrorY: bool | None = None
    scaleX: Positive | None = None
    scaleY: Positive | None = None


class TextStylePatch(ToolModel):
    bold: bool | None = None
    italic: bool | float | None = None
    underline: bool | None = None
    strikethrough: bool | None = None
    color: Color | None = None
    scale: Positive | None = None
    fontSize: Positive | None = None
    fontFamily: str | None = None
    stroke: StrokePatch | None = None
    outerStroke: StrokePatch | None = None
    glow: GlowPatch | None = None
    emphasis: bool | None = None
    noTcy: bool | None = None
    verticalAdvance: Literal["half", "full"] | None = None
    kerning: float | None = None
    preKerning: float | None = None
    lineKerning: float | None = None
    nextKerning: float | None = None
    transform: TransformPatch | None = None

    @field_validator("fontFamily")
    @classmethod
    def family_not_path(cls, value: str | None) -> str | None:
        if value is not None and (
            not value.strip() or any(c in value for c in "/\\:\x00")
        ):
            raise ValueError("fontFamily must be a font family, not a host path")
        return value


class TextRun(ToolModel):
    type: Literal["text"] = "text"
    text: str
    style: TextStylePatch = Field(default_factory=TextStylePatch)


class RubyRun(ToolModel):
    type: Literal["ruby"] = "ruby"
    base: list[TextRun]
    text: list[TextRun]


class TcyRun(ToolModel):
    type: Literal["tcy"] = "tcy"
    content: list[TextRun]


Inline = Annotated[TextRun | RubyRun | TcyRun, Field(discriminator="type")]


class Paragraph(ToolModel):
    type: Literal["paragraph"] = "paragraph"
    inlines: list[Inline]


class RichDocument(ToolModel):
    format: Literal["richtext.v1"] = "richtext.v1"
    blocks: list[Paragraph]


class RegionStylePatch(ToolModel):
    font_size: Positive | None = None
    font_family: str | None = None
    font_color: Color | None = None
    stroke_color: Color | None = None
    stroke_width: Nonnegative | None = None
    line_spacing: Positive | None = None
    letter_spacing: Positive | None = None
    alignment: Literal["auto", "left", "center", "right"] | None = None
    direction: Literal["auto", "h", "v", "hr", "vr"] | None = None
    disable_font_border: bool | None = None
    opacity: Annotated[float, Field(ge=0, le=1)] | None = None

    @field_validator("font_family")
    @classmethod
    def family_not_path(cls, value: str | None) -> str | None:
        return TextStylePatch.family_not_path(value)


Point = tuple[float, float]
Quad = tuple[Point, Point, Point, Point]


class GeometryPatch(ToolModel):
    center: Point | None = None
    lines: list[Quad] | None = None
    angle: float | None = None


class RegionEdit(RegionStylePatch):
    """Flat model-facing patch; converted to typed operations before committing."""

    region_id: str = Field(min_length=1)
    center: Point | None = None
    angle: float | None = None
    translation: str | None = None

    @model_validator(mode="after")
    def nonempty_patch(self):
        changed = self.model_fields_set - {"region_id"}
        if not changed:
            raise ValueError("至少提供一个要修改的字段")
        if any(getattr(self, name) is None for name in changed):
            raise ValueError("修改字段不能为 null；不修改的字段请省略")
        return self


class NewRegion(ToolModel):
    center: Point = Field(description="文本框中心，页面像素坐标 [x, y]")
    width: Positive = Field(le=16000, description="文本框宽度，像素")
    height: Positive = Field(le=16000, description="文本框高度，像素")
    translation: str = Field(max_length=100_000)
    direction: Literal["auto", "h", "v", "hr", "vr"] = "auto"
    angle: float = Field(default=0, description="旋转角度，单位为度")


class CreateRegion(NewRegion):
    """Internal operation: the host fits the font before committing."""

    op: Literal["create_region"] = "create_region"
    region_id: str = Field(min_length=1)
    font_size: Annotated[int, Field(strict=True, ge=1, le=8192)]


class DeleteRegion(ToolModel):
    op: Literal["delete_region"] = "delete_region"
    region_id: str = Field(min_length=1)


class SetRegionStyle(ToolModel):
    op: Literal["set_region_style"] = "set_region_style"
    region_id: str
    style: RegionStylePatch


class ReplaceRichText(ToolModel):
    op: Literal["replace_rich_text"] = "replace_rich_text"
    region_id: str
    document: RichDocument


class SetGeometry(ToolModel):
    op: Literal["set_geometry"] = "set_geometry"
    region_id: str
    geometry: GeometryPatch


class SetTranslation(ToolModel):
    op: Literal["set_translation"] = "set_translation"
    region_id: str
    text: str


class SetSpanStyle(ToolModel):
    op: Literal["set_span_style"] = "set_span_style"
    region_id: str
    occurrence_id: str
    style: TextStylePatch


Edit = Annotated[
    SetRegionStyle | ReplaceRichText | SetGeometry | SetTranslation | SetSpanStyle
    | CreateRegion | DeleteRegion,
    Field(discriminator="op"),
]


RichEdit = Annotated[ReplaceRichText | SetSpanStyle, Field(discriminator="op")]


class TerminologyRule(ToolModel):
    source: str
    translation: str
    note: str | None = None


class StylePolicyPatch(ToolModel):
    terminology: list[TerminologyRule] | None = None
    font_families: list[str] | None = None
    region_style: RegionStylePatch | None = None
    text_style: TextStylePatch | None = None
    instructions: list[str] | None = None
    source_scope_id: str | None = None

    @field_validator("font_families")
    @classmethod
    def families_not_paths(cls, value: list[str] | None) -> list[str] | None:
        if value is not None:
            for family in value:
                TextStylePatch.family_not_path(family)
        return value
