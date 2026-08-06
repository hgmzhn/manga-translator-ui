import argparse
import json
from datetime import date
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


ROOT = Path(__file__).resolve().parents[2]
WIKI_DIR = ROOT / "doc" / "wiki"
DATA_PATH = WIKI_DIR / "data" / "related-projects.yml"
SCHEMA_PATH = WIKI_DIR / "data" / "related-projects.schema.json"

NonEmptyString = Annotated[str, Field(min_length=1)]
HttpsUrl = Annotated[str, Field(min_length=1, pattern=r"^https://[^\s]+$")]


def validate_https_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("must be an HTTPS URL with a host")
    return value


class LocalizedText(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    en: NonEmptyString
    zh_cn: NonEmptyString = Field(alias="zh-CN")

    @field_validator("en", "zh_cn")
    @classmethod
    def reject_whitespace_only(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class Logo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: HttpsUrl
    authorization: NonEmptyString

    @field_validator("authorization")
    @classmethod
    def reject_whitespace_only(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("url")
    @classmethod
    def require_https_url(cls, value: str) -> str:
        return validate_https_url(value)


class RelatedProject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
    name: LocalizedText
    description: LocalizedText
    url: HttpsUrl
    relationship: LocalizedText
    category: Literal["translation", "ocr", "typesetting", "image-processing", "community", "tooling"]
    logo: Logo
    contact_url: HttpsUrl
    license_status: NonEmptyString
    approval_status: Literal["pending", "approved"]
    last_checked: date

    @field_validator("license_status")
    @classmethod
    def reject_whitespace_only(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("url", "contact_url")
    @classmethod
    def require_https_url(cls, value: str) -> str:
        return validate_https_url(value)


class RelatedProjectsDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    projects: list[RelatedProject] = Field(default_factory=list)


def schema_text() -> str:
    return json.dumps(RelatedProjectsDocument.model_json_schema(), ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Wiki related-projects data and schema.")
    parser.add_argument("--write-schema", action="store_true", help="regenerate the checked-in JSON Schema")
    arguments = parser.parse_args()

    expected_schema = schema_text()
    if arguments.write_schema:
        SCHEMA_PATH.write_text(expected_schema, encoding="utf-8")
    elif SCHEMA_PATH.read_text(encoding="utf-8") != expected_schema:
        raise AssertionError("related-projects.schema.json is stale; run with --write-schema")

    document = yaml.safe_load(DATA_PATH.read_text(encoding="utf-8"))
    validated = RelatedProjectsDocument.model_validate(document)
    approved = sum(project.approval_status == "approved" for project in validated.projects)
    print(f"PASS: projects={len(validated.projects)}, approved={approved}, schema_version=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
