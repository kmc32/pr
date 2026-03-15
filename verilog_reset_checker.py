#!/usr/bin/env python3
"""
Find signals assigned with non-blocking assignments inside Verilog always blocks
that are missing from the reset branch.

Rules:
1. Only inspect `always` blocks whose sensitivity list contains `rst_n`.
2. Inside those blocks, collect all `<=` assignment LHS signals.
3. Find the reset branch driven by a condition involving `rst_n`.
4. Report signals assigned with `<=` in the always block but not in that reset branch.
"""

from __future__ import annotations

import argparse
import dataclasses
import pathlib
import re
import sys
from typing import Iterable, List, Optional, Sequence, Tuple


ALWAYS_RE = re.compile(r"\balways\b", re.IGNORECASE)
IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
NONBLOCKING_LHS_RE = re.compile(r"(?P<lhs>[A-Za-z_][A-Za-z0-9_$]*(?:\[[^\]]+\])?)\s*<=")
DEFAULT_EXTENSIONS = (".v", ".sv", ".vh", ".svh")


@dataclasses.dataclass
class Statement:
    kind: str
    start: int
    end: int
    text: str
    condition: Optional[str] = None
    then_branch: Optional["Statement"] = None
    else_branch: Optional["Statement"] = None
    children: Optional[List["Statement"]] = None


@dataclasses.dataclass
class AlwaysBlock:
    path: pathlib.Path
    line: int
    sensitivity: str
    body: Statement


@dataclasses.dataclass
class Finding:
    path: pathlib.Path
    line: int
    sensitivity: str
    missing_signals: List[str]
    all_assigned_signals: List[str]
    reset_assigned_signals: List[str]


def strip_comments(text: str) -> str:
    result: List[str] = []
    i = 0
    in_line = False
    in_block = False
    while i < len(text):
        if in_line:
            if text[i] == "\n":
                in_line = False
                result.append("\n")
            else:
                result.append(" ")
            i += 1
            continue
        if in_block:
            if text[i : i + 2] == "*/":
                result.extend("  ")
                i += 2
                in_block = False
            else:
                result.append("\n" if text[i] == "\n" else " ")
                i += 1
            continue
        if text[i : i + 2] == "//":
            result.extend("  ")
            i += 2
            in_line = True
            continue
        if text[i : i + 2] == "/*":
            result.extend("  ")
            i += 2
            in_block = True
            continue
        result.append(text[i])
        i += 1
    return "".join(result)


