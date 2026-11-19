"""Literal policy facts. File-wide defaults and item-local evidence have separate scopes."""

import re
from bisect import bisect_right
from collections.abc import Iterator
from datetime import date
from itertools import pairwise

ARTICLE = re.compile(r"^\s*第[一二三四五六七八九十百千零\u3007\d]+条(?:之[一二三四五六七八九十\d]+)?\s*")
UNITS = re.compile(r"[^。；;！？!?\r\n]+[。；;！？!?]?")
POLICY = (
    r"(?:实施细则|管理办法|管理制度|办法|制度|规定|细则|规程|规则|"
    r"条例|指引|准则|规范|通知|标准|指南|意见|指令|程序|文件)"
)
CURRENT = rf"(?:本{POLICY}|(?:修订后的|新修订的)本?{POLICY}|本次修订(?:后的{POLICY}|版|文本)?)"
SELF_BEFORE_DATE = re.compile(CURRENT + r"(?:[（(](?:试行|暂行|修订)[）)])?\s*(?:将|原|曾|最初|原定|首次)?\s*$")
ATTACHMENT = re.compile(
    r"^\s*(?:[【\u3014（(]\s*)?(?:附\s*[件录表页]|附(?=[：:]))(?=$|[\s：:、.\uff0e（(【\u3014一二三四五六七八九十\dA-Z])"
)
CITATION = re.compile(
    r"引述|引文|引用|援引|摘录|节录|摘引|摘自|原文如下|(?:旧|原)[^，,：:。\n]{0,24}(?:载明|记载|写道|规定(?=如下|[：:])|说明)"
)
HISTORY = re.compile(
    r"修订沿革|历史沿革|最初于|原定于|曾[自于]|至今已|实施以来|施行以来|[首第][^。，；\n]{0,4}次修订|历经[^。\n]{0,10}修订"
)
LINE = re.compile(r"[^\r\n]+")
MONTH_DAY = r"[0-9一二三四五六七八九十\u3007零]{1,3}"
DATE = (
    rf"(?P<year>[0-9\u3007○零一二三四五六七八九]{{4}})\s*(?:年|-)\s*(?P<month>{MONTH_DAY})\s*"
    rf"(?:月|-)\s*(?P<day>{MONTH_DAY})\s*日?"
)
OPERATIVE = re.compile(
    rf"(?:(?P<preposition>自|从|于)\s*)?{DATE}\s*(?:起|开始)?\s*(?:正式|开始|首次)?"
    r"(?:施行|实施|生效|执行|试行)(?=$|[。，,；;！!?？：:、”’」』）)\s…]|以来)"
)
RELATIVE_DATE = re.compile(CURRENT + r"(?:自|从)(?:公布|印发|发布|颁布|下发|批准|签发|发文)(?:之日|当日|日起)")
CN_DIGITS = str.maketrans("\u3007○零一二三四五六七八九", "000123456789")


def calendar_number(text: str) -> int:
    text = text.translate(CN_DIGITS)
    if "十" in text:
        tens, units = text.split("十", 1)
        return 10 * int(tens or "1") + int(units or "0")
    return int(text)


def quotation_boundaries(text: str) -> list[int]:
    """Sorted start/end offsets retain quotation context across lines and sentence stops."""
    opens = {"“": "”", "‘": "’", "「": "」", "『": "』", "（": "）", "(": ")", '"': '"'}
    stack = []
    boundaries = []
    for mark in re.finditer(r"""[“”‘’「」『』（）()"]""", text):
        char = mark.group()
        if stack and char == stack[-1]:
            stack.pop()
            if not stack:
                boundaries.append(mark.end())
        elif char in opens:
            if not stack:
                boundaries.append(mark.start())
            stack.append(opens[char])
    return boundaries


def sentences(text: str) -> Iterator[tuple[str, int, bool]]:
    """Keep exact offsets and remember a preceding attachment or quotation heading."""
    attachment = False
    cited_section = False
    for line in LINE.finditer(text):
        line_text = line.group().strip()
        if re.fullmatch(r"[（(]?正\s*文[）)]?|附件结束|(?:引文|摘录|引用)(?:结束|完)|以下为正文", line_text):
            attachment = cited_section = False
        attachment |= bool(ATTACHMENT.match(line_text))
        for part in UNITS.finditer(line.group()):
            sentence = part.group().strip()
            if not sentence:
                continue
            start = line.start() + part.start() + len(part.group()) - len(part.group().lstrip())
            yield sentence, start, attachment or cited_section
            if CITATION.search(sentence) and re.search(r"(?:如下[。；;]?|[：:])$", sentence):
                cited_section = True
            elif cited_section and re.search(r"[”’」』]", sentence):
                cited_section = False


