"""Strict evidence contracts reproduced from the live DeepSeek failure."""
from copy import deepcopy
import unittest

from backend.experience import CandidateContractError, candidate_repair, validate_candidate


SOURCE = '合成合同表一张以元记录，另一张以万元记录；先确认币种与单位，再按1万元等于10000元换算。币种不明不汇总。'
AVAILABLE = {'synthetic-source': {'id': 'synthetic-source', 'content': SOURCE, 'locator': '纯合成原话'}}


def candidate():
    reference = {'chunk_id': 'synthetic-source', 'quote': SOURCE, 'role': 'support'}
    return {'title': '合成金额口径经验', 'summary': '先核对币种与单位，再进行有明确依据的人民币单位换算。',
            'source_refs': [reference], 'article_sections': [{'title': '适用边界',
                'content': '币种不明的记录须待补，不纳入人民币汇总。', 'origin': 'source_synthesis',
                'source_refs': [deepcopy(reference)]}]}


class ExperienceEvidenceContractTests(unittest.TestCase):
    def test_source_valid_substring_still_requires_top_level_evidence_membership(self):
        raw = candidate()
        raw['article_sections'][0]['source_refs'][0]['quote'] = '币种不明不汇总。'
        self.assertIn(raw['article_sections'][0]['source_refs'][0]['quote'], SOURCE)
        with self.assertRaises(CandidateContractError) as caught:
            validate_candidate(raw, AVAILABLE)
        self.assertEqual(caught.exception.code, 'SECTION_REFERENCE_NOT_IN_CANDIDATE')
        repair = candidate_repair(raw, caught.exception)
        self.assertEqual(repair['error_code'], 'SECTION_REFERENCE_NOT_IN_CANDIDATE')
        self.assertIn('chunk_id', repair['required_fix'])
        self.assertIn('quote', repair['required_fix'])
        self.assertIn('完整复制', repair['required_fix'])

    def test_exact_top_level_reference_reuse_preserves_valid_evidence(self):
        value = validate_candidate(candidate(), AVAILABLE)
        self.assertEqual(value['article_sections'][0]['source_refs'], value['source_refs'])
        self.assertEqual(value['source_refs'][0]['quote'], SOURCE)

    def test_new_section_quote_must_first_be_included_and_validated_at_top_level(self):
        raw = candidate()
        short = {'chunk_id': 'synthetic-source', 'quote': '币种不明不汇总。', 'role': 'boundary'}
        raw['source_refs'].append(short)
        raw['article_sections'][0]['source_refs'] = [deepcopy(short)]
        value = validate_candidate(raw, AVAILABLE)
        self.assertEqual(value['article_sections'][0]['source_refs'], [value['source_refs'][1]])
        raw['source_refs'][1]['quote'] = '原文未记载的任意金额。'
        with self.assertRaises(ValueError):
            validate_candidate(raw, AVAILABLE)

    def test_generic_repair_does_not_forward_arbitrary_exception_text(self):
        marker = 'SYNTHETIC_EXCEPTION_PAYLOAD_MUST_NOT_ENTER_REPAIR_REASON'
        repair = candidate_repair(candidate(), ValueError(marker))
        self.assertEqual(repair['error_code'], 'CANDIDATE_CONTRACT_INVALID')
        self.assertNotIn(marker, repair['reason'])
        self.assertNotIn(marker, repair['required_fix'])


if __name__ == '__main__':
    unittest.main()
