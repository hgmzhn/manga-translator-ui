"""Semantic, Glossary, OCR-Corruption, Number-Drop, and Polarity QA Auditor for Manga Translations."""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import yaml

from .glossary_manager import resolve_glossary_path
from .richtext_builder import visible_text_of_region

# Visually confusable character groups in vertical Japanese manga OCR (`manga_ocr` / `48px`)
_OCR_CONFUSABLE_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"シ", "ン", "ジ", "ソ", "ツ", "ッ"}),
    frozenset({"フ", "ブ", "プ", "ワ", "ラ", "ヲ"}),
    frozenset({"ハ", "バ", "パ", "八"}),
    frozenset({"ヒ", "ビ", "ピ"}),
    frozenset({"ヘ", "ベ", "ペ", "へ", "べ", "ぺ"}),
    frozenset({"ホ", "ボ", "ポ"}),
    frozenset({"カ", "ガ", "力"}),
    frozenset({"キ", "ギ"}),
    frozenset({"ク", "グ", "ケ", "ゲ", "タ", "ダ", "夕", "ツ"}),
    frozenset({"コ", "ゴ", "ユ", "ヨ", "ロ", "口"}),
    frozenset({"サ", "ザ"}),
    frozenset({"ス", "ズ", "ヌ", "マ", "ム"}),
    frozenset({"セ", "ゼ"}),
    frozenset({"チ", "ヂ", "テ", "デ"}),
    frozenset({"ト", "ド", "卜", "イ"}),
    frozenset({"ウ", "ヴ"}),
    frozenset({"ル", "レ", "儿"}),
    frozenset({"ー", "一", "―", "—"}),
    frozenset({"ア", "ァ", "マ"}),
    frozenset({"イ", "ィ"}),
    frozenset({"エ", "ェ", "工"}),
    frozenset({"オ", "ォ"}),
    frozenset({"ヤ", "ャ"}),
    frozenset({"ユ", "ュ"}),
    frozenset({"ヨ", "ョ"}),
)

_KATAKANA_RUN_RE = re.compile(r"[\u30a0-\u30ffー・]{3,}")
_PURE_KATAKANA_RE = re.compile(r"^[\u30a0-\u30ffー・]+$")
_TOC_OR_HEADER_SKIP_RE = re.compile(
    r"(?:quest\s*\d+|chapter\s*\d+|contents|ソード・オラトリア\s*(?:\d+|[①-⑳]))",
    re.IGNORECASE,
)

# Vertical TCY `Lv.X` misread by OCR (`大半かい２`, `唯一の以３`, `能力はい２昇格`)
_VERT_LV_OCR_CORRUPT_RE = re.compile(
    r"(?:(?:大半|唯一|能力|現在|推定|平均|全員|相手|奴ら|連中)?(?:かい|がい|のい|は以|の以|はい)|(?<![ァ-ヶ])以)[1-9１-９](?![\d０-９階分秒月日歳年人回つ倍割])"
)

# High-frequency OCR corruption patterns that directly trigger LLM hallucinated mistranslations
_OCR_HALLUCINATION_TRAPS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"プチのめ"), "OCR misread 'ブチのめす' (beat up / 揍扁) as 'プチのめ' (causing '小地…' mistranslation)"),
    (re.compile(r"[\d０-９]+秒甸"), "OCR misread '毎' (ごと / 每一秒) as '甸'"),
    (re.compile(r"ヨつの"), "OCR misread '四つの' (四个) as 'ヨつの'"),
    (re.compile(r"\ufffd"), "OCR output contains corrupted replacement character ''"),
    (re.compile(r"(?:についてし|負けたんじ|こさえるよい)$"), "OCR truncated or garbled the final column characters"),
    (
        re.compile(r"[\u4e00-\u9fff][\u30a0-\u30ff]{4,}[\u4e00-\u9fff]"),
        "Furigana (ruby) katakana appears interleaved inside kanji word — verify Raw image",
    ),
)