class Parser:
    def __init__(self, text: str):
        self.text = text

    def skip_ws(self, pos: int) -> int:
        while pos < len(self.text) and self.text[pos].isspace():
            pos += 1
        return pos

    def match_keyword(self, pos: int, keyword: str) -> bool:
        if not self.text[pos : pos + len(keyword)].lower() == keyword.lower():
            return False
        before_ok = pos == 0 or not (self.text[pos - 1].isalnum() or self.text[pos - 1] in "_$")
        after = pos + len(keyword)
        after_ok = after >= len(self.text) or not (self.text[after].isalnum() or self.text[after] in "_$")
        return before_ok and after_ok

    def parse_paren_group(self, pos: int) -> Tuple[str, int]:
        pos = self.skip_ws(pos)
        if pos >= len(self.text) or self.text[pos] != "(":
            raise ValueError(f"Expected '(' at position {pos}")
        depth = 1
        i = pos + 1
        while i < len(self.text) and depth:
            if self.text[i] == "(":
                depth += 1
            elif self.text[i] == ")":
                depth -= 1
            i += 1
        if depth != 0:
            raise ValueError("Unbalanced parentheses")
        return self.text[pos + 1 : i - 1], i

    def parse_until_semicolon(self, pos: int) -> int:
        i = pos
        while i < len(self.text):
            if self.text[i] == ";":
                return i + 1
            i += 1
        raise ValueError("Missing semicolon")

    def parse_case(self, pos: int) -> Statement:
        start = pos
        if self.match_keyword(pos, "case"):
            keyword = "case"
        elif self.match_keyword(pos, "casex"):
            keyword = "casex"
        elif self.match_keyword(pos, "casez"):
            keyword = "casez"
        else:
            raise ValueError(f"Expected case statement at position {pos}")

        i = pos + len(keyword)
        depth = 1
        while i < len(self.text):
            i = self.skip_ws(i)
            if i >= len(self.text):
                break
            if self.match_keyword(i, "case") or self.match_keyword(i, "casex") or self.match_keyword(i, "casez"):
                depth += 1
                i += 4
                continue
            if self.match_keyword(i, "endcase"):
                depth -= 1
                i += len("endcase")
                if depth == 0:
                    return Statement("case", start, i, self.text[start:i])
                continue
            i += 1
        raise ValueError("Missing endcase")

    def parse_block(self, pos: int) -> Statement:
        start = pos
        pos += len("begin")
        children: List[Statement] = []
        while True:
            pos = self.skip_ws(pos)
            if pos >= len(self.text):
                raise ValueError("Missing end for begin block")
            if self.match_keyword(pos, "end"):
                end = pos + len("end")
                return Statement("block", start, end, self.text[start:end], children=children)
            child = self.parse_statement(pos)
            children.append(child)
            pos = child.end

    def parse_if(self, pos: int) -> Statement:
        start = pos
        pos += len("if")
        condition, pos = self.parse_paren_group(pos)
        then_branch = self.parse_statement(self.skip_ws(pos))
        pos = self.skip_ws(then_branch.end)
        else_branch = None
        if pos < len(self.text) and self.match_keyword(pos, "else"):
            pos += len("else")
            else_branch = self.parse_statement(self.skip_ws(pos))
            pos = else_branch.end
        return Statement(
            "if",
            start,
            pos,
            self.text[start:pos],
            condition=condition,
            then_branch=then_branch,
            else_branch=else_branch,
        )

    def parse_statement(self, pos: int) -> Statement:
        pos = self.skip_ws(pos)
        if self.match_keyword(pos, "begin"):
            return self.parse_block(pos)
        if self.match_keyword(pos, "if"):
            return self.parse_if(pos)
        if (
            self.match_keyword(pos, "case")
            or self.match_keyword(pos, "casex")
            or self.match_keyword(pos, "casez")
        ):
            return self.parse_case(pos)
        end = self.parse_until_semicolon(pos)
        return Statement("simple", pos, end, self.text[pos:end])


