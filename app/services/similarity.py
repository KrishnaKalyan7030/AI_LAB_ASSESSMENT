"""Token-based code similarity checker (no AI involved).

Source code is tokenised with Python's own tokenizer and normalised so that
renaming variables, changing literal values, reformatting or editing comments
does not hide copied structure:

* identifiers -> ID (keywords and common builtins are kept as-is)
* numbers -> NUM, strings -> STR
* comments, blank lines and whitespace are dropped

Two scores are combined:
* sequence similarity (difflib) on the normalised token streams, which also
  yields the matching sections reported to the teacher;
* Jaccard overlap of 5-token fingerprints, which is robust to re-ordering.

A high score is a signal for HUMAN REVIEW, not proof of plagiarism: short
programs, shared starter code and standard algorithms naturally look alike.
"""
from __future__ import annotations

import builtins
import difflib
import io
import keyword
import re
import tokenize
from dataclasses import dataclass, field

KEEP_NAMES = set(keyword.kwlist) | {n for n in dir(builtins) if not n.startswith("_")}
KGRAM = 5
MIN_MATCH_TOKENS = 8

HUMAN_REVIEW_NOTE = (
    "Similarity scores are computed from normalised code tokens. They indicate code that "
    "may share structure and always require human review: standard algorithms, starter "
    "code and short programs can be legitimately similar."
)


@dataclass
class Token:
    kind: str
    line: int


@dataclass
class SimilarityResult:
    similarity: float           # 0..1 combined score
    sequence_ratio: float
    fingerprint_overlap: float
    matching_sections: list = field(default_factory=list)
    flagged: bool = False

    @property
    def percent(self) -> int:
        return round(self.similarity * 100)


def _fallback_tokens(code: str) -> list:
    out = []
    for lineno, line in enumerate(code.splitlines(), 1):
        line = line.split("#", 1)[0]
        for tok in re.findall(r"[A-Za-z_]\w*|\d+(?:\.\d+)?|\"[^\"]*\"|'[^']*'|\S", line):
            if tok[0].isdigit():
                out.append(Token("NUM", lineno))
            elif tok[0] in "\"'":
                out.append(Token("STR", lineno))
            elif tok[0].isalpha() or tok[0] == "_":
                out.append(Token(tok if tok in KEEP_NAMES else "ID", lineno))
            else:
                out.append(Token(tok, lineno))
    return out


def normalize_tokens(code: str) -> list:
    tokens = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(code).readline):
            ttype, text, (line, _col) = tok.type, tok.string, tok.start
            if ttype in (tokenize.COMMENT, tokenize.NL, tokenize.ENCODING, tokenize.ENDMARKER):
                continue
            if ttype == tokenize.NAME:
                kind = text if text in KEEP_NAMES else "ID"
            elif ttype == tokenize.NUMBER:
                kind = "NUM"
            elif ttype == tokenize.STRING or ttype in {getattr(tokenize, "FSTRING_START", -1)}:
                kind = "STR"
            elif ttype in {getattr(tokenize, "FSTRING_MIDDLE", -1), getattr(tokenize, "FSTRING_END", -1)}:
                continue
            elif ttype == tokenize.NEWLINE:
                kind = "NEWLINE"
            elif ttype == tokenize.INDENT:
                kind = "INDENT"
            elif ttype == tokenize.DEDENT:
                kind = "DEDENT"
            else:
                kind = text
            tokens.append(Token(kind, line))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return _fallback_tokens(code)
    return tokens


def _fingerprints(kinds: list) -> set:
    if len(kinds) < KGRAM:
        return {tuple(kinds)} if kinds else set()
    return {tuple(kinds[i:i + KGRAM]) for i in range(len(kinds) - KGRAM + 1)}


def compare(code_a: str, code_b: str, threshold: float = 0.75) -> SimilarityResult:
    ta, tb = normalize_tokens(code_a), normalize_tokens(code_b)
    ka, kb = [t.kind for t in ta], [t.kind for t in tb]
    if not ka or not kb:
        return SimilarityResult(0.0, 0.0, 0.0)
    matcher = difflib.SequenceMatcher(None, ka, kb, autojunk=False)
    ratio = matcher.ratio()
    fa, fb = _fingerprints(ka), _fingerprints(kb)
    jaccard = len(fa & fb) / len(fa | fb) if fa | fb else 0.0
    combined = 0.6 * ratio + 0.4 * jaccard

    lines_a, lines_b = code_a.splitlines(), code_b.splitlines()
    sections = []
    for block in matcher.get_matching_blocks():
        if block.size < MIN_MATCH_TOKENS:
            continue
        a_start, a_end = ta[block.a].line, ta[block.a + block.size - 1].line
        b_start, b_end = tb[block.b].line, tb[block.b + block.size - 1].line
        sections.append({
            "tokens": block.size,
            "a_lines": [a_start, a_end],
            "b_lines": [b_start, b_end],
            "a_excerpt": "\n".join(lines_a[a_start - 1:a_end])[:1500],
            "b_excerpt": "\n".join(lines_b[b_start - 1:b_end])[:1500],
        })
    sections.sort(key=lambda s: -s["tokens"])
    return SimilarityResult(
        similarity=round(combined, 4),
        sequence_ratio=round(ratio, 4),
        fingerprint_overlap=round(jaccard, 4),
        matching_sections=sections[:10],
        flagged=combined >= threshold,
    )
