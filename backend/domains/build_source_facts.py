"""Literal policy facts. File-wide defaults and item-local evidence have separate scopes."""

import re
from bisect import bisect_right
from collections.abc import Iterator
from datetime import date
from itertools import pairwise

FACTS_VERSION = 3
ARTICLE_NUMBER = r"第[一二三四五六七八九十百千零\u3007\d]+条(?:之[一二三四五六七八九十\d]+)?"
ARTICLE = re.compile(r"^\s*" + ARTICLE_NUMBER + r"\s*")
AMENDMENT_HEADING = re.compile(
    r"^\s*(?:[原旧新](?:条文|条款|规定|文本|文|版)?|现行条文|"
    r"(?:修订|修改|修正|调整|变更)[前后](?:的?(?:条文|条款|规定|文本))?)"
    rf"\s*(?:{ARTICLE_NUMBER}\s*)?[：:]\s*"
)
UNITS = re.compile(r"[^。；;！？!?\r\n]+[。；;！？!?]?")
POLICY = (
    r"(?:实施细则|管理办法|管理制度|办法|制度|规定|细则|规程|规则|"
    r"条例|指引|准则|规范|通知|标准|指南|意见|指令|程序|文件)"
)
CURRENT = rf"(?:本{POLICY}|(?:修订后的|新修订的)本?{POLICY}|本次修订(?:后的{POLICY}|版|文本)?)"
SELF_BEFORE_DATE = re.compile(
    CURRENT + r"(?:[（(](?:试行|暂行|修订)[）)])?\s*(?:(?:(?:经|重新)?修[订改正](?:完成)?后?|修订版)\s*[，,]?\s*)?"
    r"(?:将|原|曾|最初|原定|首次)?\s*$"
)
ELIDED_AMENDMENT = re.compile(
    r"^(?:(?:现已?|本次|此次|本轮|再次|重新|已)\s*)*(?:经(?:过)?|予以|作|进行)?"
    r"(?:(?:第[一二三四五六七八九十\d]+次|再次|重新|全面|部分|集中|正式)\s*)*"
    r"(?:修订|修正|修改|增订|修编)(?:完成|通过|公布|完毕)?(?:后)?\s*[，,]\s*(?:决定|定|将|现定)?$"
)
ATTACHMENT = re.compile(
    r"^\s*(?:[【\u3014（(]\s*)?(?:附\s*[件录表页]|附(?=[：:]))(?=$|[\s：:、.\uff0e（(【《〈「『\u3014一二三四五六七八九十\dA-Z])"
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
    rf"(?:(?P<preposition>自|从|于)\s*)?{DATE}\s*(?:起|开始)?\s*(?:正式|开始|首次|重新|再次)?"
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
    amendment_excerpt = False
    for line in LINE.finditer(text):
        line_text = line.group().strip()
        if re.fullmatch(r"[（(]?正\s*文[）)]?|附件结束|(?:引文|摘录|引用)(?:结束|完)|以下为正文", line_text):
            attachment = cited_section = amendment_excerpt = False
        if heading := AMENDMENT_HEADING.match(line_text):
            amendment_excerpt = heading.end() == len(line_text)
        attachment |= bool(ATTACHMENT.match(line_text))
        for part in UNITS.finditer(line.group()):
            sentence = part.group().strip()
            if not sentence:
                continue
            start = line.start() + part.start() + len(part.group()) - len(part.group().lstrip())
            yield sentence, start, attachment or cited_section or amendment_excerpt
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
        heading = AMENDMENT_HEADING.match(prefix)
        if heading:
            prefix = prefix[heading.end() :]
        cited = (
            external
            or bool(heading)
            or bool(bisect_right(quotations, start + found.start()) % 2)
            or bool(CITATION.search(prefix))
        )
        subject = SELF_BEFORE_DATE.search(prefix)
        elided = found["preposition"] and ELIDED_AMENDMENT.fullmatch(ARTICLE.sub("", prefix))
        # A second date may share the first clause's subject (经修订，自…起施行).
        continued = bool(matches and found["preposition"] and re.search(r"[，,；;…]\s*$", prefix))
        cited_title = cited and re.search(rf"(?:[原旧该]{POLICY}|《[^》]+》)\s*$", prefix)
        bare_excerpt = cited and re.search(r'(?:^|[“‘「『"：:])\s*(?:第[一二三四五六七八九十百零\d]+条\s*)?$', prefix)
        approved = re.match(
            CURRENT + r"(?:已)?经[^。，；;]{1,60}(?:审议|通过|同意|批准|修订)[，,]\s*(?:定|将)?$",
            ARTICLE.sub("", prefix),
        )
        bare_provision = found["preposition"] and not ARTICLE.sub("", prefix).strip()
        if not (subject or elided or continued or cited_title or bare_excerpt or approved or bare_provision):
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
        matches.append((found, value, bool(cited or historical or elided)))
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


SCOPE_LIMIT = re.compile(r"不适用|不(?:包括|含|涵盖)|除外|另有规定的?除外|除[^，,。；;]{1,80}(?:以外|之外|外)")
FILE_EXCLUSION = re.compile(r"(?:不适用(?:于)?" + CURRENT + r"|" + CURRENT + r"(?:不适用|的?适用范围不))")
SCOPE_QUALIFIER = re.compile(r"不(?:属于|纳入|列入)|不在[^，,。；;]{0,24}范围|排除在[^，,。；;]{0,24}范围")
SEPARATE_OBLIGATION = re.compile(r"应(?!用|对|急|收|付)|必须|须经|可以|所称|是指")


def following_scope_limit(sentence: str) -> bool:
    phrase = re.sub(r"^(?:但是|但|其中|惟)[，,\s]*", "", ARTICLE.sub("", sentence))
    if SEPARATE_OBLIGATION.search(phrase):
        return False
    # A new rule can state a condition, an obligation and its own exception.
    # Only an explicit range reference or a standalone exclusion qualifies the preceding scope.
    return bool(SCOPE_QUALIFIER.search(phrase) or (not re.search(r"[，,]", phrase) and SCOPE_LIMIT.search(phrase)))


def scope_value(sentence: str, *, file_only: bool = False) -> str:
    core = ARTICLE.sub("", sentence).rstrip("。；;！？!?").strip()
    subject = CURRENT if file_only else rf"(?:{CURRENT}|本[条章节款项])"
    match = re.fullmatch(
        subject + r"(?:[仅均只]?适用于|的?适用范围(?:为|包括|是|[：:]))\s*([^，,。；;]+)([，,].*)?", core
    )
    if match and match[2] and SCOPE_LIMIT.search(match[2]):
        return core[match.start(1) :].strip()
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
    scope_conflict = False
    previous_scope = False
    for sentence, start, external in sentences(text):
        dates.extend(date_candidates(sentence, start, external, quotations))
        if not external and not bisect_right(quotations, start) % 2:
            relative |= bool(RELATIVE_DATE.search(sentence))
            value = scope_value(sentence, file_only=True)
            if value:
                if scope["value"] and value != scope["value"]:
                    scope_conflict = True
                else:
                    scope = file_fact(value, sentence, start)
            elif FILE_EXCLUSION.search(sentence) or (previous_scope and following_scope_limit(sentence)):
                scope_conflict = True
            previous_scope = bool(value)
        else:
            previous_scope = False
    return {
        "recognition_version": FACTS_VERSION,
        "valid_from": dates[0][0].copy() if len(dates) == 1 and not dates[0][1] and not relative else file_fact(),
        "valid_from_candidates": [fact for fact, _ in dates],
        "scope": file_fact() if scope_conflict else scope,
        "scope_conflict": scope_conflict,
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
PROHIBITED_EXEMPTION = re.compile(
    r"(?:不得|不能|不可|不应|不准|禁止|严禁|不予)(?:(?!但是|但可|可以|允许)[^。，,；;！？\n]){0,80}?"
    r"(?:免予|免于|豁免|免除|免审|跳过)"
)


def permitted_phrases(quote: str) -> Iterator[str]:
    """Remove prohibited comma clauses while keeping each remaining condition and consequence together."""
    for clause in re.split(r"[。；;！？\n]", quote):
        start = 0
        for part in re.finditer(r"[^，,]+", clause):
            if PROHIBITED_EXEMPTION.search(part.group()):
                before = clause[start : part.start()].strip("，, ")
                if before:
                    yield before
                start = part.end()
        if rest := clause[start:].strip("，, "):
            yield rest


def source_exception(quote: str) -> tuple[str, bool]:
    """An explicit exclusion or a different obligation. An uncertain branch needs review."""
    label = EXCEPTION_LABEL.search(quote)
    if label and not PROHIBITED_EXEMPTION.search(label.group(1)):
        return label.group(1).strip(), False
    unresolved = False
    for part in permitted_phrases(quote):
        if EXCEPTION.search(ARTICLE.sub("", part)):
            return part.strip(), False
        unresolved |= bool(SPECIAL_PROCEDURE.search(part))
    negative = NEGATIVE_BRANCH.search(quote)
    if negative and not PROHIBITED_EXEMPTION.search(negative.group()):
        return negative.group().strip(), False
    return "", unresolved


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
CLOCK = re.compile(
    r"(?:在|于)(?:当日|次日|当天)?(?:上午|下午|中午|早上|晚上|凌晨)?[零〇一二三四五六七八九十两\d]{1,3}"
    r"(?:(?:时|点)(?:[零〇一二三四五六七八九十\d]{1,3}分|半|整)?|[:：][0-5]\d)(?:之|以)?(?:前|后)?"
)
TEMPORAL_EVENT = re.compile(
    r"生效|失效|发布|印发|提交|申报|报送|申请|受理|交付|签[收发字章约署订]|核[验对准]|审[批核查议结]|"
    r"验收|复[核查]|收到|归档|登记|结[算束账]|完成|通过|办理|实施|开工|竣工|到期|期满|届满|截止|之前|之后"
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
        temporal = CLOCK.search(clause) or next(
            (m for m in TEMPORAL.finditer(clause) if not m[0].startswith("在") or TEMPORAL_EVENT.search(m[0])), None
        )
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
        if (
            field == "exceptions"
            and support
            and (
                (FALSE_EXCEPTION.search(value) and not source_exception(value)[0])
                or (
                    PROHIBITED_EXEMPTION.search(support)
                    and not any(value in part and source_exception(part)[0] for part in permitted_phrases(support))
                )
            )
        ):
            support = ""
        if field == "scope" and support:
            for clause, _, external in sentences(support):
                complete = scope_value(clause)
                if not external and value in complete and value != complete and SCOPE_LIMIT.search(complete):
                    support = ""
                    break
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
        if (
            field == "scope"
            and facts.get("scope_conflict")
            and any(scope_value(sentence, file_only=True) for quote in quotes for sentence, _, _ in sentences(quote))
        ):
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
