import hashlib

import pytest

import arkui_ut_agent.tools.editing as editing_module
from arkui_ut_agent.tools import EditingTools, ToolRegistry, register_editing_tools


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    return root


def assert_failure(result, code, source, location=None):
    assert result.success is False
    assert result.diagnostics[0].code == code
    assert result.diagnostics[0].location == location
    assert result.provenance[0].source == source
    assert result.provenance[0].location == location


def create_symlink_or_skip(link, target, *, directory=False):
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as exc:
        pytest.skip(f"Symlinks are unavailable in this environment: {exc}")


class TestWriteFile:
    def test_creates_utf8_file_and_preserves_explicit_newlines(self, repository):
        tools = EditingTools(repository)

        result = tools.write_file({"path": "created.txt", "content": "first\r\nsecond\n"})

        expected_bytes = b"first\r\nsecond\n"
        assert result.success is True
        assert (repository / "created.txt").read_bytes() == expected_bytes
        assert result.data == {
            "path": "created.txt",
            "operation": "create",
            "created": True,
            "overwritten": False,
            "changed": True,
            "bytes_written": len(expected_bytes),
            "before_sha256": None,
            "after_sha256": hashlib.sha256(expected_bytes).hexdigest(),
        }
        assert result.provenance[0].source == "write_file"
        assert result.provenance[0].location == "created.txt"

    def test_overwrites_existing_utf8_file(self, repository):
        target = repository / "target.txt"
        target.write_text("before", encoding="utf-8")

        result = EditingTools(repository).write_file({"path": "target.txt", "content": "after"})

        assert result.success is True
        assert target.read_text(encoding="utf-8") == "after"
        assert result.data["operation"] == "overwrite"
        assert result.data["created"] is False
        assert result.data["overwritten"] is True
        assert result.data["before_sha256"] == hashlib.sha256(b"before").hexdigest()

    def test_identical_content_is_successful_without_rewriting(self, repository, monkeypatch):
        target = repository / "target.txt"
        target.write_text("same", encoding="utf-8")

        def unexpected_write(_path, _content):
            raise AssertionError("identical content must not be rewritten")

        monkeypatch.setattr(editing_module, "_write_utf8", unexpected_write)
        result = EditingTools(repository).write_file({"path": "target.txt", "content": "same"})

        assert result.success is True
        assert result.data["operation"] == "unchanged"
        assert result.data["changed"] is False
        assert result.data["bytes_written"] == 0

    def test_missing_parent_requires_explicit_create_parents(self, repository):
        tools = EditingTools(repository)

        failure = tools.write_file({"path": "nested/target.txt", "content": "content"})

        assert_failure(failure, "parent_not_found", "write_file", "nested/target.txt")
        assert not (repository / "nested").exists()

        success = tools.write_file(
            {"path": "nested/target.txt", "content": "content", "create_parents": True}
        )

        assert success.success is True
        assert (repository / "nested" / "target.txt").read_text(encoding="utf-8") == "content"

    def test_overwrite_false_preserves_existing_file(self, repository):
        target = repository / "target.txt"
        target.write_text("original", encoding="utf-8")

        result = EditingTools(repository).write_file(
            {"path": "target.txt", "content": "replacement", "overwrite": False}
        )

        assert_failure(result, "file_exists", "write_file", "target.txt")
        assert target.read_text(encoding="utf-8") == "original"

    @pytest.mark.parametrize(
        "arguments",
        [
            {},
            {"path": "target.txt"},
            {"path": "target.txt", "content": 1},
            {"path": "target.txt", "content": "x", "create_parents": "yes"},
            {"path": "target.txt", "content": "x", "overwrite": 1},
            {"path": "target.txt", "content": "x", "unexpected": True},
        ],
    )
    def test_rejects_invalid_arguments(self, repository, arguments):
        result = EditingTools(repository).write_file(arguments)

        assert_failure(result, "invalid_arguments", "write_file")


