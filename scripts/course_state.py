from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
COURSE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
COURSE_STATUSES = {"planning", "active", "blocked", "complete"}
MILESTONE_STATUSES = {"pending", "in_progress", "complete", "blocked"}
EXPECTED_MILESTONE_IDS = [str(index) for index in range(1, 7)]
PROGRESS_ARCHIVE_THRESHOLD = 400
PROGRESS_SECTION_PATTERN = re.compile(r"^## .+ \| 阶段 (?P<phase_id>[0-9]+)\s*$")
LESSON_STATUSES = {
    "planned",
    "in_progress",
    "blocked",
    "awaiting_confirmation",
    "complete",
}
VERIFICATION_STATUSES = {"passed", "failed"}
CHECKPOINT_KEYS = {
    "course_status",
    "phase_id",
    "phase_status",
    "current_lesson",
    "next_lesson",
    "summary",
    "decisions",
    "blockers",
    "goal",
    "stack",
    "acceptance",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _templates_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "assets" / "templates"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read valid UTF-8 JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object in {path}")
    return value


def _write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8", newline="\n")


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        _write_text(
            temp_path,
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        )
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _new_course_id() -> str:
    return f"course-{uuid.uuid4().hex[:8]}"


def _validate_course_id(course_id: str) -> None:
    if not COURSE_ID_PATTERN.fullmatch(course_id):
        raise ValueError(
            "course_id must use 3-64 lowercase letters, digits, or hyphens"
        )


def _course_dir(project_root: str | Path) -> Path:
    return Path(project_root).resolve() / ".course"


def _load_state(project_root: str | Path) -> tuple[Path, dict[str, Any]]:
    course_dir = _course_dir(project_root)
    state_path = course_dir / "course.json"
    if not state_path.is_file():
        raise FileNotFoundError(f"course state does not exist: {state_path}")
    return course_dir, _read_json(state_path)


def _require_string(value: Any, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    normalized = value.strip()
    if not normalized and not allow_empty:
        raise ValueError(f"{field} must not be empty")
    return normalized


def _validate_relative_path(value: Any, field: str) -> str:
    normalized = _require_string(value, field)
    path = Path(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{field} must be a project-relative path")
    return path.as_posix()


def _validate_verification(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise ValueError("current_lesson.verification must be a list")
    result: list[dict[str, str]] = []
    for index, item in enumerate(value):
        field = f"current_lesson.verification[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{field} must be an object")
        scope = _require_string(item.get("scope"), f"{field}.scope")
        if scope not in {"demo", "project"}:
            raise ValueError(f"{field}.scope must be 'demo' or 'project'")
        status = _require_string(item.get("status"), f"{field}.status")
        if status not in VERIFICATION_STATUSES:
            raise ValueError(f"{field}.status must be 'passed' or 'failed'")
        result.append(
            {
                "scope": scope,
                "command": _require_string(item.get("command"), f"{field}.command"),
                "status": status,
                "summary": _require_string(item.get("summary"), f"{field}.summary"),
            }
        )
    return result


def _validate_lesson(
    value: Any,
    *,
    course_id: str,
) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("current_lesson must be an object or null")

    lesson_id = _require_string(value.get("id"), "current_lesson.id")
    if not COURSE_ID_PATTERN.fullmatch(lesson_id):
        raise ValueError("current_lesson.id must be a lowercase slug")
    status = _require_string(value.get("status"), "current_lesson.status")
    if status not in LESSON_STATUSES:
        raise ValueError(f"unsupported current_lesson.status: {status}")

    demo_path = value.get("demo_path", "")
    doc_path = value.get("doc_path", "")
    main_files = value.get("main_files", [])
    if not isinstance(main_files, list):
        raise ValueError("current_lesson.main_files must be a list")
    normalized_main_files = [
        _validate_relative_path(item, "current_lesson.main_files")
        for item in main_files
    ]
    verification = _validate_verification(value.get("verification", []))

    if status in {"awaiting_confirmation", "complete"}:
        demo_path = _validate_relative_path(demo_path, "current_lesson.demo_path")
        doc_path = _validate_relative_path(doc_path, "current_lesson.doc_path")
        expected_demo_prefix = f"learning-labs/{course_id}/"
        expected_doc_prefix = f"docs/course/{course_id}/lessons/"
        if not demo_path.startswith(expected_demo_prefix):
            raise ValueError(
                f"current_lesson.demo_path must start with {expected_demo_prefix}"
            )
        if not doc_path.startswith(expected_doc_prefix):
            raise ValueError(
                f"current_lesson.doc_path must start with {expected_doc_prefix}"
            )
        if not normalized_main_files:
            raise ValueError("at least one main project file is required")
        passed_scopes = {
            item["scope"] for item in verification if item["status"] == "passed"
        }
        if "demo" not in passed_scopes:
            raise ValueError("passed demo verification is required")
        if "project" not in passed_scopes:
            raise ValueError("passed project verification is required")
    else:
        demo_path = (
            _validate_relative_path(demo_path, "current_lesson.demo_path")
            if demo_path
            else ""
        )
        doc_path = (
            _validate_relative_path(doc_path, "current_lesson.doc_path")
            if doc_path
            else ""
        )

    return {
        "id": lesson_id,
        "title": _require_string(value.get("title"), "current_lesson.title"),
        "status": status,
        "demo_path": demo_path,
        "doc_path": doc_path,
        "main_files": normalized_main_files,
        "verification": verification,
    }


def _validate_next_lesson(value: Any) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("next_lesson must be an object or null")
    lesson_id = _require_string(value.get("id"), "next_lesson.id")
    if not COURSE_ID_PATTERN.fullmatch(lesson_id):
        raise ValueError("next_lesson.id must be a lowercase slug")
    return {
        "id": lesson_id,
        "title": _require_string(value.get("title"), "next_lesson.title"),
    }


def _validate_phase(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("phase must be an object")
    phase_id = _require_string(value.get("id"), "phase.id")
    if phase_id != "0" and phase_id not in EXPECTED_MILESTONE_IDS:
        raise ValueError("phase.id is invalid")
    status = _require_string(value.get("status"), "phase.status")
    if status not in MILESTONE_STATUSES:
        raise ValueError("phase.status is invalid")
    return {
        "id": phase_id,
        "title": _require_string(value.get("title"), "phase.title"),
        "status": status,
    }


def _validate_decisions(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise ValueError("decisions must be a list")
    result: list[dict[str, str]] = []
    for index, item in enumerate(value):
        field = f"decisions[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{field} must be an object")
        result.append(
            {
                "id": _require_string(item.get("id"), f"{field}.id"),
                "decision": _require_string(
                    item.get("decision"), f"{field}.decision"
                ),
                "reason": _require_string(item.get("reason"), f"{field}.reason"),
            }
        )
    return result


def _validate_blockers(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("blockers must be a list")
    return [_require_string(item, "blockers[]") for item in value]


def init_course(
    project_root: str | Path,
    *,
    mode: str,
    title: str,
    course_id: str | None = None,
    now: datetime | None = None,
) -> Path:
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"project root does not exist: {root}")
    if mode not in {"new", "existing"}:
        raise ValueError("mode must be 'new' or 'existing'")
    title = title.strip()
    if not title:
        raise ValueError("title must not be empty")

    final_dir = root / ".course"
    if final_dir.exists():
        raise FileExistsError(f"course state already exists: {final_dir}")

    resolved_course_id = course_id or _new_course_id()
    _validate_course_id(resolved_course_id)
    created_at = _timestamp(now or _utc_now())
    templates = _templates_dir()
    temp_dir = root / f".course.tmp-{uuid.uuid4().hex}"

    try:
        temp_dir.mkdir()
        state = _read_json(templates / "course.json")
        state.update(
            {
                "schema_version": SCHEMA_VERSION,
                "course_id": resolved_course_id,
                "title": title,
                "mode": mode,
                "created_at": created_at,
                "updated_at": created_at,
            }
        )
        _write_text(
            temp_dir / "course.json",
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        )
        plan = (templates / "plan.md").read_text(encoding="utf-8")
        _write_text(temp_dir / "plan.md", plan.replace("{{TITLE}}", title))
        progress = (templates / "progress.md").read_text(encoding="utf-8")
        progress = progress.replace("{{TITLE}}", title).replace(
            "{{CREATED_AT}}", created_at
        )
        _write_text(temp_dir / "progress.md", progress)
        os.replace(temp_dir, final_dir)
    except Exception:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        raise

    return final_dir / "course.json"


def apply_checkpoint(
    project_root: str | Path,
    payload_path: str | Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    course_dir, state = _load_state(root)
    payload = _read_json(Path(payload_path).resolve())
    unknown_keys = sorted(set(payload) - CHECKPOINT_KEYS)
    if unknown_keys:
        raise ValueError(f"unknown checkpoint fields: {', '.join(unknown_keys)}")

    course_status = _require_string(payload.get("course_status"), "course_status")
    if course_status not in COURSE_STATUSES:
        raise ValueError(f"unsupported course_status: {course_status}")
    phase_id = _require_string(payload.get("phase_id"), "phase_id")
    phase_status = _require_string(payload.get("phase_status"), "phase_status")
    if phase_status not in MILESTONE_STATUSES:
        raise ValueError(f"unsupported phase_status: {phase_status}")

    milestones = state.get("milestones")
    if not isinstance(milestones, list):
        raise ValueError("course.json milestones must be a list")
    milestone = next(
        (item for item in milestones if isinstance(item, dict) and item.get("id") == phase_id),
        None,
    )
    if phase_id == "0":
        phase_title = "需求访谈与方案选择"
    elif milestone is None:
        raise ValueError(f"unknown phase_id: {phase_id}")
    else:
        phase_title = _require_string(milestone.get("title"), "milestone.title")

    course_id = _require_string(state.get("course_id"), "course.json course_id")
    current_lesson = _validate_lesson(
        payload.get("current_lesson"), course_id=course_id
    )
    next_lesson = _validate_next_lesson(payload.get("next_lesson"))
    summary = _require_string(payload.get("summary"), "summary")
    if len(summary) > 2000:
        raise ValueError("summary must not exceed 2000 characters")
    decisions = _validate_decisions(payload.get("decisions"))
    blockers = _validate_blockers(payload.get("blockers"))

    merged_decisions = list(state.get("decisions", []))
    decision_by_id = {
        item.get("id"): item for item in merged_decisions if isinstance(item, dict)
    }
    for decision in decisions:
        existing = decision_by_id.get(decision["id"])
        if existing is not None and existing != decision:
            raise ValueError(
                f"decision {decision['id']} already exists with different content"
            )
        if existing is None:
            merged_decisions.append(decision)
            decision_by_id[decision["id"]] = decision

    updated_at = _timestamp(now or _utc_now())
    state.update(
        {
            "status": course_status,
            "updated_at": updated_at,
            "phase": {
                "id": phase_id,
                "title": phase_title,
                "status": phase_status,
            },
            "current_lesson": current_lesson,
            "next_lesson": next_lesson,
            "summary": summary,
            "decisions": merged_decisions,
            "blockers": blockers,
            "recent_verification": (
                current_lesson["verification"] if current_lesson else []
            ),
        }
    )

    if milestone is not None:
        milestone["status"] = phase_status
    if "goal" in payload:
        state["goal"] = _require_string(payload["goal"], "goal")
    if "stack" in payload:
        stack = payload["stack"]
        if not isinstance(stack, list):
            raise ValueError("stack must be a list")
        state["stack"] = [_require_string(item, "stack[]") for item in stack]
    if "acceptance" in payload:
        if not isinstance(payload["acceptance"], dict):
            raise ValueError("acceptance must be an object")
        state["acceptance"] = payload["acceptance"]

    if current_lesson and current_lesson["status"] == "complete":
        completed = list(state.get("completed_lessons", []))
        completed = [
            item
            for item in completed
            if not isinstance(item, dict) or item.get("id") != current_lesson["id"]
        ]
        completed.append(current_lesson)
        state["completed_lessons"] = completed

    validation_errors = _validation_errors(
        root,
        course_dir,
        state,
        complete=course_status == "complete",
    )
    if validation_errors:
        prefix = (
            "course cannot be completed"
            if course_status == "complete"
            else "checkpoint is invalid"
        )
        raise ValueError(f"{prefix}: " + "; ".join(validation_errors))

    _atomic_write_json(course_dir / "course.json", state)
    _append_progress(course_dir / "progress.md", state, decisions)
    return state


def _append_progress(
    progress_path: Path,
    state: dict[str, Any],
    new_decisions: list[dict[str, str]],
) -> None:
    lesson = state.get("current_lesson") or {}
    next_lesson = state.get("next_lesson") or {}
    lines = [
        "",
        f"## {state['updated_at']} | 阶段 {state['phase']['id']}",
        "",
        f"- 课程状态：`{state['status']}`",
        f"- 当前课程节：{lesson.get('id', '无')} {lesson.get('title', '')}".rstrip(),
        f"- 课程节状态：`{lesson.get('status', '无')}`",
        f"- 摘要：{state['summary']}",
        f"- 下一课程节：{next_lesson.get('id', '待规划')} {next_lesson.get('title', '')}".rstrip(),
    ]
    for decision in new_decisions:
        lines.append(
            f"- 决策 `{decision['id']}`：{decision['decision']}，原因：{decision['reason']}"
        )
    for verification in lesson.get("verification", []):
        lines.append(
            f"- 验证 `{verification['scope']}`：`{verification['status']}` - "
            f"`{verification['command']}`"
        )
    for blocker in state.get("blockers", []):
        lines.append(f"- 阻塞：{blocker}")
    with progress_path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write("\n".join(lines) + "\n")
    _archive_completed_progress(progress_path, state)


def _archive_completed_progress(
    progress_path: Path,
    state: dict[str, Any],
) -> None:
    lines = progress_path.read_text(encoding="utf-8").splitlines(keepends=True)
    if len(lines) <= PROGRESS_ARCHIVE_THRESHOLD:
        return

    completed_phase_ids = {
        str(item.get("id"))
        for item in state.get("milestones", [])
        if isinstance(item, dict) and item.get("status") == "complete"
    }
    current_phase_id = str((state.get("phase") or {}).get("id", ""))
    archive_phase_ids = completed_phase_ids - {current_phase_id}
    if not archive_phase_ids:
        return

    sections: list[tuple[str | None, list[str]]] = []
    current_id: str | None = None
    current_lines: list[str] = []
    for line in lines:
        match = PROGRESS_SECTION_PATTERN.match(line.rstrip("\r\n"))
        if match:
            sections.append((current_id, current_lines))
            current_id = match.group("phase_id")
            current_lines = [line]
        else:
            current_lines.append(line)
    sections.append((current_id, current_lines))

    archive_dir = progress_path.parent / "archive"
    archive_dir.mkdir(exist_ok=True)
    kept_sections: list[str] = []
    archived_ids: set[str] = set()
    for phase_id, section_lines in sections:
        if phase_id in archive_phase_ids:
            archive_path = archive_dir / f"phase-{phase_id}.md"
            previous = (
                archive_path.read_text(encoding="utf-8").rstrip()
                if archive_path.is_file()
                else ""
            )
            section = "".join(section_lines).strip()
            content = "\n\n".join(part for part in (previous, section) if part)
            _write_text(archive_path, content + "\n")
            archived_ids.add(phase_id)
        else:
            kept_sections.append("".join(section_lines))

    _write_text(progress_path, "".join(kept_sections))
    index_lines = [
        "# 已归档课程进度",
        "",
        "旧阶段记录保留在以下文件，当前阶段仍保留在 `progress.md`：",
        "",
    ]
    for archive_path in sorted(archive_dir.glob("phase-*.md")):
        phase_id = archive_path.stem.removeprefix("phase-")
        index_lines.append(f"- 阶段 {phase_id}：`archive/{archive_path.name}`")
    if archived_ids:
        _write_text(archive_dir / "index.md", "\n".join(index_lines) + "\n")


def build_resume(project_root: str | Path, *, max_chars: int = 4000) -> str:
    if max_chars < 300:
        raise ValueError("max_chars must be at least 300")
    course_dir, state = _load_state(project_root)
    phase = state.get("phase") or {}
    current = state.get("current_lesson") or {}
    next_lesson = state.get("next_lesson") or {}
    decisions = state.get("decisions") or []
    verification = state.get("recent_verification") or []

    lines = [
        f"# 课程恢复：{state.get('title', '未命名课程')}",
        "",
        f"- Course ID：`{state.get('course_id', '')}`",
        f"- 模式：`{state.get('mode', '')}`",
        f"- 课程状态：`{state.get('status', '')}`",
        f"- 当前阶段：{phase.get('id', '')} {phase.get('title', '')} (`{phase.get('status', '')}`)",
        f"- 当前课程节：{current.get('id', '无')} {current.get('title', '')} (`{current.get('status', '无')}`)",
        f"- 下一课程节：{next_lesson.get('id', '待规划')} {next_lesson.get('title', '')}",
        f"- 计划文件：`{(course_dir / 'plan.md').as_posix()}`",
        f"- 进度文件：`{(course_dir / 'progress.md').as_posix()}`",
        "",
        "## 当前摘要",
        "",
        str(state.get("summary", "")),
    ]
    if state.get("blockers"):
        lines.extend(["", "## 阻塞", ""])
        lines.extend(f"- {item}" for item in state["blockers"])
    if decisions:
        lines.extend(["", "## 最近决策", ""])
        lines.extend(
            f"- `{item.get('id', '')}` {item.get('decision', '')}"
            for item in decisions[-5:]
        )
    if verification:
        lines.extend(["", "## 最近验证", ""])
        lines.extend(
            f"- `{item.get('scope', '')}` `{item.get('status', '')}` "
            f"`{item.get('command', '')}`"
            for item in verification[-4:]
        )

    output = "\n".join(lines).rstrip() + "\n"
    if len(output) <= max_chars:
        return output
    marker = "\n\n[恢复摘要已按长度上限截断]\n"
    return output[: max_chars - len(marker)].rstrip() + marker


def _path_exists(root: Path, value: Any, field: str, *, directory: bool = False) -> str | None:
    try:
        relative = _validate_relative_path(value, field)
    except ValueError as exc:
        return str(exc)
    target = root / relative
    exists = target.is_dir() if directory else target.is_file()
    if not exists:
        expected = "directory" if directory else "file"
        return f"{field} {expected} does not exist: {relative}"
    return None


def _lesson_artifact_errors(
    root: Path,
    lesson: dict[str, Any],
    field: str,
) -> list[str]:
    errors: list[str] = []
    for value, name, directory in (
        (lesson["demo_path"], f"{field}.demo_path", True),
        (lesson["doc_path"], f"{field}.doc_path", False),
    ):
        error = _path_exists(root, value, name, directory=directory)
        if error:
            errors.append(error)
    for index, path in enumerate(lesson["main_files"]):
        error = _path_exists(root, path, f"{field}.main_files[{index}]")
        if error:
            errors.append(error)
    return errors


def _state_structure_errors(root: Path, state: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    try:
        _validate_phase(state.get("phase"))
    except (TypeError, ValueError) as exc:
        errors.append(str(exc))

    milestones = state.get("milestones")
    if not isinstance(milestones, list) or not milestones:
        errors.append("course milestones are missing")
    else:
        milestone_ids: list[str] = []
        for index, milestone in enumerate(milestones):
            field = f"milestones[{index}]"
            if not isinstance(milestone, dict):
                errors.append(f"{field} must be an object")
                continue
            milestone_id = milestone.get("id")
            if not isinstance(milestone_id, str) or not milestone_id.strip():
                errors.append(f"{field}.id must not be empty")
            else:
                milestone_ids.append(milestone_id.strip())
            if not isinstance(milestone.get("title"), str) or not milestone["title"].strip():
                errors.append(f"{field}.title must not be empty")
            if milestone.get("status") not in MILESTONE_STATUSES:
                errors.append(f"{field}.status is invalid")
        if milestone_ids != EXPECTED_MILESTONE_IDS:
            errors.append("milestones must contain phases 1 through 6 in order")

    course_id = str(state.get("course_id", ""))
    try:
        current_lesson = _validate_lesson(
            state.get("current_lesson"),
            course_id=course_id,
        )
    except (TypeError, ValueError) as exc:
        errors.append(f"current_lesson: {exc}")
    else:
        if current_lesson and current_lesson["status"] in {
            "awaiting_confirmation",
            "complete",
        }:
            errors.extend(
                _lesson_artifact_errors(root, current_lesson, "current_lesson")
            )

    try:
        _validate_next_lesson(state.get("next_lesson"))
    except (TypeError, ValueError) as exc:
        errors.append(f"next_lesson: {exc}")
    try:
        _validate_decisions(state.get("decisions"))
    except (TypeError, ValueError) as exc:
        errors.append(f"decisions: {exc}")
    try:
        _validate_blockers(state.get("blockers"))
    except (TypeError, ValueError) as exc:
        errors.append(f"blockers: {exc}")
    return errors


def _completion_errors(root: Path, state: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if state.get("status") != "complete":
        errors.append("course status must be complete")

    phase = state.get("phase")
    if not isinstance(phase, dict) or phase.get("id") != "6" or phase.get("status") != "complete":
        errors.append("final phase 6 must be complete")

    milestones = state.get("milestones")
    if not isinstance(milestones, list) or not milestones:
        errors.append("course milestones are missing")
    elif any(
        not isinstance(item, dict) or item.get("status") != "complete"
        for item in milestones
    ):
        errors.append("all milestones must be complete")

    if state.get("blockers"):
        errors.append("course blockers must be empty")

    current_lesson = state.get("current_lesson")
    if isinstance(current_lesson, dict) and current_lesson.get("status") != "complete":
        errors.append("current lesson must be complete before course completion")

    completed_lessons = state.get("completed_lessons")
    if not isinstance(completed_lessons, list) or not completed_lessons:
        errors.append("at least one completed formal lesson is required")
    else:
        course_id = str(state.get("course_id", ""))
        for index, lesson in enumerate(completed_lessons):
            field = f"completed_lessons[{index}]"
            try:
                normalized = _validate_lesson(
                    {**lesson, "status": "complete"}
                    if isinstance(lesson, dict)
                    else lesson,
                    course_id=course_id,
                )
            except (TypeError, ValueError) as exc:
                errors.append(f"{field}: {exc}")
                continue
            assert normalized is not None
            errors.extend(_lesson_artifact_errors(root, normalized, field))

    acceptance = state.get("acceptance")
    if not isinstance(acceptance, dict):
        errors.append("acceptance must be an object")
        return errors

    if acceptance.get("requirements_mapped") is not True:
        errors.append("requirements mapping must be complete")
    else:
        error = _path_exists(
            root,
            acceptance.get("requirements_map_path"),
            "acceptance.requirements_map_path",
        )
        if error:
            errors.append(error)

    if acceptance.get("architecture_current") is not True:
        errors.append("architecture documentation must be current")
    else:
        error = _path_exists(
            root,
            acceptance.get("architecture_path"),
            "acceptance.architecture_path",
        )
        if error:
            errors.append(error)

    for key in ("start_command", "test_command"):
        command = acceptance.get(key)
        if not isinstance(command, dict):
            errors.append(f"acceptance.{key} must be recorded")
            continue
        if not isinstance(command.get("command"), str) or not command["command"].strip():
            errors.append(f"acceptance.{key}.command must not be empty")
        if command.get("status") != "passed":
            errors.append(f"acceptance.{key} must have passed")

    excluded = acceptance.get("excluded_launch_work")
    if not isinstance(excluded, list) or not excluded:
        errors.append("excluded launch work must be documented")
    return errors


def _validation_errors(
    root: Path,
    course_dir: Path,
    state: dict[str, Any],
    *,
    complete: bool,
) -> list[str]:
    errors: list[str] = []
    if state.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    try:
        _validate_course_id(str(state.get("course_id", "")))
    except ValueError as exc:
        errors.append(str(exc))
    if not isinstance(state.get("title"), str) or not state["title"].strip():
        errors.append("course title must not be empty")
    if state.get("mode") not in {"new", "existing"}:
        errors.append("course mode must be new or existing")
    if state.get("status") not in COURSE_STATUSES:
        errors.append("course status is invalid")
    if not (course_dir / "plan.md").is_file():
        errors.append(".course/plan.md is missing")
    if not (course_dir / "progress.md").is_file():
        errors.append(".course/progress.md is missing")
    if not isinstance(state.get("summary"), str):
        errors.append("course summary must be a string")
    errors.extend(_state_structure_errors(root, state))
    if complete:
        errors.extend(_completion_errors(root, state))
    return errors


def validate_course(project_root: str | Path, *, complete: bool = False) -> list[str]:
    root = Path(project_root).resolve()
    try:
        course_dir, state = _load_state(root)
    except (FileNotFoundError, ValueError) as exc:
        return [str(exc)]
    return _validation_errors(root, course_dir, state, complete=complete)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Initialize, resume, checkpoint, and validate a teaching course."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="initialize .course state")
    init_parser.add_argument("project_root")
    init_parser.add_argument("--mode", choices=("new", "existing"), required=True)
    init_parser.add_argument("--title", required=True)

    resume_parser = subparsers.add_parser("resume", help="print bounded context")
    resume_parser.add_argument("project_root")
    resume_parser.add_argument("--max-chars", type=int, default=4000)

    checkpoint_parser = subparsers.add_parser(
        "checkpoint", help="apply a JSON checkpoint"
    )
    checkpoint_parser.add_argument("project_root")
    checkpoint_parser.add_argument("--payload", required=True)

    validate_parser = subparsers.add_parser("validate", help="validate course state")
    validate_parser.add_argument("project_root")
    validate_parser.add_argument("--complete", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "init":
            state_path = init_course(
                args.project_root,
                mode=args.mode,
                title=args.title,
            )
            print(f"[OK] Initialized course state: {state_path}")
        elif args.command == "resume":
            print(
                build_resume(args.project_root, max_chars=args.max_chars),
                end="",
            )
        elif args.command == "checkpoint":
            state = apply_checkpoint(args.project_root, args.payload)
            print(
                f"[OK] Checkpoint saved: phase {state['phase']['id']}, "
                f"status {state['status']}"
            )
        else:
            errors = validate_course(args.project_root, complete=args.complete)
            if errors:
                for error in errors:
                    print(f"[ERROR] {error}", file=sys.stderr)
                return 1
            print("[OK] Course state is valid")
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