_PROHIBITION_JP_RE = re.compile(r"[るうくぐすつぬぶむ]んじゃ(?:ねえ|ねぇ|ない|ないわ)(?![か？\?])")
_PROHIBITION_ZH_MARKERS = ("别", "不要", "不准", "不许", "少", "莫", "勿", "休", "甭", "干嘛", "怎么能", "哪有", "不带")

_CLAUSE_START_UUN_RE = re.compile(r"^[…‥\s]*ううん(?![、…\s]*(?:そう|わかっ))")
_AFFIRMATIVE_START_ZH_RE = re.compile(r"^[︙…\s]*(?:嗯|是啊|对|没错|好)(?![，、\s]*不)")

_JP_TRAILING_PAUSE_OR_CONJ_RE = re.compile(
    r"(?:[…‥\.・ー―—〜～っッ\?？!！]$|[…‥⋯]{1,}|"
    r"(?:だが|けど|けれど|けれども|のに|から|し|て|で|ば|たら|なら|ものの|とは|って|とか|たり|やら|かも|かな|かしら|な|ね|よ|ぞ|ぜ|わ|さ)[、。！？!?]*$)"
)

_SMALL_CN_DIGITS = {
    0: "零〇",
    1: "一壹",
    2: "二两兩双雙俩倆次",
    3: "三仨叁",
    4: "四肆",
    5: "五伍",
    6: "六陆陸",
    7: "七柒",
    8: "八捌",
    9: "九玖",
    10: "十拾",
}


@dataclass(frozen=True)
class GlossaryTerm:
    original: str
    translations: tuple[str, ...]
    category: str
    is_pure_katakana: bool


def _strip_mid_dots(s: str) -> str:
    return re.sub(r"[\s·・‧•]+", "", s)


def _chars_are_ocr_confusable(a: str, b: str) -> bool:
    if a == b:
        return True
    return any(a in grp and b in grp for grp in _OCR_CONFUSABLE_GROUPS)


def _int_to_chinese_forms(n: int) -> set[str]:
    forms: set[str] = {str(n), unicodedata.normalize("NFKC", str(n))}
    # Fullwidth Arabic digits
    forms.add("".join(chr(ord("０") + int(d)) for d in str(n)))
    if n in _SMALL_CN_DIGITS:
        forms.update(_SMALL_CN_DIGITS[n])
    elif 11 <= n <= 99:
        tens, ones = divmod(n, 10)
        t_prefix = "" if tens == 1 else "二三四五六七八九"[tens - 2]
        o_suffix = "" if ones == 0 else "一二三四五六七八九"[ones - 1]
        forms.add(f"{t_prefix}十{o_suffix}")
        if tens == 2 and ones == 0:
            forms.add("廿")
        elif tens == 3 and ones == 0:
            forms.add("卅")
    elif n in (100, 1000, 10000):
        forms.update({100: ("百", "一百"), 1000: ("千", "一千"), 10000: ("万", "一万")}[n])
    return {f for f in forms if f}


