from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import course_state


FIXED_NOW = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
COURSE_ID = "course-a1b2c3d4"


class CourseStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temp_dir.name) / "中文 项目"
        self.project_root.mkdir()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def init_course(self) -> dict:
        course_state.init_course(
            self.project_root,
            mode="new",
            title="商用战斗系统课程",
            course_id=COURSE_ID,
            now=FIXED_NOW,
        )
        return self.read_state()

    def read_state(self) -> dict:
        return json.loads(
            (self.project_root / ".course" / "course.json").read_text(
                encoding="utf-8"
            )
        )

    def write_payload(self, payload: dict) -> Path:
        payload_path = self.project_root / "checkpoint.json"
        payload_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return payload_path

    def test_init_creates_utf8_state_and_templates(self) -> None:
        state = self.init_course()

        self.assertEqual(state["schema_version"], 1)
        self.assertEqual(state["course_id"], COURSE_ID)
        self.assertEqual(state["title"], "商用战斗系统课程")
        self.assertEqual(state["mode"], "new")
        self.assertEqual(state["status"], "planning")
        self.assertEqual(state["phase"]["id"], "0")
        self.assertEqual(len(state["milestones"]), 6)
        self.assertTrue((self.project_root / ".course" / "plan.md").is_file())
        self.assertTrue((self.project_root / ".course" / "progress.md").is_file())
        self.assertIn(
            "商用战斗系统课程",
            (self.project_root / ".course" / "plan.md").read_text(
                encoding="utf-8"
            ),
        )

    def test_init_refuses_to_overwrite_existing_course(self) -> None:
        self.init_course()

        with self.assertRaisesRegex(FileExistsError, "already exists"):
            course_state.init_course(
                self.project_root,
                mode="new",
                title="另一个课程",
                course_id="course-deadbeef",
                now=FIXED_NOW,
            )

    def test_init_existing_mode_preserves_project_source(self) -> None:
        source = self.project_root / "src" / "main.py"
        source.parent.mkdir()
        source.write_text("print('existing project')\n", encoding="utf-8")

        course_state.init_course(
            self.project_root,
            mode="existing",
            title="既有项目教学",
            course_id=COURSE_ID,
            now=FIXED_NOW,
        )

        self.assertEqual(source.read_text(encoding="utf-8"), "print('existing project')\n")
        self.assertEqual(self.read_state()["mode"], "existing")

    def test_checkpoint_merges_decision_and_appends_progress(self) -> None:
        self.init_course()
        payload = {
            "course_status": "active",
            "phase_id": "1",
            "phase_status": "in_progress",
            "current_lesson": {
                "id": "01-walking-skeleton",
                "title": "可运行 Walking Skeleton",
                "status": "in_progress",
                "demo_path": "learning-labs/course-a1b2c3d4/01-walking-skeleton",
                "doc_path": "docs/course/course-a1b2c3d4/lessons/01-walking-skeleton.md",
                "main_files": [],
                "verification": [],
            },
            "next_lesson": {
                "id": "02-core-contracts",
                "title": "核心抽象与接口",
            },
            "summary": "已选择模块化单体并开始建立可运行骨架。",
            "decisions": [
                {
                    "id": "D-001",
                    "decision": "采用模块化单体",
                    "reason": "当前规模下边界清晰且部署简单。",
                }
            ],
            "blockers": [],
        }

        course_state.apply_checkpoint(
            self.project_root,
            self.write_payload(payload),
            now=FIXED_NOW,
        )

        state = self.read_state()
        self.assertEqual(state["status"], "active")
        self.assertEqual(state["phase"]["id"], "1")
        self.assertEqual(state["current_lesson"]["id"], "01-walking-skeleton")
        self.assertEqual(state["decisions"][0]["id"], "D-001")
        progress = (self.project_root / ".course" / "progress.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("01-walking-skeleton", progress)
        self.assertIn("已选择模块化单体", progress)

    def test_long_progress_archives_completed_phase(self) -> None:
        self.init_course()
        progress_path = self.project_root / ".course" / "progress.md"
        progress_path.write_text(
            "\n".join(
                [
                    "# 课程进度",
                    *(["历史记录"] * 399),
                    "## 2026-08-07T12:00:00Z | 阶段 1",
                    "旧阶段记录",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        state = self.read_state()
        state["phase"] = {
            "id": "2",
            "title": "核心抽象与接口",
            "status": "in_progress",
        }
        state["milestones"][0]["status"] = "complete"
        state["summary"] = "进入第二阶段。"

        course_state._append_progress(progress_path, state, [])

        archive = self.project_root / ".course" / "archive" / "phase-1.md"
        index = self.project_root / ".course" / "archive" / "index.md"
        current_progress = progress_path.read_text(encoding="utf-8")
        self.assertTrue(archive.is_file())
        self.assertIn("旧阶段记录", archive.read_text(encoding="utf-8"))
        self.assertNotIn("旧阶段记录", current_progress)
        self.assertIn("archive/phase-1.md", index.read_text(encoding="utf-8"))

    def test_formal_lesson_requires_demo_and_project_verification(self) -> None:
        self.init_course()
        payload = {
            "course_status": "active",
            "phase_id": "1",
            "phase_status": "in_progress",
            "current_lesson": {
                "id": "01-walking-skeleton",
                "title": "可运行 Walking Skeleton",
                "status": "awaiting_confirmation",
                "demo_path": "learning-labs/course-a1b2c3d4/01-walking-skeleton",
                "doc_path": "docs/course/course-a1b2c3d4/lessons/01-walking-skeleton.md",
                "main_files": ["src/main.py"],
                "verification": [
                    {
                        "scope": "demo",
                        "command": "python demo.py",
                        "status": "passed",
                        "summary": "Demo 运行成功",
                    }
                ],
            },
            "next_lesson": None,
            "summary": "骨架完成。",
            "decisions": [],
            "blockers": [],
        }

        with self.assertRaisesRegex(ValueError, "project verification"):
            course_state.apply_checkpoint(
                self.project_root,
                self.write_payload(payload),
                now=FIXED_NOW,
            )

    def test_formal_lesson_rejects_failed_project_verification(self) -> None:
        self.init_course()
        payload = {
            "course_status": "active",
            "phase_id": "1",
            "phase_status": "in_progress",
            "current_lesson": {
                "id": "01-walking-skeleton",
                "title": "可运行 Walking Skeleton",
                "status": "awaiting_confirmation",
                "demo_path": "learning-labs/course-a1b2c3d4/01-walking-skeleton",
                "doc_path": "docs/course/course-a1b2c3d4/lessons/01-walking-skeleton.md",
                "main_files": ["src/main.py"],
                "verification": [
                    {
                        "scope": "demo",
                        "command": "python main.py",
                        "status": "passed",
                        "summary": "Demo passed",
                    },
                    {
                        "scope": "project",
                        "command": "python -m unittest",
                        "status": "failed",
                        "summary": "Project tests failed",
                    },
                ],
            },
            "next_lesson": None,
            "summary": "主项目测试失败。",
            "decisions": [],
            "blockers": ["修复主项目测试"],
        }

        with self.assertRaisesRegex(ValueError, "passed project verification"):
            course_state.apply_checkpoint(
                self.project_root,
                self.write_payload(payload),
                now=FIXED_NOW,
            )

    def test_awaiting_confirmation_requires_artifacts_to_exist(self) -> None:
        self.init_course()
        payload = {
            "course_status": "active",
            "phase_id": "1",
            "phase_status": "in_progress",
            "current_lesson": {
                "id": "01-walking-skeleton",
                "title": "可运行 Walking Skeleton",
                "status": "awaiting_confirmation",
                "demo_path": "learning-labs/course-a1b2c3d4/01-walking-skeleton",
                "doc_path": "docs/course/course-a1b2c3d4/lessons/01-walking-skeleton.md",
                "main_files": ["src/main.py"],
                "verification": [
                    {
                        "scope": "demo",
                        "command": "python main.py",
                        "status": "passed",
                        "summary": "Demo passed",
                    },
                    {
                        "scope": "project",
                        "command": "python -m unittest",
                        "status": "passed",
                        "summary": "Project passed",
                    },
                ],
            },
            "next_lesson": None,
            "summary": "骨架完成。",
            "decisions": [],
            "blockers": [],
        }

        with self.assertRaisesRegex(ValueError, "demo_path directory does not exist"):
            course_state.apply_checkpoint(
                self.project_root,
                self.write_payload(payload),
                now=FIXED_NOW,
            )

    def test_validate_rejects_malformed_lesson_and_milestone(self) -> None:
        self.init_course()
        state = self.read_state()
        state["milestones"][0]["status"] = "mystery"
        state["current_lesson"] = {"id": "broken-lesson"}
        (self.project_root / ".course" / "course.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        errors = course_state.validate_course(self.project_root)

        self.assertIn("milestones[0].status is invalid", errors)
        self.assertTrue(
            any(error.startswith("current_lesson:") for error in errors),
            errors,
        )

    def test_validate_requires_all_six_milestones(self) -> None:
        self.init_course()
        state = self.read_state()
        state["milestones"] = state["milestones"][:-1]
        (self.project_root / ".course" / "course.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        errors = course_state.validate_course(self.project_root)

        self.assertIn("milestones must contain phases 1 through 6 in order", errors)

    def test_validate_rejects_malformed_current_phase(self) -> None:
        self.init_course()
        state = self.read_state()
        state["phase"]["status"] = "mystery"
        (self.project_root / ".course" / "course.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        errors = course_state.validate_course(self.project_root)

        self.assertIn("phase.status is invalid", errors)

    def test_resume_is_bounded_and_contains_current_context(self) -> None:
        self.init_course()
        state = self.read_state()
        state["summary"] = "当前上下文" + ("很长" * 2000)
        (self.project_root / ".course" / "course.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        output = course_state.build_resume(self.project_root, max_chars=1200)

        self.assertLessEqual(len(output), 1200)
        self.assertIn("商用战斗系统课程", output)
        self.assertIn("当前阶段", output)

    def test_complete_validation_requires_integrated_artifacts(self) -> None:
        self.init_course()
        state = self.read_state()
        state["status"] = "complete"
        state["milestones"] = [
            {**milestone, "status": "complete"}
            for milestone in state["milestones"]
        ]
        state["acceptance"] = {
            "requirements_mapped": True,
            "requirements_map_path": "docs/acceptance-map.md",
            "architecture_current": True,
            "architecture_path": "docs/architecture.md",
            "start_command": {"command": "python -m app", "status": "passed"},
            "test_command": {"command": "python -m unittest", "status": "passed"},
            "excluded_launch_work": ["云部署"],
        }
        (self.project_root / ".course" / "course.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        errors = course_state.validate_course(self.project_root, complete=True)

        self.assertIn("at least one completed formal lesson is required", errors)

    def test_complete_validation_passes_for_integrated_course(self) -> None:
        self.init_course()
        demo = self.project_root / "learning-labs" / COURSE_ID / "01-skeleton"
        demo.mkdir(parents=True)
        (demo / "main.py").write_text("print('demo')\n", encoding="utf-8")
        doc = (
            self.project_root
            / "docs"
            / "course"
            / COURSE_ID
            / "lessons"
            / "01-skeleton.md"
        )
        doc.parent.mkdir(parents=True)
        doc.write_text("# Walking Skeleton\n", encoding="utf-8")
        main_file = self.project_root / "src" / "main.py"
        main_file.parent.mkdir()
        main_file.write_text("print('project')\n", encoding="utf-8")
        architecture = self.project_root / "docs" / "architecture.md"
        architecture.write_text("# Architecture\n", encoding="utf-8")
        requirements_map = self.project_root / "docs" / "acceptance-map.md"
        requirements_map.write_text("# Acceptance map\n", encoding="utf-8")

        state = self.read_state()
        state["status"] = "complete"
        state["phase"] = {"id": "6", "title": "完整项目验收与课程复盘", "status": "complete"}
        state["milestones"] = [
            {**milestone, "status": "complete"}
            for milestone in state["milestones"]
        ]
        state["completed_lessons"] = [
            {
                "id": "01-skeleton",
                "title": "Walking Skeleton",
                "demo_path": f"learning-labs/{COURSE_ID}/01-skeleton",
                "doc_path": f"docs/course/{COURSE_ID}/lessons/01-skeleton.md",
                "main_files": ["src/main.py"],
                "verification": [
                    {
                        "scope": "demo",
                        "command": "python main.py",
                        "status": "passed",
                        "summary": "Demo passed",
                    },
                    {
                        "scope": "project",
                        "command": "python src/main.py",
                        "status": "passed",
                        "summary": "Project passed",
                    },
                ],
            }
        ]
        state["blockers"] = []
        state["acceptance"] = {
            "requirements_mapped": True,
            "requirements_map_path": "docs/acceptance-map.md",
            "architecture_current": True,
            "architecture_path": "docs/architecture.md",
            "start_command": {"command": "python src/main.py", "status": "passed"},
            "test_command": {"command": "python -m unittest", "status": "passed"},
            "excluded_launch_work": ["真实云部署", "线上监控验证"],
        }
        (self.project_root / ".course" / "course.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        errors = course_state.validate_course(self.project_root, complete=True)

        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
