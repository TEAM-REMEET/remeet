import importlib
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

runner = importlib.import_module('실행하기')


class WorkflowTest(unittest.TestCase):
    def test_full_run_calls_each_stage_once_and_reuses_output(self):
        exp = MagicMock()
        exp.output = Path('run')
        with patch.object(runner, 'Experiment', return_value=exp), patch.object(runner, 'assemble', return_value=exp.output) as assemble:
            result = runner.execute('full', 'TS3003a')
        exp.run_chunks.assert_called_once_with()
        exp.merge_and_review.assert_called_once_with()
        exp.review_actions.assert_called_once_with()
        assemble.assert_called_once_with('TS3003a', exp.output, exp.output, exp)
        self.assertEqual(result, exp.output)

    def test_check_validates_all_before_any_execution(self):
        with patch.object(runner, 'validate_inputs') as validate, patch.object(runner, 'execute') as execute:
            result = runner.main(['final', '--meetings', 'all', '--check'])
        self.assertEqual(result, 0)
        self.assertEqual(validate.call_count, 4)
        execute.assert_not_called()

    def test_bad_input_prevents_any_execution(self):
        with patch.object(runner, 'validate_inputs', side_effect=[None, ValueError('bad')]), patch.object(runner, 'execute') as execute:
            with self.assertRaises(ValueError):
                runner.main(['actions', '--meetings', 'TS3003a', 'TS3003b'])
        execute.assert_not_called()

    def test_default_action_handles_embedded_and_separate_runs(self):
        with patch.object(Path, 'exists', return_value=False):
            self.assertEqual(runner.default_action(Path('saved')), Path('saved/03_액션'))
        with patch.object(Path, 'exists', return_value=True):
            self.assertEqual(runner.default_action(Path('saved')), Path('saved'))


if __name__ == '__main__':
    unittest.main()