def line_number_for_offset(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def normalize_lhs(lhs: str) -> str:
    lhs = lhs.strip()
    bracket = lhs.find("[")
    if bracket != -1:
        lhs = lhs[:bracket]
    return lhs


def collect_nonblocking_lhs(text: str) -> List[str]:
    seen = []
    added = set()
    for match in NONBLOCKING_LHS_RE.finditer(text):
        name = normalize_lhs(match.group("lhs"))
        if name not in added:
            added.add(name)
            seen.append(name)
    return seen


def is_reset_asserted_condition(condition: str) -> bool:
    cond = re.sub(r"\s+", "", condition.lower())
    reset_low_patterns = (
        "!rst_n",
        "~rst_n",
        "rst_n==1'b0",
        "rst_n==1'h0",
        "rst_n==1'd0",
        "rst_n==0",
        "1'b0==rst_n",
        "1'h0==rst_n",
        "1'd0==rst_n",
        "0==rst_n",
        "rst_n!=1'b1",
        "rst_n!=1'h1",
        "rst_n!=1'd1",
        "rst_n!=1",
    )
    return any(pattern in cond for pattern in reset_low_patterns)


def is_reset_released_condition(condition: str) -> bool:
    cond = re.sub(r"\s+", "", condition.lower())
    reset_high_patterns = (
        "rst_n",
        "rst_n==1'b1",
        "rst_n==1'h1",
        "rst_n==1'd1",
        "rst_n==1",
        "1'b1==rst_n",
        "1'h1==rst_n",
        "1'd1==rst_n",
        "1==rst_n",
    )
    if any(pattern == cond for pattern in reset_high_patterns):
        return True
    if any(pattern in cond for pattern in reset_high_patterns[1:]):
        return True
    return False


def find_reset_branch(statement: Statement) -> Optional[Statement]:
    if statement.kind == "if" and statement.condition and re.search(r"\brst_n\b", statement.condition):
        if is_reset_asserted_condition(statement.condition):
            return statement.then_branch
        if is_reset_released_condition(statement.condition):
            return statement.else_branch
    if statement.kind == "block" and statement.children:
        for child in statement.children:
            branch = find_reset_branch(child)
            if branch is not None:
                return branch
    return None


def parse_always_blocks(path: pathlib.Path, text: str) -> List[AlwaysBlock]:
    cleaned = strip_comments(text)
    parser = Parser(cleaned)
    blocks: List[AlwaysBlock] = []

    for match in ALWAYS_RE.finditer(cleaned):
        pos = parser.skip_ws(match.end())
        if pos >= len(cleaned) or cleaned[pos] != "@":
            continue
        pos = parser.skip_ws(pos + 1)
        if pos >= len(cleaned) or cleaned[pos] != "(":
            continue
        try:
            sensitivity, pos = parser.parse_paren_group(pos)
            if not re.search(r"\brst_n\b", sensitivity):
                continue
            body = parser.parse_statement(pos)
        except ValueError:
            continue
        blocks.append(
            AlwaysBlock(
                path=path,
                line=line_number_for_offset(cleaned, match.start()),
                sensitivity=sensitivity.strip(),
                body=body,
            )
        )
    return blocks


def analyze_file(path: pathlib.Path) -> List[Finding]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    findings: List[Finding] = []
    for block in parse_always_blocks(path, text):
        all_assigned = collect_nonblocking_lhs(block.body.text)
        if not all_assigned:
            continue
        reset_branch = find_reset_branch(block.body)
        if reset_branch is None:
            continue
        reset_assigned = collect_nonblocking_lhs(reset_branch.text)
        missing = [signal for signal in all_assigned if signal not in set(reset_assigned)]
        if missing:
            findings.append(
                Finding(
                    path=path,
                    line=block.line,
                    sensitivity=block.sensitivity,
                    missing_signals=missing,
                    all_assigned_signals=all_assigned,
                    reset_assigned_signals=reset_assigned,
                )
            )
    return findings


def iter_verilog_files(paths: Sequence[str], extensions: Sequence[str]) -> Iterable[pathlib.Path]:
    for raw in paths:
        path = pathlib.Path(raw)
        if path.is_file():
            yield path
            continue
        if path.is_dir():
            for file_path in sorted(path.rglob("*")):
                if file_path.is_file() and file_path.suffix.lower() in extensions:
                    yield file_path


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Find non-blocking assigned signals that are missing in reset branches."
    )
    parser.add_argument("paths", nargs="+", help="Verilog files or directories to scan")
    parser.add_argument(
        "--extensions",
        nargs="*",
        default=list(DEFAULT_EXTENSIONS),
        help=f"File extensions to include, default: {' '.join(DEFAULT_EXTENSIONS)}",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    files = list(iter_verilog_files(args.paths, tuple(ext.lower() for ext in args.extensions)))
    if not files:
        print("No matching Verilog files found.", file=sys.stderr)
        return 1

    total_findings = 0
    for path in files:
        findings = analyze_file(path)
        if not findings:
            continue
        total_findings += len(findings)
        for item in findings:
            print(f"{item.path}:{item.line}")
            print(f"  sensitivity: {item.sensitivity}")
            print(f"  missing_in_reset: {', '.join(item.missing_signals)}")
            print(f"  all_nonblocking: {', '.join(item.all_assigned_signals)}")
            print(f"  reset_branch_nonblocking: {', '.join(item.reset_assigned_signals) or '(none)'}")

    if total_findings == 0:
        print("No missing reset assignments found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