class TestApplyPatch:
    def test_replaces_one_exact_fragment_and_preserves_newlines(self, repository):
        target = repository / "sample.cc"
        target.write_bytes(b"before\r\nold value\r\nafter\r\n")

        result = EditingTools(repository).apply_patch(
            {"path": "sample.cc", "old_text": "old value", "new_text": "new value"}
        )

        assert result.success is True
        assert target.read_bytes() == b"before\r\nnew value\r\nafter\r\n"
        assert result.data["operation"] == "replace"
        assert result.data["replacements"] == 1
        assert result.data["old_text"] == "old value"
        assert result.data["new_text"] == "new value"
        assert result.data["before_sha256"] != result.data["after_sha256"]
        assert result.provenance[0].source == "apply_patch"
        assert result.provenance[0].location == "sample.cc"

    def test_target_mismatch_is_structured_and_does_not_modify_file(self, repository):
        target = repository / "sample.cc"
        target.write_text("current", encoding="utf-8")

        result = EditingTools(repository).apply_patch(
            {"path": "sample.cc", "old_text": "stale", "new_text": "replacement"}
        )

        assert_failure(result, "patch_target_not_found", "apply_patch", "sample.cc")
        assert result.diagnostics[0].details == {"occurrences": 0}
        assert target.read_text(encoding="utf-8") == "current"

    def test_multiple_targets_are_a_conflict_and_do_not_modify_file(self, repository):
        target = repository / "sample.cc"
        target.write_text("same\nsame\n", encoding="utf-8")

        result = EditingTools(repository).apply_patch(
            {"path": "sample.cc", "old_text": "same", "new_text": "changed"}
        )

        assert_failure(result, "patch_conflict", "apply_patch", "sample.cc")
        assert result.diagnostics[0].details == {"occurrences": 2}
        assert target.read_text(encoding="utf-8") == "same\nsame\n"

    def test_overlapping_targets_are_a_conflict_and_do_not_modify_file(self, repository):
        target = repository / "sample.cc"
        target.write_text("aaa", encoding="utf-8")

        result = EditingTools(repository).apply_patch(
            {"path": "sample.cc", "old_text": "aa", "new_text": "changed"}
        )

        assert_failure(result, "patch_conflict", "apply_patch", "sample.cc")
        assert result.diagnostics[0].details == {"occurrences": 2}
        assert target.read_text(encoding="utf-8") == "aaa"

    def test_missing_target_file_is_structured(self, repository):
        result = EditingTools(repository).apply_patch(
            {"path": "missing.cc", "old_text": "before", "new_text": "after"}
        )

        assert_failure(result, "path_not_found", "apply_patch", "missing.cc")

    @pytest.mark.parametrize(
        "arguments",
        [
            {},
            {"path": "sample.cc", "old_text": "", "new_text": "new"},
            {"path": "sample.cc", "old_text": "same", "new_text": "same"},
            {"path": "sample.cc", "old_text": "old", "new_text": 1},
            {"path": "sample.cc", "old_text": "old", "new_text": "new", "count": 1},
        ],
    )
    def test_rejects_invalid_arguments(self, repository, arguments):
        result = EditingTools(repository).apply_patch(arguments)

        assert_failure(result, "invalid_arguments", "apply_patch")


@pytest.mark.parametrize("method_name", ["write_file", "apply_patch"])
@pytest.mark.parametrize("path_kind", ["absolute", "parent"])
def test_editing_rejects_absolute_and_parent_paths(repository, method_name, path_kind):
    path = str(repository / "absolute.txt") if path_kind == "absolute" else "nested/../escaped.txt"
    arguments = {"path": path, "content": "new"}
    if method_name == "apply_patch":
        arguments = {"path": path, "old_text": "old", "new_text": "new"}

    result = getattr(EditingTools(repository), method_name)(arguments)

    expected_code = "absolute_path_not_allowed" if path_kind == "absolute" else "path_outside_repository"
    assert_failure(result, expected_code, method_name, path)


