import importlib.util
import io
import json
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('v2_run', Path(__file__).with_name('실행하기.py'))
v2 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v2)


class OverlapTests(unittest.TestCase):
    def test_restore_segments_recombines_old_chunks_within_each_segment(self):
        utterances = [{'번호': i, '텍스트': str(i) * 300} for i in range(5)]
        utterances[4]['텍스트'] = 'x' * 900
        chunks = [{'청크ID': 1, 'segment_id': 0, '발화': utterances[:1]},
                  {'청크ID': 2, 'segment_id': 0, '발화': utterances[1:3]},
                  {'청크ID': 3, 'segment_id': 1, '발화': utterances[3:]}]
        result = v2.restore_segments(chunks)
        self.assertEqual([[u['번호'] for u in c['발화']] for c in result], [[0, 1, 2], [3, 4]])
        self.assertEqual([c['segment_id'] for c in result], [0, 1])
        self.assertEqual(len(result[-1]['발화'][-1]['텍스트']), 900)

    def test_stored_outer_and_new_inner_boundaries(self):
        utterances = [{'번호': i, '텍스트': str(i) * 120} for i in range(5)]
        chunks = [
            {'청크ID': 1, 'segment_id': 0, '발화': utterances[:1]},
            {'청크ID': 2, 'segment_id': 1, '발화': utterances[1:3]},
            {'청크ID': 3, 'segment_id': 1, '발화': utterances[3:]},
        ]
        raw = lambda i: {'utterance_index': i, 'text': utterances[i]['텍스트']}
        data = {'segments': [
            {'segment_id': 0, 'utterances': [raw(0)], 'overlap_before': [], 'overlap_after': [raw(1)]},
            {'segment_id': 1, 'utterances': [raw(i) for i in range(1, 5)], 'overlap_before': [raw(0)], 'overlap_after': []},
        ]}
        result = v2.add_overlap(data, chunks)
        self.assertEqual(result[0]['뒤_오버랩_발화번호'], [1])
        self.assertEqual(result[1]['앞_오버랩_발화번호'], [0])
        self.assertEqual(result[1]['뒤_오버랩_발화번호'], [3, 4])
        self.assertEqual(result[2]['앞_오버랩_발화번호'], [1, 2])
        self.assertEqual(result[2]['뒤_오버랩_발화번호'], [])
        self.assertEqual(result[1]['글자수']['뒤'], 240)
        self.assertEqual(result[1]['오버랩_출처'], {'앞': '기존 파일', '뒤': '원문'})
        self.assertEqual([u['번호'] for c in result for u in c['발화'] if u['번호'] in c['핵심_발화번호']], list(range(5)))

    def test_rejects_stale_stored_overlap(self):
        chunks = [{'청크ID': 1, 'segment_id': 0, '발화': [{'번호': 0, '텍스트': '원문'}]}]
        data = {'segments': [{'segment_id': 0, 'utterances': [{'utterance_index': 0}],
                              'overlap_before': [{'utterance_index': 99, 'text': '다른 원문'}]}]}
        with self.assertRaises(ValueError):
            v2.add_overlap(data, chunks)