@lru_cache(maxsize=16)
def _load_cached_glossary(path_str: str, mtime_ns: int) -> tuple[GlossaryTerm, ...]:
    _ = mtime_ns
    p = Path(path_str)
    terms: list[GlossaryTerm] = []
    if not p.is_file():
        return ()
    try:
        if p.suffix.lower() == ".txt":
            for ln in p.read_text(encoding="utf-8").splitlines():
                s = ln.strip()
                if not s or s.startswith(("#", "//")):
                    continue
                parts = re.split(r"\t+|\s{2,}|\s+", s, maxsplit=1)
                if len(parts) == 2 and len(parts[0].strip()) >= 2 and parts[1].strip():
                    orig = parts[0].strip()
                    terms.append(
                        GlossaryTerm(
                            original=orig,
                            translations=(parts[1].strip(),),
                            category="TXT",
                            is_pure_katakana=bool(_PURE_KATAKANA_RE.match(orig)),
                        )
                    )
        else:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            g_dict = data.get("glossary") if isinstance(data, dict) else None
            if isinstance(g_dict, dict):
                for cat, items in g_dict.items():
                    if not isinstance(items, list):
                        continue
                    for entry in items:
                        if not isinstance(entry, dict):
                            continue
                        primary_orig = str(entry.get("original") or "").strip()
                        aliases = entry.get("aliases") if isinstance(entry.get("aliases"), list) else []
                        zh_cands: list[str] = []
                        orig_variants: list[str] = [primary_orig] if primary_orig else []
                        for al in aliases:
                            if not isinstance(al, dict):
                                continue
                            al_orig = str(al.get("original") or "").strip()
                            if al_orig and al_orig not in orig_variants:
                                orig_variants.append(al_orig)
                            for tr_item in al.get("translations") or []:
                                if isinstance(tr_item, dict):
                                    t_val = str(tr_item.get("text") or "").strip()
                                    if t_val and t_val not in zh_cands:
                                        zh_cands.append(t_val)
                                elif isinstance(tr_item, str) and tr_item.strip():
                                    if tr_item.strip() not in zh_cands:
                                        zh_cands.append(tr_item.strip())
                        if not zh_cands:
                            continue
                        for ov in orig_variants:
                            if len(ov) < 2:
                                continue
                            is_kata = bool(_PURE_KATAKANA_RE.match(ov))
                            # Skip generic 2-kanji common nouns in Item/Skill (e.g. `魔法`, `冒険`) that frequently adapt in context
                            if cat in ("Item", "Skill") and not is_kata and len(ov) <= 2:
                                continue
                            terms.append(
                                GlossaryTerm(
                                    original=ov,
                                    translations=tuple(zh_cands),
                                    category=str(cat),
                                    is_pure_katakana=is_kata,
                                )
                            )
    except Exception:
        return ()
    return tuple(terms)


def load_active_glossary_terms(glossary_file: Optional[str | Path] = None) -> tuple[GlossaryTerm, ...]:
    """Load glossary terms from explicit path or active MTUI `high_quality_prompt_path`."""
    if glossary_file is None and "PYTEST_CURRENT_TEST" in os.environ:
        return ()
    try:
        resolved = Path(glossary_file) if glossary_file else resolve_glossary_path(None)
        if not resolved.is_file():
            return ()
        return _load_cached_glossary(str(resolved.resolve()), resolved.stat().st_mtime_ns)
    except Exception:
        return ()


def _source_contains_term(src_txt: str, term: GlossaryTerm) -> bool:
    if term.original not in src_txt:
        return False
    if term.is_pure_katakana and len(term.original) <= 3:
        # Enforce katakana word boundary so `シルエット` never matches `シル` and `ベルト` never matches `ベル`!
        pat = rf"(?<![\u30a0-\u30ffー]){re.escape(term.original)}(?![\u30a0-\u30ffー])"
        return bool(re.search(pat, src_txt))
    return True


