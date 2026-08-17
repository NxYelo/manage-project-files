import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import manage_project_files as mpf


class ManageProjectFilesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.workspace = self.base / "团队工作区"
        self.knowledge = self.base / "团队知识库"

    def tearDown(self):
        self.temp.cleanup()

    def run_main(self, *args):
        output = StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            code = mpf.main(list(args))
        return code, output.getvalue()

    def initialize(self):
        code, _ = self.run_main(
            "init-workspace", "--root", str(self.workspace),
            "--knowledge-root", str(self.knowledge), "--apply"
        )
        self.assertEqual(code, 0)
        code, _ = self.run_main(
            "init-project", "--root", str(self.workspace),
            "--name", "202609 示例项目", "--with-unfiled", "--apply"
        )
        self.assertEqual(code, 0)
        return self.workspace / "202609 示例项目"

    def test_preview_is_read_only_and_apply_builds_full_tree(self):
        code, output = self.run_main("init-workspace", "--root", str(self.workspace))
        self.assertEqual(code, 0)
        self.assertIn("未执行任何写入", output)
        self.assertFalse(self.workspace.exists())

        project = self.initialize()
        policy = mpf.load_policy()
        for name in policy["required_dirs"] + ["99_未归档"]:
            self.assertTrue((project / name).is_dir(), name)
        self.assertTrue((project / "00_README.md").is_file())
        self.assertTrue((project / "00_文件分类与归档规则.md").is_file())
        self.assertTrue((self.knowledge / "案例与复盘").is_dir())
        index = (self.workspace / "00_项目总索引.md").read_text(encoding="utf-8")
        self.assertEqual(index.count("| 202609 示例项目 |"), 1)

        original = project / "00_README.md"
        original.write_text("自定义内容", encoding="utf-8")
        code, _ = self.run_main(
            "init-project", "--root", str(self.workspace),
            "--name", "202609 示例项目", "--apply"
        )
        self.assertEqual(code, 0)
        self.assertEqual(original.read_text(encoding="utf-8"), "自定义内容")

    def test_classification_explains_destination_and_flow(self):
        code, output = self.run_main(
            "classify", "--name", "客户确认邮件.pdf", "--kind", "requirement"
        )
        self.assertEqual(code, 0)
        self.assertIn("01_需求输入", output)
        self.assertIn("下一步", output)
        code, output = self.run_main(
            "classify", "--name", "不确定.bin", "--kind", "unknown", "--format", "json"
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["destination"], "00_输入待分配")

    def test_audit_detects_violations_and_allowlisted_infrastructure(self):
        project = self.initialize()
        (project / ".git").mkdir()
        (project / "杂目录").mkdir()
        (project / "散放方案.docx").write_bytes(b"doc")
        (project / "98_历史归档" / "旧稿.docx").write_bytes(b"old")
        marked_folder = project / "98_历史归档" / "批次_旧版"
        marked_folder.mkdir()
        nested = marked_folder / "内部文件.docx"
        nested.write_bytes(b"nested")
        (project / "04_项目执行资料" / "脚本_workbuddy_202609011200.md").write_text("x", encoding="utf-8")
        issues = mpf.audit_issues(self.workspace, mpf.load_policy())
        codes = {item["code"] for item in issues}
        self.assertIn("extra_root_dir", codes)
        self.assertIn("root_business_file", codes)
        self.assertIn("archive_marker_missing", codes)
        self.assertIn("ai_suffix_full_name", codes)
        self.assertFalse(any(item["path"].endswith(".git") for item in issues))
        self.assertFalse(any(item["path"] == str(nested) for item in issues))

    def test_plan_mapping_apply_and_verify(self):
        project = self.initialize()
        loose = project / "执行稿.docx"
        loose.write_bytes(b"v1")
        mapping = self.base / "mapping.json"
        mapping.write_text(json.dumps({"202609 示例项目/执行稿.docx": "04_项目执行资料"}, ensure_ascii=False), encoding="utf-8")
        plan = self.base / "plan.json"
        code, _ = self.run_main(
            "plan-fix", "--root", str(self.workspace), "--mapping", str(mapping), "--output", str(plan)
        )
        self.assertEqual(code, 0)
        data = json.loads(plan.read_text(encoding="utf-8"))
        move = next(a for a in data["actions"] if a.get("source") == str(loose.resolve()) and a["type"] == "move")
        code, output = self.run_main(
            "apply", "--plan", str(plan), "--approve", move["id"], "--confirm", "APPLY"
        )
        self.assertEqual(code, 0, output)
        self.assertFalse(loose.exists())
        self.assertTrue((project / "04_项目执行资料" / "执行稿.docx").exists())
        code, output = self.run_main("verify", "--root", str(self.workspace))
        self.assertEqual(code, 0, output)

    def test_source_change_stops_apply_without_partial_execution(self):
        project = self.initialize()
        old = project / "98_历史归档" / "历史稿.docx"
        old.write_bytes(b"v1")
        plan = self.base / "plan.json"
        code, _ = self.run_main("plan-fix", "--root", str(self.workspace), "--output", str(plan))
        self.assertEqual(code, 0)
        data = json.loads(plan.read_text(encoding="utf-8"))
        rename = next(a for a in data["actions"] if a.get("source") == str(old.resolve()))
        old.write_bytes(b"changed")
        code, output = self.run_main(
            "apply", "--plan", str(plan), "--approve", rename["id"], "--confirm", "APPLY"
        )
        self.assertEqual(code, 2)
        self.assertIn("发生变化", output)
        self.assertTrue(old.exists())

    def test_invalid_name_and_relaxed_exception_are_rejected(self):
        self.initialize()
        code, output = self.run_main(
            "init-project", "--root", str(self.workspace), "--name", "无日期项目", "--apply"
        )
        self.assertEqual(code, 2)
        self.assertIn("YYYYMM", output)
        config = self.base / "bad.json"
        config.write_text(json.dumps({"intake_days": 30}), encoding="utf-8")
        code, output = self.run_main("audit", "--root", str(self.workspace), "--config", str(config))
        self.assertEqual(code, 2)
        self.assertIn("不能放宽", output)


if __name__ == "__main__":
    unittest.main()