def file_fact(value: str = "", quote: str = "", start: int = 0) -> dict:
    fact = {"value": value, "origin": "system", "quote": quote, "level": "document"}
    if quote:
        fact.update(start=start, end=start + len(quote))
    return fact


def date_candidates(sentence: str, start: int, external: bool, quotations: list[int]) -> list[tuple[dict, bool]]:
    matches = []
    for found in OPERATIVE.finditer(sentence):
        prefix = sentence[: found.start()].rstrip()
        cited = external or bool(bisect_right(quotations, start + found.start()) % 2) or bool(CITATION.search(prefix))
        subject = SELF_BEFORE_DATE.search(prefix)
        # A second date may share the first clause's subject (经修订，自…起施行).
        continued = bool(matches and found["preposition"] and re.search(r"[，,；;…]\s*$", prefix))
        cited_title = cited and re.search(rf"(?:[原旧该]{POLICY}|《[^》]+》)\s*$", prefix)
        bare_excerpt = cited and re.search(r'(?:^|[“‘「『"：:])\s*(?:第[一二三四五六七八九十百零\d]+条\s*)?$', prefix)
        approved = re.match(
            CURRENT + r"(?:已)?经[^。，；;]{1,60}(?:审议|通过|同意|批准|修订)[，,]\s*(?:定|将)?$",
            ARTICLE.sub("", prefix),
        )
        if not (subject or continued or cited_title or bare_excerpt or approved):
            continue
        try:
            value = date(*(calendar_number(found[k]) for k in ("year", "month", "day"))).isoformat()
        except ValueError:
            continue
        own_history = re.split(r"[，,]\s*(?:原|旧)" + POLICY, sentence[found.end() :], maxsplit=1)[0]
        historical = bool(
            HISTORY.search(own_history)
            or own_history.startswith("以来")
            or "首次" in found.group()
            or re.search(r"(?:最初|原定|曾|首次)\s*$", prefix)
        )
        matches.append((found, value, cited or historical))
    # Two dates can share a sentence and its grammatical subject. Split only
    # before the next operative clause so the history between them remains visible.
    cuts = [0]
    for previous, following in pairwise(matches):
        left, right = previous[0].end(), following[0].start()
        breaks = list(re.finditer(r"[，,；;…]", sentence[left:right]))
        cuts.append(left + breaks[-1].end() if breaks else right)
    cuts.append(len(sentence))
    options = []
    for index, (_, value, cautious) in enumerate(matches):
        at = cuts[index]
        quote = sentence[at : cuts[index + 1]].strip()
        offset = start + sentence.index(quote, at)
        options.append((file_fact(value, quote, offset), cautious))
    return options


def scope_value(sentence: str, *, file_only: bool = False) -> str:
    core = ARTICLE.sub("", sentence).rstrip("。；;！？!?").strip()
    subject = CURRENT if file_only else rf"(?:{CURRENT}|本[条章节款项])"
    match = re.fullmatch(
        subject + r"(?:[仅均只]?适用于|的?适用范围(?:为|包括|是|[：:]))\s*([^，,。；;]+)(?:[，,].*)?", core
    )
    if not match:
        match = re.fullmatch(r"([^，,：:不]+?)[，,]?\s*(?:均)?适用" + subject, core)
    if not match and not file_only:
        match = re.fullmatch(r"适用范围\s*[：:]\s*(.+)", core)
    return match.group(1).strip() if match else ""


def document_facts(text: str) -> dict:
    dates = []
    scope = file_fact()
    quotations = quotation_boundaries(text)
    relative = False
    for sentence, start, external in sentences(text):
        dates.extend(date_candidates(sentence, start, external, quotations))
        if not external and not bisect_right(quotations, start) % 2:
            relative |= bool(RELATIVE_DATE.search(sentence))
            if not scope["value"]:
                value = scope_value(sentence, file_only=True)
                if value:
                    scope = file_fact(value, sentence, start)
    return {
        "valid_from": dates[0][0].copy() if len(dates) == 1 and not dates[0][1] and not relative else file_fact(),
        "valid_from_candidates": [fact for fact, _ in dates],
        "scope": scope,
    }