def audit_region_translation_accuracy(
    idx: int,
    reg: dict[str, Any],
    *,
    glossary_terms: tuple[GlossaryTerm, ...] = (),
) -> list[dict[str, Any]]:
    """Run deterministic mistranslation & OCR-corruption checks on a single region."""
    issues: list[dict[str, Any]] = []
    src_txt = str(reg.get("text", "") or "").strip()
    vis_txt = visible_text_of_region(reg).strip()
    if not src_txt or not vis_txt:
        return issues
    if _TOC_OR_HEADER_SKIP_RE.search(src_txt) or _TOC_OR_HEADER_SKIP_RE.search(vis_txt):
        return issues

    vis_flat = vis_txt.replace("\n", "")
    vis_nodot = _strip_mid_dots(vis_flat)

    # 1. Active Glossary Exact Match & OCR Confusable Proper-Noun Typo Detection
    if glossary_terms:
        exact_known_kata = {t.original for t in glossary_terms if t.is_pure_katakana}
        emitted_exact_origs: set[str] = set()
        for term in glossary_terms:
            if term.original in emitted_exact_origs:
                continue
            if _source_contains_term(src_txt, term):
                # Skip if a longer glossary super-term containing `term.original` is also present and satisfied
                longer_satisfied = any(
                    other.original != term.original
                    and term.original in other.original
                    and other.original in src_txt
                    and any(_strip_mid_dots(zt) in vis_nodot for zt in other.translations)
                    for other in glossary_terms
                )
                if longer_satisfied:
                    continue
                if not any(_strip_mid_dots(zt) in vis_nodot for zt in term.translations):
                    emitted_exact_origs.add(term.original)
                    issues.append(
                        {
                            "severity": "warning",
                            "category": "glossary_term_mismatch",
                            "region_index": idx,
                            "expected_terms": list(term.translations),
                            "message": (
                                f"Region #{idx} source contains glossary {term.category} term {term.original!r} "
                                f"(expected {'/'.join(term.translations)!r}), but translation is {vis_txt!r}!"
                            ),
                        }
                    )

        # 1b. Fuzzy 1-2 char OCR confusable katakana typo on proper nouns (e.g. `フィシ` -> `フィン`='芬恩' mistranslated as `菲西`!)
        proper_kata_terms = [
            t for t in glossary_terms if t.is_pure_katakana and len(t.original) >= 3 and t.category in ("Person", "Location", "Org", "Creature", "TXT")
        ]
        for m in _KATAKANA_RUN_RE.finditer(src_txt):
            tok = m.group(0).strip("・")
            if len(tok) < 3 or tok in exact_known_kata:
                continue
            tok_clean = tok.replace("・", "")
            for term in proper_kata_terms:
                ref_clean = term.original.replace("・", "")
                if len(tok_clean) != len(ref_clean) or tok_clean == ref_clean:
                    continue
                diffs = [(a, b) for a, b in zip(tok_clean, ref_clean) if a != b]
                max_allowed = 1 if len(ref_clean) < 5 else 2
                if 1 <= len(diffs) <= max_allowed and all(_chars_are_ocr_confusable(a, b) for a, b in diffs):
                    if not any(_strip_mid_dots(zt) in vis_nodot for zt in term.translations):
                        issues.append(
                            {
                                "severity": "error",
                                "category": "suspect_ocr_glossary_typo",
                                "region_index": idx,
                                "ocr_token": tok,
                                "likely_glossary_original": term.original,
                                "expected_terms": list(term.translations),
                                "message": (
                                    f"Region #{idx} source has {tok!r} — likely OCR misread of glossary {term.category} "
                                    f"{term.original!r} ({'/'.join(term.translations)!r}), and translation {vis_txt!r} lacks "
                                    f"{'/'.join(term.translations)!r} (possible phonetic hallucination)! Verify Raw image!"
                                ),
                            }
                        )
                    break

    # 2. OCR Corruption Signatures (`Lv.X` vertical TCY garble, `プチのめ`, `1秒甸`, ``, furigana-in-kanji, truncated tails)
    lv_m = _VERT_LV_OCR_CORRUPT_RE.search(src_txt)
    if lv_m and not re.search(r"(?:Lv|lv|LV|级|級)", vis_flat):
        issues.append(
            {
                "severity": "error",
                "category": "suspect_ocr_corruption_mistranslation",
                "region_index": idx,
                "message": (
                    f"Region #{idx} source has {lv_m.group(0)!r} in {src_txt!r} — classic vertical TCY 'Lv.X' OCR misread, "
                    f"and translation {vis_txt!r} dropped the level! Inspect Raw image at 1:1!"
                ),
            }
        )
    else:
        for pat, desc in _OCR_HALLUCINATION_TRAPS:
            hm = pat.search(src_txt)
            if hm:
                issues.append(
                    {
                        "severity": "warning",
                        "category": "suspect_ocr_corruption_mistranslation",
                        "region_index": idx,
                        "message": (
                            f"Region #{idx} source {src_txt!r} matched OCR-corruption trap {hm.group(0)!r} ({desc}) -> "
                            f"verify translation {vis_txt!r} directly against Raw artwork!"
                        ),
                    }
                )
                break

    # 3. Concrete Number / Quantity Drop (`n >= 2`, e.g. `2`, `3`, `21`, `30` dropped completely in translation)
    norm_src_nums = unicodedata.normalize("NFKC", src_txt)
    for nm in re.finditer(r"(?<!\d)\d{1,4}(?!\d)", norm_src_nums):
        n_val = int(nm.group(0))
        if n_val < 2:
            continue
        accepted_forms = _int_to_chinese_forms(n_val)
        if not any(f in vis_flat for f in accepted_forms):
            issues.append(
                {
                    "severity": "warning",
                    "category": "translation_number_mismatch",
                    "region_index": idx,
                    "missing_number": n_val,
                    "message": (
                        f"Region #{idx} source {src_txt!r} contains number {n_val}, which is completely absent from "
                        f"translation {vis_txt!r} — check for dropped level/quantity or truncated OCR!"
                    ),
                }
            )
            break

    # 4. Invented Trailing Ellipsis on Complete/Garbled Non-Ellipsis Source (`大半かい２` -> `大半都会︙`, `内容についてし` -> `关于内容的︙`)
    if (
        len(src_txt) >= 4
        and vis_flat.endswith(("︙", "…"))
        and (re.search(r"についてし$", src_txt) or not _JP_TRAILING_PAUSE_OR_CONJ_RE.search(src_txt))
    ):
        issues.append(
            {
                "severity": "warning",
                "category": "invented_ellipsis_truncated_translation",
                "region_index": idx,
                "message": (
                    f"Region #{idx} translation {vis_txt!r} ends with an ellipsis ('︙'), whereas source {src_txt!r} "
                    "has no trailing ellipsis or conjunctive pause — often a symptom of LLM guessing over truncated/garbled OCR! Verify Raw image!"
                ),
            }
        )

    # 5. Negation / Prohibition Reversal (`～んじゃねえ` without prohibition marker; clause-initial `ううん` translated as `嗯/是`)
    if _PROHIBITION_JP_RE.search(src_txt) and not any(m in vis_flat for m in _PROHIBITION_ZH_MARKERS):
        issues.append(
            {
                "severity": "warning",
                "category": "negation_or_prohibition_drop_suspect",
                "region_index": idx,
                "message": (
                    f"Region #{idx} source {src_txt!r} uses colloquial prohibition ('～んじゃねえ' = 'Don't ~!'), "
                    f"but translation {vis_txt!r} contains no Chinese prohibition/refutation marker ('别/不要/少…') — check for reversed meaning!"
                ),
            }
        )
    elif _CLAUSE_START_UUN_RE.search(src_txt) and _AFFIRMATIVE_START_ZH_RE.search(vis_flat):
        issues.append(
            {
                "severity": "warning",
                "category": "negation_or_prohibition_drop_suspect",
                "region_index": idx,
                "message": (
                    f"Region #{idx} source {src_txt!r} starts with 'ううん' (nasal negation = '不/才不是'), "
                    f"but translation {vis_txt!r} starts affirmatively ('嗯/是/对') — check for 'うん' vs 'ううん' polarity inversion!"
                ),
            }
        )

    # 6. Japanese Sentence-Final Modal Tail-Inversion Calque (`图1`: `跟神明\n编假话认罪\n都说了没用嘛︙` from `～っちゅーに/～ってば`)
    vis_lines = [ln.strip() for ln in vis_txt.split("\n") if ln.strip()]
    if (
        len(vis_lines) >= 2
        and re.match(r"^(?:都说了|不是说了|明明说了|早就说了)", vis_lines[-1])
        and not re.match(r"^(?:都说了|不是说了|明明说了|早就说了)", vis_lines[0])
    ):
        issues.append(
            {
                "severity": "warning",
                "category": "inverted_japanese_calque_syntax",
                "region_index": idx,
                "message": (
                    f"Region #{idx} ({vis_txt!r}) tacks modal frame '{vis_lines[-1][:3]}…' onto the final line "
                    "(Japanese sentence-final '～っちゅーに/～ってば' calque) — front '都说了' to the start of line 1 "
                    "(e.g. '都说了…是没有用的嘛') for natural Chinese syntax and balanced columns!"
                ),
            }
        )

    return issues