@pytest.mark.parametrize("method_name", ["write_file", "apply_patch"])
def test_editing_rejects_file_symlink_even_when_it_points_outside(repository, method_name):
    outside = repository.parent / "outside.txt"
    outside.write_text("outside old", encoding="utf-8")
    link = repository / "link.txt"
    create_symlink_or_skip(link, outside)
    arguments = {"path": "link.txt", "content": "changed"}
    if method_name == "apply_patch":
        arguments = {"path": "link.txt", "old_text": "old", "new_text": "new"}

    result = getattr(EditingTools(repository), method_name)(arguments)

    assert_failure(result, "symlink_not_allowed", method_name, "link.txt")
    assert outside.read_text(encoding="utf-8") == "outside old"


def test_write_file_rejects_symlink_parent_escape(repository):
    outside = repository.parent / "outside"
    outside.mkdir()
    link = repository / "linked"
    create_symlink_or_skip(link, outside, directory=True)

    result = EditingTools(repository).write_file({"path": "linked/new.txt", "content": "changed"})

    assert_failure(result, "symlink_not_allowed", "write_file", "linked/new.txt")
    assert not (outside / "new.txt").exists()


class TestEncodingAndIoFailures:
    @pytest.mark.parametrize("method_name", ["write_file", "apply_patch"])
    def test_non_utf8_existing_file_is_structured(self, repository, method_name):
        (repository / "binary.dat").write_bytes(b"\xff\xfe")
        arguments = {"path": "binary.dat", "content": "text"}
        if method_name == "apply_patch":
            arguments = {"path": "binary.dat", "old_text": "old", "new_text": "new"}

        result = getattr(EditingTools(repository), method_name)(arguments)

        assert_failure(result, "file_decode_error", method_name, "binary.dat")

    def test_unencodable_content_is_structured(self, repository):
        result = EditingTools(repository).write_file(
            {"path": "nested/target.txt", "content": "\ud800", "create_parents": True}
        )

        assert_failure(result, "file_encode_error", "write_file", "nested/target.txt")
        assert not (repository / "nested").exists()

    def test_write_io_failure_is_structured(self, repository, monkeypatch):
        def fail_write(_path, _content):
            raise PermissionError("read-only filesystem")

        monkeypatch.setattr(editing_module, "_write_utf8", fail_write)

        result = EditingTools(repository).write_file({"path": "target.txt", "content": "content"})

        assert_failure(result, "file_write_error", "write_file", "target.txt")
        assert result.diagnostics[0].details["exception_type"] == "PermissionError"

    def test_patch_read_io_failure_is_structured(self, repository, monkeypatch):
        (repository / "target.txt").write_text("old", encoding="utf-8")

        def fail_read(_path):
            raise PermissionError("access denied")

        monkeypatch.setattr(editing_module, "_read_utf8", fail_read)
        result = EditingTools(repository).apply_patch(
            {"path": "target.txt", "old_text": "old", "new_text": "new"}
        )

        assert_failure(result, "file_read_error", "apply_patch", "target.txt")
        assert result.diagnostics[0].details["exception_type"] == "PermissionError"


def test_registration_and_dispatch_preserve_success_failure_and_provenance(repository):
    target = repository / "target.txt"
    target.write_text("old", encoding="utf-8")
    registry = ToolRegistry()
    register_editing_tools(registry, repository)

    success = registry.dispatch(
        "apply_patch",
        {"path": "target.txt", "old_text": "old", "new_text": "new"},
    )
    failure = registry.dispatch(
        "apply_patch",
        {"path": "target.txt", "old_text": "missing", "new_text": "other"},
    )

    assert registry.names == ("apply_patch", "write_file")
    assert success.tool_name == "apply_patch"
    assert success.success is True
    assert success.data["replacements"] == 1
    assert success.provenance[0].location == "target.txt"
    assert failure.tool_name == "apply_patch"
    assert failure.success is False
    assert failure.diagnostics[0].code == "patch_target_not_found"
    assert failure.provenance[0].source == "apply_patch"


def test_registration_is_atomic_when_an_editing_tool_name_already_exists(repository):
    registry = ToolRegistry()
    registry.register("write_file", lambda _arguments: None)

    with pytest.raises(ValueError, match="already registered"):
        EditingTools(repository).register(registry)

    assert registry.names == ("write_file",)
