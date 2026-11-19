"""D18/D21. The sample policies use ordinary clauses and hard negative neighbours."""

import unittest

from backend.domains import build


def ground(text: str, **fields: object) -> dict:
    return build.ground_candidate({"kind": "rule", "content": text, "source_quotes": [{"quote": text}], **fields}, text)


class SourceFacts(unittest.TestCase):
    def test_effective_dates_require_a_single_current_operative_sentence(self) -> None:
        cases = [
            ("本办法经董事会审议通过，自2027年3月1日起施行。", "2027-03-01"),
            ("经第二次修订，本制度自2027年3月1日起施行。", "2027-03-01"),
            ("修订后的细则自2027年4月1日起施行。", "2027-04-01"),
            ("本规定自2027年10月1日起施行，原办法已于2020年完成第一次修订。", "2027-10-01"),
            ("本规程自2028年2月29日起施行。", "2028-02-29"),
            ("第十二条 本办法自 2027 年 4 月 1 日起正式执行。", "2027-04-01"),
            ("本细则于2027-12-31生效。", "2027-12-31"),
            ("本指引自二〇二七年十一月二十日起实施。", "2027-11-20"),
            ("本次修订版自2027年7月1日起施行。", "2027-07-01"),
            ("第二十条 自2027年7月1日起施行。", "2027-07-01"),
            ("经修订，本办法将于2027年7月1日重新施行。", "2027-07-01"),
            ("附则\n第二十一条 本规定从2027年3月1日起执行。原规定同时废止。", "2027-03-01"),
            ("本条例自2027年9月1日起施行，实施细则另行制定。", "2027-09-01"),
            ("本准则于2027年10月1日开始实施。", "2027-10-01"),
            ("本实施细则自2027年6月1日起生效；原口径同时停止执行。", "2027-06-01"),
            ("修订后的本办法自2027年8月1日起施行。", "2027-08-01"),
            ("本规定于2026年2月29日生效。", ""),
            ("二〇二五年十二月修订，2026年1月1日印发。", ""),
            ("上一版制度自2020年3月1日起实施，现予废止。", ""),
            ("公司自2025年1月1日起实施月度对账。", ""),
            ("本办法规定自2027年1月1日起执行新的补贴标准。", ""),
            ("本办法于2027年3月1日经董事会审议通过。", ""),
            ("本规定自公布之日起施行。", ""),
            ("办事机构最迟于2027年1月1日完成备案。", ""),
            ("原《采购办法》（自2020年3月1日起实施）同时废止。", ""),
            ("本办法将于2027年1月1日组织实施情况评估。", ""),
            ("本细则自2027年2月1日起实施年度盘点。", ""),
            ("本通知自印发之日起施行，2027年2月1日前清理旧表。", ""),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                fact = build.document_facts(text)["valid_from"]
                self.assertEqual(fact["value"], expected)
                if expected:
                    self.assertEqual(fact["origin"], "system")
                    self.assertEqual(text[fact["start"] : fact["end"]], fact["quote"])

    def test_history_annexes_and_quotations_offer_distinct_exact_sentences(self) -> None:
        cases = [
            (
                "原第十条：本办法自2015年7月1日起施行。\n修订后第十条：自2026年1月1日起施行。",
                ["2015-07-01", "2026-01-01"],
            ),
            ("修订前文本：\n第十条 自2015年7月1日起施行。", ["2015-07-01"]),
            ("本办法自2018年5月1日起施行。本办法修订后，自2026年1月1日起施行。", ["2018-05-01", "2026-01-01"]),
            ("本办法自2018年5月1日起施行。本办法经修订，自2026年1月1日起施行。", ["2018-05-01", "2026-01-01"]),
            (
                "本制度于2018年5月1日实施，至今已修订三次。现予公布，本制度自2026年1月1日起施行。",
                ["2018-05-01", "2026-01-01"],
            ),
            (
                "本办法自2015年7月1日起施行，2020年第一次修订。本次修订版自2026年9月1日起实施。",
                ["2015-07-01", "2026-09-01"],
            ),
            ("本办法最初于2018年5月1日实施。经重新审议，本次修订自2026年1月1日起施行。", ["2018-05-01", "2026-01-01"]),
            ("附件：原有工作规程摘录\n第十条 自2018年2月1日起施行。", ["2018-02-01"]),
            ("附件《原采购办法》\n第十五条 本办法自2015年7月1日起施行。", ["2015-07-01"]),
            ("本办法自2018年1月1日起施行。\n本办法修订后自2026年1月1日起施行。", ["2018-01-01", "2026-01-01"]),
            (
                "第一条 本制度于2018年5月1日实施。2020年完成第一次修订。\n第二十条 自2026年1月1日起施行。",
                ["2018-05-01", "2026-01-01"],
            ),
            ("本办法自2018年1月1日起施行。经修订，本办法将于2026年1月1日重新施行。", ["2018-01-01", "2026-01-01"]),
            ("本制度于2018年5月1日实施，至今已修订三次，自2026年1月1日起施行。", ["2018-05-01", "2026-01-01"]),
            (
                "本规定自2026年3月1日起实施。\n附件一：旧办法摘录\n本办法自2017年9月1日起施行。",
                ["2026-03-01", "2017-09-01"],
            ),
            ("《原管理办法》摘录如下。\n第一条 办理人员应核验单据。\n本办法自2015年5月6日起施行。", ["2015-05-06"]),
            ("原规程规定：\n本规程自2016年6月1日起执行。", ["2016-06-01"]),
            ("附：废止规程\n本规程自2014年3月1日起执行。", ["2014-03-01"]),
            ("附　件一：此前的决定\n本办法自2013年12月1日起施行。", ["2013-12-01"]),
            ("本办法于2015年7月1日首次施行，随后历经三次修订。", ["2015-07-01"]),
            ("本规定自2010年2月1日实施以来，已成为检查的重要依据。", ["2010-02-01"]),
            ("附件 2 原规定节录\n第一条 本办法自2017年9月1日起施行。", ["2017-09-01"]),
            ("此前的文件写道：“本办法自2017年9月1日起施行。”请据此核对当年凭证。", ["2017-09-01"]),
            ("现引述原制度如下：\n\n本制度自2017年1月1日起执行。", ["2017-01-01"]),
            ("原文件说明（本办法自2019年3月1日起施行），现已废止。", ["2019-03-01"]),
            ("引文记载：\n「第十五条。\n本条例自2020年2月1日起施行。」", ["2020-02-01"]),
            ("本办法自2026年3月1日起实施。\n本办法自2026年3月1日起实施。", ["2026-03-01"] * 2),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                facts = build.document_facts(text)
                self.assertEqual(facts["valid_from"]["value"], "")
                options = facts["valid_from_candidates"]
                self.assertEqual([option["value"] for option in options], expected)
                self.assertEqual(len({(c["start"], c["end"]) for c in options}), len(expected))
                if "至今已修订三次，自" in text:
                    self.assertIn("至今已修订三次", options[0]["quote"])
                for c in options:
                    self.assertEqual(text[c["start"] : c["end"]], c["quote"])
                    self.assertEqual(c["origin"], "system")

    def test_scope_distinguishes_file_article_and_mentions_of_other_rules(self) -> None:
        cases = [
            ("第二条 本办法适用于集团本部及所属单位。", "集团本部及所属单位", True),
            ("本细则仅适用于首次申报项目。", "首次申报项目", True),
            ("集团在境内设立的分支机构，适用本规定。", "集团在境内设立的分支机构", True),
            ("第三条 本条例的适用范围为经营性停车设施。", "经营性停车设施", True),
            ("本实施细则适用于以公开招标方式采购的项目。", "以公开招标方式采购的项目", True),
            ("本制度适用于研发、试制和中试阶段的技术资料。", "研发、试制和中试阶段的技术资料", True),
            ("本规定适用于外聘专业人员，临时人员另行管理。", "外聘专业人员", True),
            ("本办法适用于公司各部门，但不适用于海外分支机构。", "公司各部门，但不适用于海外分支机构", True),
            ("本细则适用于下属企业，其中参股公司除外。", "下属企业，其中参股公司除外", True),
            ("本办法适用于集团在建工程（含改扩建）。", "集团在建工程（含改扩建）", True),
            ("本条仅适用于已生效的框架合同。", "已生效的框架合同", False),
            ("本章适用于总部集中采购事项。", "总部集中采购事项", False),
            ("本款适用于通过网上渠道办理的申请。", "通过网上渠道办理的申请", False),
            ("适用范围：合同项下的质保金。", "合同项下的质保金", False),
            ("本通知适用于直接承担档案保管职责的部门。", "直接承担档案保管职责的部门", True),
            ("原办法适用于集团总部，现行规则另行规定。", "", False),
            ("审批人员应确认是否适用于本案。", "", False),
            ("有关人员应了解本制度的适用范围。", "", False),
            ("本办法不适用于临时垫付。", "", False),
            ("本办法适用于公司各部门；海外分支机构除外。", "", False),
            ("本办法适用于公司各部门。海外分支机构不适用本办法。", "", False),
            ("本办法适用于公司各部门；但不包括海外分支机构。", "", False),
            ("本办法适用于公司各部门。海外分支机构不适用。", "", False),
            ("本办法适用于公司各部门。新设派驻办公室不纳入上述范围。", "", False),
            ("本办法适用于公司各部门。第二条 申请人应当提交原件，已联网核查的除外。", "公司各部门", True),
            ("本办法适用的具体问题由财务部说明。", "", False),
            ("符合时应参照本规定办理。", "", False),
            ("部门应当准确说明项目范围。", "", False),
            ("适用范围尚待董事会确定。", "", False),
            ("本细则明确各部门工作职责。", "", False),
            ("为便于查阅，旧制度载明“本办法适用于原试点单位”。", "", False),
            ("范围外的项目按原流程申报。", "", False),
        ]
        for text, expected, shared in cases:
            with self.subTest(text=text):
                self.assertEqual(build.document_facts(text)["scope"]["value"], expected if shared else "")
                item = ground(text)
                self.assertEqual(item["scope"], expected)
                if expected:
                    self.assertEqual(item["field_provenance"]["scope"]["origin"], "system")
                    self.assertIn(expected, item["field_provenance"]["scope"]["quote"])

    def test_conditions_keep_real_triggers_and_reject_attributive_lists(self) -> None:
        cases = [
            ("如遇申请人失联，承办人应作书面记录。", "如遇申请人失联"),
            ("一旦发现账实不符，仓管员须立即复核。", "一旦发现账实不符"),
            ("若校验通过，系统可以恢复受理。", "若校验通过"),
            ("当资料齐全时，窗口应当场受理。", "当资料齐全时"),
            ("申请金额超过限额的，由部门负责人复核。", "申请金额超过限额的"),
            ("发现虚假票据的不予报销。", "发现虚假票据的"),
            ("合同期满后，档案员应将纸本移交档案室。", "合同期满后"),
            ("经主管部门书面同意后，经办人方可展期。", "经主管部门书面同意后"),
            ("在房屋交付前，出租人应结清公共费用。", "在房屋交付前"),
            ("凡申请异地转入的，应提供原始流水。", "凡申请异地转入的"),
            ("如未按时完成整改，暂停受理新增事项。", "如未按时完成整改"),
            ("具备法定资格的，方可承担独立审核。", "具备法定资格的"),
            ("在两名经办人共同签字后方可封存。", "在两名经办人共同签字后"),
            ("会计应在上午十时之前完成清点。", "在上午十时之前"),
            ("库存应于17时30分前完成核对。", "于17时30分前"),
            ("档案管理员应在柜台前放置一份签收簿。", ""),
            ("请在档案柜后留足检修空间。", ""),
            ("应当提供真实的，合法的票据。", ""),
            ("应当提交完整的，有签章的原件。", ""),
            ("当事人应在登记册签字。", ""),
            ("应当及时归还借用设备。", ""),
            ("在制度范围内及时审批预算。", ""),
            ("在此前提下，继续履行审查职责。", ""),
            ("当前各处室应按规定整理票据。", ""),
            ("档案室要保护在用设备和后备电源。", ""),
            ("如实登记每一笔支出。", ""),
            ("本章包括若干管理事项，由办公室解释。", ""),
            ("严禁以不实理由申请延期。", ""),
            ("金额与期限应在同一张表内填报。", ""),
            ("负责人应将最新的，经复核的材料归档。", ""),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                item = ground(text)
                self.assertEqual(item["conditions"], [expected] if expected else [])
                if expected:
                    self.assertEqual(item["field_provenance"]["conditions"]["origin"], "system")
                    self.assertIn(expected, item["field_provenance"]["conditions"]["quote"])

    def test_exceptions_use_a_logical_branch_and_reject_lexical_coincidences(self) -> None:
        cases = [
            ("登记人员应逐项复核，但紧急疏散时可先行办理。", "但紧急疏散时可先行办理"),
            ("除依法应当保密的事项外，会议记录应当公布。", "除依法应当保密的事项外"),
            ("临时调用的车辆不受上述期限限制。", "不受上述期限限制"),
            ("申请人原则上提交原件；已联网核验的可免予提交。", "可免予提交"),
            ("境内支出统一结算，抢险救灾所需临时付款除外。", "临时付款除外"),
            ("省级机关另有规定的，从其规定。", "另有规定"),
            ("一般不得延长保管期限；确需延长的，应作书面记录。", "确需延长的"),
            ("同一凭证经系统核验的，不必再次提交纸件。", "不必再次提交"),
            ("一般先经初审，危急时允许跳过初审。", "允许跳过初审"),
            ("机构合并的项目可以免除重复验收。", "可以免除重复验收"),
            ("所有申请应书面提交；出现系统故障时可不经网上申报。", "可不经网上申报"),
            ("各地均须复核，已获批准的试点项目无需复核。", "无需复核"),
            ("经办人应删除外部共享文件。", ""),
            ("移除过期账号并对外部系统同步。", ""),
            ("申请人非但要核对金额，还要核对收款人。", ""),
            ("清除合同外包装，注意保存封条。", ""),
            ("财务部负责扣除外币折算差额。", ""),
            ("对账环节应排除外围系统干扰。", ""),
            ("同步移除境外账号。", ""),
            ("解除外包关系前须归还门禁卡。", ""),
            ("行政人员应消除外界噪声影响。", ""),
            ("不仅要防范重复付款，而且要定期盘点。", ""),
            ("办理时应调查是否已经删除外部共有目录。", ""),
            ("经办人应填写豁免申请台账。", ""),
            ("任何人员不得以紧急为由免予审批。", ""),
            ("任何部门不得以任何理由免予审核。", ""),
            ("但任何人不得以职务需要为由豁免核验。", ""),
            ("例外：不得因项目紧急免除复核。", ""),
            ("不得以业务紧急为由免予审批，但获得书面授权的可免予交纸质副本。", "可免予交纸质副本"),
            (
                "不得以紧急为由免予审批，但经负责人书面批准的，可免予提交纸质副本。",
                "但经负责人书面批准的，可免予提交纸质副本",
            ),
        ]
        for text, needle in cases:
            with self.subTest(text=text):
                item = ground(text)
                if needle:
                    self.assertIn(needle, item["exceptions"])
                    self.assertIn(item["exceptions"], text)
                    self.assertEqual(item["field_provenance"]["exceptions"]["origin"], "system")
                else:
                    self.assertEqual(item["exceptions"], "")
                    self.assertNotIn("exceptions", item["field_provenance"])

    def test_model_fields_are_literal_and_local_fallback_is_never_model_provenance(self) -> None:
        text = "单笔付款超过限额的，应经审批；确需延后提交的，由经办部门书面说明。"
        model = ground(
            text,
            extraction_mode="online",
            definition="单笔付款",
            conditions=["单笔付款超过限额的"],
            exceptions="确需延后提交的，由经办部门书面说明",
        )
        self.assertEqual(model["field_provenance"]["definition"]["origin"], "source")
        self.assertEqual(model["field_provenance"]["exceptions"]["origin"], "source")
        # A second, unsupplied condition is recognized from the same article.
        self.assertEqual(model["field_provenance"]["conditions"]["origin"], "system")
        for mode in ("local", None):
            item = ground(text, extraction_mode=mode, definition="单笔付款", conditions=[text])
            self.assertNotIn("source", [v["origin"] for v in item["field_provenance"].values()])
        rechecked = build.ground_candidate(model, text)
        for field in ("definition", "exceptions", "conditions"):
            self.assertEqual(rechecked["field_provenance"][field], model["field_provenance"][field])
        for literal in ("应删除外部共享文件", "移除过期账号并对外部系统同步", "非但要核对金额"):
            item = ground(literal + "。", extraction_mode="online", exceptions=literal)
            self.assertEqual(item["exceptions"], "")
        other = ground("第五条 付款凭证应当入档。", extraction_mode="online", exceptions="紧急支付除外")
        self.assertEqual(other["exceptions"], "")
        self.assertNotIn("exceptions", other["field_provenance"])

    def test_prohibited_waivers_and_scope_limits_survive_grounding(self) -> None:
        for wording in (
            "任何人不得免予提交原始凭证",
            "经办人禁止自行免除核验",
            "不得擅自免于复核",
            "任何人员不得以紧急为由免予审批",
            "任何部门不得以任何理由免予审核",
        ):
            for value in (None, wording, wording[3:]):
                with self.subTest(wording=wording, value=value):
                    item = ground(wording + "。", extraction_mode="online", exceptions=value)
                    self.assertEqual(item["exceptions"], "")
                    self.assertNotIn("exceptions", item["field_provenance"])
        mixed = "不得以紧急为由免予审批，但获得书面授权的可免予交纸质副本。"
        self.assertEqual(
            ground(mixed, extraction_mode="online", exceptions="免予审批")["exceptions"],
            "但获得书面授权的可免予交纸质副本",
        )
        allowed = "但经负责人书面批准的，可免予提交纸质副本"
        separated = "不得以紧急为由免予审批，" + allowed + "。"
        permitted = ground(separated, extraction_mode="online", exceptions=allowed)
        self.assertEqual(permitted["exceptions"], allowed)
        self.assertEqual(permitted["field_provenance"]["exceptions"]["origin"], "source")
        text = "本办法适用于公司各部门，但不适用于海外分支机构。"
        own = ground(text, extraction_mode="online", scope="公司各部门")
        self.assertEqual(own["scope"], "公司各部门，但不适用于海外分支机构")
        self.assertEqual(own["field_provenance"]["scope"]["origin"], "system")
        rule = "采购款应由财务部门复核。"
        candidate = {"kind": "rule", "source_quotes": [{"quote": rule}], "content": rule}
        inherited = build.ground_candidate(candidate, text + "\n" + rule)
        self.assertEqual(inherited["scope"], own["scope"])
        self.assertEqual(inherited["field_provenance"]["scope"]["quote"], text)
        conflicting = "本办法适用于各分支机构。本办法不适用于间接控股企业。"
        self.assertEqual(build.ground_candidate(candidate, conflicting + rule)["scope"], "")
        for limited in (
            "本办法适用于公司各部门；海外分支机构除外。",
            "本办法适用于公司各部门。海外分支机构不适用本办法。",
        ):
            self.assertEqual(build.ground_candidate(candidate, limited + rule)["scope"], "")
            self.assertEqual(ground(limited, extraction_mode="online", scope="公司各部门")["scope"], "")