EXCEPTION = re.compile(
    r"(?:^|[，,：:])\s*(?:但是|但(?!凡|愿)|除(?:非|[^，,；;。\n]{1,100}?(?:以外|之外|外)(?=[，,、]|一律|均|皆|其余|应|须|不得|$)))|"
    r"除外(?=[，,、]|$)|确需|另有规定|免予|免于|(?:可以|可|准予|予以|依法)豁免|"
    r"(?:可以|可|准予)免除|不受[^，,；;。\n]{1,80}?限制|"
    r"(?:无|毋|勿)(?:需|须)|不(?:必|需要|用再|再要求)|"
    r"(?:可以|可|允许)(?:不(?:经|再|予|履行|提交|执行)|省略|跳过|免[办审核报签检交缴])|不适用"
)
SPECIAL_PROCEDURE = re.compile(r"特殊(?:情况|情形|事项)|紧急|抢险|抢救|救灾|另行|另按|酌情|视情|变通|绿色通道")
NEGATIVE_BRANCH = re.compile(
    r"(?<=[；;])\s*[^。；;\n]{0,60}(?:不|未|缺|无法|不能|不足|争议|异议|逾期)[^。；;\n]{0,60}?的[，,][^。；;\n]{1,160}"
)
EXCEPTION_LABEL = re.compile(r"(?:^|[。；;\n])\s*例外(?:情形)?[：:]\s*([^。；;\n]+)")


def source_exception(quote: str) -> tuple[str, bool]:
    """An explicit exclusion or a different obligation. An uncertain branch needs review."""
    label = EXCEPTION_LABEL.search(quote)
    if label:
        return label.group(1).strip(), False
    for clause in re.split(r"[。；;\n]", quote):
        if EXCEPTION.search(ARTICLE.sub("", clause)):
            return clause.strip(), False
    negative = NEGATIVE_BRANCH.search(quote)
    if negative:
        return negative.group().strip(), False
    return "", bool(SPECIAL_PROCEDURE.search(quote))


CONDITIONAL = (
    r"(?:如果|假如|倘若|倘使|若(?!干)|一旦|凡是?|如(?:遇|有|需|发现|发生|无法|未|不)|"
    r"当(?!事人|前|月|日|地|班|场|年|代|务|即))"
)
INTRODUCED = re.compile(r"(?:^|(?<=[，,]))\s*(" + CONDITIONAL + r"[^，,。；;\n]{1,100})(?=[，,]\s*\S)")
TEMPORAL = re.compile(
    r"(?:在|(?<!应)当(?!事人|前|日|地|场)|若|如果)[^，,。；;\n]{1,80}?"
    r"(?:后(?!备|台|续|勤)|(?<![及按准随同定])时(?!间|限|机)|(?<!提)前(?!提|述|沿|期|景))|"
    r"(?:经[^，,。；;\n]{1,55}?(?:同意|审批|批准|签字|核验)|[^，,。；;\n]{0,30}?(?:期满|到期)|完成|通过|收到)"
    r"[^，,。；;\n]{0,60}?(?:后(?!备|台|续)|时(?!间|限)|之日起)"
)
PREDICATE = re.compile(
    r"超过|超出|达到|不足|低于|高于|具备|确需|发现|存在|发生|出现|遇到|"
    r"未经|未按|未取得|符合|满足|经.+(?:批准|同意|核准)|不齐|缺失|逾期|变更|中止|不一致|擅自"
)
OBLIGATION = re.compile(
    r"^(?:\s*(?:由|按照|按|应|须|需|必须|方可|才可|可以|可|予以|不予|不得|禁止)|[^，,的]{1,22}(?:应|须|必须|不得))"
)
UNRESOLVED_CONDITION = re.compile(r"(?:^|[，,])\s*" + CONDITIONAL)


def condition_clauses(quote: str) -> Iterator[tuple[str, bool]]:
    for clause in re.split(r"[。；;\n]", quote):
        clause = ARTICLE.sub("", clause).strip()
        if not clause:
            continue
        label = re.match(r"适用条件[：:]\s*(.+)", clause)
        introduced = INTRODUCED.search(clause)
        if label or introduced:
            yield (label or introduced).group(1).strip(), False
            continue
        temporal = TEMPORAL.search(clause)
        if temporal:
            yield temporal.group().strip(), False
            continue
        trigger = re.search(
            r"(?:^|(?<=[，,]))\s*([^，,。；;\n]{1,100}?[的者])(?:[，,]|(?=不予|不得|应|须|必须|予以))(.+)", clause
        )
        if trigger and PREDICATE.search(trigger.group(1)) and OBLIGATION.match(trigger.group(2)):
            yield trigger.group(1), False
        elif UNRESOLVED_CONDITION.search(clause):
            yield "", True


