"""有限中文表达的共同结构化层；原文和索引不经过改写。

索引使用 Python Unicode 字符位置（左闭右开），不是 JavaScript UTF-16 位置。
只支持当前词表和单对象分句，不推断医学诊断。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
import re
from typing import Any


ANATOMY_TERMS = ("肺上叶", "肺中叶", "肺下叶")
FINDING_TERMS = ("结节", "炎性改变")
SEGMENT_PATTERN = re.compile(r"[^。；;\n]+")
LENGTH_UNIT_PATTERN = r"(?:mm|cm|毫米|厘米)"
MEASUREMENT_PATTERN = re.compile(
    rf"(?<![\d.\-+])\d+(?:\.\d+)?\s*{LENGTH_UNIT_PATTERN}(?![A-Za-z0-9²³])", re.I
)
EXPLICIT_TIME_PATTERN = re.compile(
    r"既往|曾经|曾见|原为|现为|原来|上次|前次|较前|去年|前年|今年|"
    r"术前|术后|治疗前|治疗后|切除前|切除后|(?:19|20)[0-9]{2}\s*年"
)
CHINESE_COUNT_PATTERN = re.compile(
    r"([零〇一二三四五六七八九十百千万两俩数几]+(?:余|多)?|若干)\s*"
    r"(?:枚|颗|处|个(?=\s*(?:结节|炎性改变)))"
)


@dataclass(frozen=True)
class Measurement:
    raw: str
    value: str
    unit: str
    normalized_mm: str
    span_start: int
    span_end: int


@dataclass(frozen=True)
class ReportFinding:
    source: str
    evidence: str
    span_start: int
    span_end: int
    side: str
    anatomy: str
    observation: str
    assertion: str
    measurement: Measurement | None
    measurement_skip_reason: str | None
    comparison_skip_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ParsedReport:
    findings: tuple[ReportFinding, ...]
    skipped: tuple[dict[str, Any], ...]
    # 不能把无法解析的同类片段直接丢掉，然后把剩下一个对象误当唯一对象。
    pairing_blockers: tuple[dict[str, Any], ...] = ()


def extract_side(segment: str) -> str | None:
    # “8mm左右”的“左右”是近似词，不是左右两侧；尺寸仍会被单独拒绝比较。
    segment = re.sub(rf"(\d+(?:\.\d+)?\s*{LENGTH_UNIT_PATTERN})\s*左右", r"\1", segment, flags=re.I)
    if any(term in segment for term in ("双侧", "双肺", "左右")):
        return None
    has_left, has_right = "左" in segment, "右" in segment
    if has_left == has_right:
        return None
    return "left" if has_left else "right"


def extract_assertion(segment: str, observation: str) -> str:
    """对象前局部表达加受限的对象后“不存在”；复杂语句不猜。"""
    before = segment[:segment.index(observation)]
    clause = re.split(r"[，,：:]", before)[-1]
    if re.search(r"排除|不除外|考虑|疑似|可能|待排|未见明显|无明显|未见明确|未发现明显|未发现明确|无明确|没有明显|没有明确", clause):
        return "uncertain"
    if re.search(r"未见|未发现|无|没有", clause):
        # 双重否定/非目标表达不当成明确否定。
        if re.search(r"并非|不是|不无|不能|非无", clause):
            return "uncertain"
        if re.search(rf"(?:未见|未发现|无|没有)\s*(?:\d+(?:\.\d+)?\s*{LENGTH_UNIT_PATTERN})?\s*$", clause, re.I):
            return "absent"
        # “无钙化结节”可能只否定钙化，不确认病灶不存在。
        return "unknown"
    if re.search(r"不|非|否认", clause):
        return "unknown"
    after = segment[segment.index(observation) + len(observation):]
    if re.search(r"排除|不除外|考虑|疑似|可能|待排|并非|不是|不无|不能|非无", after):
        return "uncertain"
    # 必须是紧跟对象的完整谓语；“不存在恶性征象”并非病灶不存在。
    if re.fullmatch(r"\s*不存在\s*", after):
        return "absent"
    # 只去掉明确的稳定性表达，再检查剩余否定；不能一个“未增大”掩盖另一处否定。
    remaining = re.sub(r"未见明显增大|未见增大|无明显增大|没有明显增大|没有增大|不增大", "", after)
    if re.search(r"未见|未发现|无|没有|不|非|否认", remaining):
        return "unknown"
    return "present"


def extract_measurement(segment: str, offset: int) -> tuple[Measurement | None, str | None]:
    """先拒绝范围/近似/多值等表达，再将中英文单值长度归一到毫米。"""
    if (any(char.isdecimal() and char not in "0123456789" for char in segment) or
            re.search(r"\d\s*[,，．]\s*\d|[−－＋﹣﹢＜＞]\s*\d", segment)):
        return None, "非ASCII数字或特殊数字格式暂未支持，不能截取部分数值"
    if re.search(r"(?:最大|最小|平均|长|短|横|纵|前后|上下|左右)(?:直)?径", segment):
        return None, "带测量轴/最大最小限定的长度暂未比较"
    if EXPLICIT_TIME_PATTERN.search(segment):
        return None, "明确时间/历史描述暂未支持跨时间配对"
    matches = list(MEASUREMENT_PATTERN.finditer(segment))
    if re.search(rf"\d\s*(?:[xX×*～~—–-]|至|到)\s*\d|\d\s*{LENGTH_UNIT_PATTERN}\s*(?:[xX×*～~—–-]|至|到)", segment, re.I):
        return None, "范围值或多径测量暂未支持"
    if re.search(r"\d[eE][+-]?\d", segment):
        return None, "科学计数表达暂未支持"
    if len(matches) != 1:
        return None, "没有单一受支持长度" if not matches else "一个片段中有多个测量值"
    match = matches[0]
    prefix = segment[max(0, match.start() - 8):match.start()]
    suffix = segment[match.end():match.end() + 8]
    if re.search(r"约|大概|近|[<>≤≥+-]|大于|小于|至少|至多|超过|不足", prefix) or re.match(r"\s*(?:左右|上下|余|以下|以上|以内|以外|/|每|\^|平方|立方)", suffix):
        return None, "近似值、比较值或非长度单位暂未支持"
    if re.search(r"较前|既往|原为|现为|原来|增大|缩小|上次|前次", segment):
        return None, "历史对比/变化描述暂未支持"
    raw = match.group()
    number, unit = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(mm|cm|毫米|厘米)", raw, re.I).groups()
    if len(number.replace(".", "")) > 18:
        return None, "测量数字超过本版支持长度"
    value = Decimal(number)
    if value <= 0:
        return None, "长度必须大于零"
    unit = {"毫米": "mm", "厘米": "cm"}.get(unit.lower(), unit.lower())
    normalized = value * (Decimal("10") if unit == "cm" else Decimal("1"))
    return Measurement(raw, number, unit, format(normalized, "f"), offset + match.start(), offset + match.end()), None


def parse_report(text: str, source: str) -> ParsedReport:
    """一句一个明确对象才记录；被跳过的原文和原因也保留。"""
    findings, skipped, blockers = [], [], []
    for match in SEGMENT_PATTERN.finditer(text):
        raw = match.group()
        segment = raw.strip()
        if not segment:
            continue
        start = match.start() + len(raw) - len(raw.lstrip())
        end = start + len(segment)
        side = extract_side(segment)
        anatomy = [term for term in ANATOMY_TERMS if term in segment]
        observation = [term for term in FINDING_TERMS if term in segment]
        # 重复同类对象也拒绝；一个词表命中不代表只有一个病灶。
        ambiguous = any(segment.count(term) > 1 for term in observation) or any(
            term in segment for term in ("多发", "多个", "数个", "两枚", "两处", "两颗")
        ) or any(count.group(1) != "一" for count in CHINESE_COUNT_PATTERN.finditer(segment)
        ) or any(int(count.group(1)) > 1 for count in re.finditer(
            r"(\d{1,4})\s*(?:枚|颗|处|个(?=\s*(?:结节|炎性改变)))", segment
        ))
        if side is None or len(anatomy) != 1 or len(observation) != 1 or ambiguous:
            skipped.append({"source": source, "evidence": segment, "span_start": start, "span_end": end,
                            "reason": "侧别、部位或对象不唯一/未识别"})
            for site in anatomy:
                for item in observation:
                    blockers.append({"anatomy": site, "observation": item, "side": side,
                                     "reason": "同类片段存在不唯一/未识别对象，不能过滤后猜测配对"})
            continue
        measurement, reason = extract_measurement(segment, start)
        findings.append(ReportFinding(source, segment, start, end, side, anatomy[0], observation[0],
                                      extract_assertion(segment, observation[0]), measurement, reason,
                                      "明确时间/历史描述暂未支持跨时间配对" if EXPLICIT_TIME_PATTERN.search(segment) else None))
    return ParsedReport(tuple(findings), tuple(skipped), tuple(blockers))
