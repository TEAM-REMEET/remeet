import json
import os
import sys
import tempfile
import unittest
from io import BytesIO
from unittest.mock import patch
from pathlib import Path
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from followup.adapters.parsers.transcript import read_transcript
from followup.analysis.baseline import parse_actions, split_chunks
from followup.application.analyze_meeting import analyze_meeting
from followup.adapters.llm.openai_client import OpenAIClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import run_base_agent


class BaselineTests(unittest.TestCase):
    def test_parent_env_loaded_without_overriding_environment(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "remeet"
            root.mkdir()
            (root.parent / ".env").write_text('OPENAI_API_KEY="file-test-key"\n', encoding="utf-8-sig")
            for existing, expected in [(None, "file-test-key"), ("shell-test-key", "shell-test-key")]:
                with patch.dict(os.environ, {}, clear=True):
                    if existing:
                        os.environ["OPENAI_API_KEY"] = existing
                    with patch.object(run_base_agent, "ROOT", root), patch.object(
                        sys, "argv", ["run_base_agent.py"]
                    ), patch.object(run_base_agent, "analyze_meeting", return_value={"status": "성공"}), patch("builtins.print"):
                        self.assertEqual(run_base_agent.main(), 0)
                    self.assertEqual(os.environ["OPENAI_API_KEY"], expected)

    def test_model_adapter_without_network(self):
        response = {"model": "fake-version", "usage": {"total_tokens": 12},
                    "choices": [{"finish_reason": "stop", "message": {"content": "summary"}}]}
        client = OpenAIClient("fake")
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}), patch(
            "followup.adapters.llm.openai_client.urlopen",
            return_value=BytesIO(json.dumps(response).encode()),
        ) as request:
            self.assertEqual(client("system", "input"), "summary")
            payload = json.loads(request.call_args.args[0].data)
            self.assertEqual(payload["model"], "fake")
            self.assertEqual(client.calls[0]["usage"]["total_tokens"], 12)
        response["choices"][0]["finish_reason"] = "length"
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}), patch(
            "followup.adapters.llm.openai_client.urlopen",
            return_value=BytesIO(json.dumps(response).encode()),
        ), self.assertRaises(ValueError):
            client("system", "input")

    def test_multiple_chunks_merge_in_order(self):
        result, output, _ = self.run_fake(["앞 요약", "[]", "뒤 요약", "[]", "최종 요약"], "가" * 1001)
        self.assertEqual(result["status"], "성공")
        self.assertEqual((output / "chunk_0002_summary.md").read_text(encoding="utf-8"), "뒤 요약")

    def test_chunks_preserve_all_characters(self):
        text = "가 나\n" * 501
        chunks = split_chunks(text)
        self.assertEqual("".join(c["text"] for c in chunks), text)
        self.assertEqual([len(c["text"]) for c in chunks], [1000, 1000, 4])
        self.assertEqual(chunks[-1]["start"], 2000)

    def test_action_contract(self):
        self.assertEqual(parse_actions("[]"), [])
        for bad in ['{}', '[{}]', '[{"유형":"기타"}]', 'not json']:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_actions(bad)

    def run_fake(self, responses, text="발표자료 작성해주세요."):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        source = root / "meeting.txt"
        source.write_text(text, encoding="utf-8")
        iterator = iter(responses)
        def call(system, user):
            item = next(iterator)
            if isinstance(item, Exception):
                raise item
            return item
        output = root / "run"
        result = analyze_meeting(source, output, "fake", call)
        return result, output, source

    def test_success_and_no_overwrite(self):
        action = {"유형": "업무 수행", "내용": "발표자료 작성", "담당자": "미정",
                  "기한": "미정", "원문근거": "발표자료 작성해주세요."}
        result, output, source = self.run_fake(["청크 요약", json.dumps([action]), "최종 요약"])
        self.assertEqual(result["status"], "성공")
        line = (output / "meeting_액션.jsonl").read_text(encoding="utf-8")
        self.assertEqual(json.loads(line), action)
        self.assertTrue((output / "prompts.json").exists())
        with self.assertRaises(FileExistsError):
            analyze_meeting(source, output, "fake", lambda *a: "")

    def test_no_actions_is_success(self):
        result, output, _ = self.run_fake(["요약", "[]", "최종 요약"])
        self.assertEqual(result["status"], "성공")
        self.assertEqual((output / "meeting_액션.jsonl").read_bytes(), b"")

    def test_failures_are_not_successful_results(self):
        for responses, text in [([RuntimeError("secret")], "회의"),
                                (["요약", "invalid"], "회의"),
                                (["요약", "[]", RuntimeError("context overflow")], "회의"),
                                ([], " ")]:
            with self.subTest(responses=responses):
                result, output, _ = self.run_fake(responses, text)
                self.assertEqual(result["status"], "실패")
                self.assertFalse((output / "meeting_액션.jsonl").exists())
                self.assertNotIn("secret", (output / "run.json").read_text(encoding="utf-8"))

    def test_document_formats(self):
        with tempfile.TemporaryDirectory() as folder:
            for suffix, member, ns in [
                ("docx", "word/document.xml", "http://schemas.openxmlformats.org/wordprocessingml/2006/main"),
                ("hwpx", "Contents/section0.xml", "http://www.hancom.co.kr/hwpml/2011/paragraph"),
            ]:
                path = Path(folder) / f"meeting.{suffix}"
                with ZipFile(path, "w") as archive:
                    archive.writestr(member, f'<root xmlns:p="{ns}"><p:p><p:r><p:t>회의 내용</p:t></p:r></p:p></root>')
                self.assertEqual(read_transcript(path), "회의 내용")
            with ZipFile(path, "w") as archive:
                archive.writestr(member, '<!DOCTYPE root><root/>')
            with self.assertRaises(ValueError):
                read_transcript(path)


if __name__ == "__main__":
    unittest.main()