def own_quotes(candidate: dict, text: str) -> tuple[list[str], bool]:
    quotes = []
    ambiguous = False
    content = str(candidate.get("content") or "").strip()
    for item in candidate.get("source_quotes") or []:
        quote = item.get("quote") if isinstance(item, dict) else None
        if not isinstance(quote, str) or not quote.strip() or quote not in text:
            continue
        articles = [
            part for part in re.split(r"(?=第[一二三四五六七八九十百千零\u3007\d]+条)", quote) if ARTICLE.match(part)
        ]
        if len(articles) < 2:
            quotes.append(quote)
        else:
            local = [part.strip() for part in articles if part.strip() in content]
            if not local and content and content in quote:
                local = [content]
            if not local:
                ambiguous = True
                values = [candidate.get(key) for key in ("scope", "exceptions")]
                if isinstance(candidate.get("conditions"), list):
                    values.extend(candidate["conditions"])
                local = [v for v in values if isinstance(v, str) and v.strip() and v in quote and v in content]
            quotes.extend(local)
    return quotes, ambiguous


def literal_source(value: object, quotes: list[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    return next((quote for quote in quotes if value in quote), "")


def model_fields(candidate: dict) -> dict:
    if candidate.get("extraction_mode") != "online":
        return {}
    recorded = candidate.get("field_provenance")
    if not isinstance(recorded, dict):
        return candidate
    # A saved candidate contains defaults and human edits alongside the input.
    # Grounding it again must never turn one of those back into a model claim.
    model = {
        key: candidate.get(key)
        for key, record in recorded.items()
        if isinstance(record, dict) and record.get("origin") == "source"
    }
    condition_record = recorded.get("conditions")
    if isinstance(condition_record, dict) and condition_record.get("parts"):
        model["conditions"] = [
            p["value"]
            for p in condition_record["parts"]
            if isinstance(p, dict) and p.get("origin") == "source" and p.get("value")
        ]
    return model


FALSE_EXCEPTION = re.compile(r"(?:删|移|排|清|消|扣|解|剔|去|拆|切)除[^。；;]{0,100}外|(?:非|不)但|豁免申请")


def ground_candidate(candidate: dict, source_text: str, facts: dict | None = None) -> dict:
    """Only literal values explicitly supplied by the model can claim model extraction."""
    result = dict(candidate)
    facts = facts if facts is not None else document_facts(source_text)
    quotes, ambiguous = own_quotes(candidate, source_text)
    model = model_fields(candidate)
    origins = {}
    for field in ("definition", "unit", "grain", "boundary", "scope", "exceptions"):
        value = model.get(field)
        support = literal_source(value, quotes)
        if field == "exceptions" and support and FALSE_EXCEPTION.search(value) and not source_exception(value)[0]:
            support = ""
        origin = "source" if support else "system"
        if not support:
            value = ""
            if field == "definition" and quotes:
                value = re.split(r"[。；;\n]", quotes[0], maxsplit=1)[0].strip()[:250]
            elif field == "scope":
                value = next(
                    (
                        v
                        for quote in quotes
                        for sentence, _, external in sentences(quote)
                        if not external and (v := scope_value(sentence))
                    ),
                    "",
                )
        if field == "exceptions" and (not value or value in {"无例外", "没有例外"}):
            for quote in quotes:
                literal, unresolved = source_exception(quote)
                if literal:
                    if literal != value:
                        value, origin = literal, "system"
                    support = quote
                    break
                if unresolved:
                    ambiguous = True
                    value = ""
        result[field] = value
        if value:
            origins[field] = {"origin": origin, "quote": support or literal_source(value, quotes)}
    supplied = model.get("conditions")
    parts = {}
    if isinstance(supplied, list):
        for value in supplied:
            if supporting := literal_source(value, quotes):
                parts[value] = {"origin": "source", "quote": supporting}
    for quote in quotes:
        for condition, unresolved in condition_clauses(quote):
            ambiguous |= unresolved
            if condition and not any(v in condition or condition in v for v in parts):
                parts[condition] = {"origin": "system", "quote": quote}
    result["conditions"] = list(parts)
    if parts:
        selected = next((part for part in parts.values() if part["origin"] == "system"), next(iter(parts.values())))
        origins["conditions"] = dict(selected)
        if len(parts) > 1:
            origins["conditions"]["parts"] = [{"value": value, **part} for value, part in parts.items()]
    result["bulk_confirmation_blocked"] = ambiguous
    if not result["scope"] and facts["scope"]["value"]:
        result["scope"] = facts["scope"]["value"]
        origins["scope"] = dict(facts["scope"])
    result["valid_from"] = facts["valid_from"]["value"]
    result["valid_to"] = ""
    if result["valid_from"]:
        origins["valid_from"] = dict(facts["valid_from"])
    result["field_provenance"] = origins
    return result