class AgendaTests(unittest.TestCase):
    def setUp(self):
        self.summaries = [{'meeting_id': 'M', 'chunk_id': 1, 'agendas': [
            {'new_key': 'C001U001', 'agenda_id': None, 'title': '첫 주제'},
            {'new_key': 'C001U002', 'agenda_id': None, 'title': '둘째 주제'}]}]

    def test_individual_units_can_map_to_separate_agendas(self):
        mapping = {'meeting_id': 'M', 'agendas': [{'title': '첫 주제', 'unit_ids': ['C001U001']},
                                                    {'title': '둘째 주제', 'unit_ids': ['C001U002']}], 'unassigned': []}
        agendas, results = v2.assign_agendas('M', self.summaries, mapping)
        self.assertEqual([a['안건ID'] for a in agendas], ['A001', 'A002'])
        self.assertEqual([a['agenda_id'] for a in results[0]['agendas']], ['A001', 'A002'])
        self.assertIsNone(self.summaries[0]['agendas'][0]['agenda_id'])

    def test_missing_or_duplicate_mapping_fails(self):
        mapping = {'meeting_id': 'M', 'agendas': [{'title': '주제', 'unit_ids': ['C001U001']}], 'unassigned': []}
        with self.assertRaises(ValueError):
            v2.assign_agendas('M', self.summaries, mapping)
        mapping['unassigned'] = [{'unit_id': 'C001U001', 'reason': '중복'}]
        with self.assertRaises(ValueError):
            v2.assign_agendas('M', self.summaries, mapping)

    def test_unassigned_unit_is_kept_in_mapping(self):
        mapping = {'meeting_id': 'M', 'agendas': [{'title': '주제', 'unit_ids': ['C001U001']}],
                   'unassigned': [{'unit_id': 'C001U002', 'reason': '불확실'}]}
        agendas, results = v2.assign_agendas('M', self.summaries, mapping)
        self.assertEqual(len(results[0]['agendas']), 1)
        self.assertEqual(mapping['unassigned'][0]['unit_id'], 'C001U002')

    def test_whole_meeting_mapping_writes_separate_merge_inputs(self):
        saved = {}
        with patch.object(v2, 'save', side_effect=lambda path, data: saved.setdefault(str(path), data)):
            exp = v2.V2Experiment.__new__(v2.V2Experiment)
            exp.data = {'meeting_id': 'M'}
            exp.independent = self.summaries
            exp.output = Path('output')
            mapping = {'meeting_id': 'M', 'agendas': [
                {'title': '첫 주제', 'unit_ids': ['C001U001']},
                {'title': '둘째 주제', 'unit_ids': ['C001U002']}], 'unassigned': []}
            exp.call = lambda stage, system, user, schema: mapping
            exp.make_agendas()
            self.assertEqual(len(exp.results), 2)
            self.assertEqual([len(r['agendas']) for r in exp.results], [1, 1])
            chunk = saved[str(exp.output / '01_청크요약' / '001.json')]
            self.assertEqual(len(chunk['agendas']), 2)
            self.assertEqual(saved[str(exp.output / '02_안건연결.json')], mapping)


class ParallelTests(unittest.TestCase):
    def test_parallel_results_keep_original_order_and_save_by_id(self):
        chunks = [{'청크ID': i} for i in (1, 2, 3)]
        entered = threading.Barrier(3, timeout=2)
        saved = {}
        def worker(chunk):
            entered.wait()
            return {'chunk_id': chunk['청크ID']}
        result = v2.parallel_collect(chunks, worker, lambda chunk_id, data: saved.update({chunk_id: data}))
        self.assertEqual([x['chunk_id'] for x in result], [1, 2, 3])
        self.assertEqual(set(saved), {1, 2, 3})

    def test_pacer_spaces_requests(self):
        pacer = v2.RequestPacer(rpm=10)
        with patch.object(v2.time, 'monotonic', side_effect=[100, 100, 100, 106]), \
             patch.object(v2.time, 'sleep') as sleep:
            pacer.wait()
            pacer.wait()
        sleep.assert_called_once_with(6)

    def test_retry_only_transient_rate_limits(self):
        def error(kind, code, headers=None):
            body = json.dumps({'error': {'type': kind, 'code': code}}).encode()
            return HTTPError('https://example.com', 429, '', headers or {}, io.BytesIO(body))
        with patch.object(v2.random, 'uniform', return_value=0):
            self.assertEqual(v2.retry_delay(error('rate_limit_error', 'slow_down', {'Retry-After': '12'}), 0), 12)
            self.assertIsNone(v2.retry_delay(error('insufficient_quota', 'insufficient_quota'), 0))

    def test_parallel_call_records_usage_without_cross_talk(self):
        exp = v2.V2Experiment.__new__(v2.V2Experiment)
        exp.setup_parallel()
        exp.pacer.wait = lambda: None
        exp.schemas = {'test': {}}
        exp.stage_stats = {}
        exp.record = lambda *args: None
        barrier = threading.Barrier(2, timeout=2)
        class Response:
            def __enter__(self):
                barrier.wait()
                return io.BytesIO(json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': '{}'}}],
                                             'usage': {'prompt_tokens': 10, 'completion_tokens': 3}}).encode())
            def __exit__(self, *args): pass
        with patch.dict(v2.os.environ, {'OPENAI_API_KEY': 'test'}), patch.object(v2, 'urlopen', side_effect=lambda *a, **k: Response()):
            v2.parallel_collect([{'청크ID': 1}, {'청크ID': 2}],
                                lambda c: exp.call(f"chunk_{c['청크ID']}_summary", 'system', 'user', 'test'),
                                lambda *args: None)
        stats = exp.stage_stats['청크 요약']
        self.assertEqual((stats['calls'], stats['input_tokens'], stats['output_tokens']), (2, 20, 6))
        self.assertEqual(stats['status'], 'completed')


if __name__ == '__main__':
    unittest.main()
