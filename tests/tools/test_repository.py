import json
import subprocess

import pytest

from arkui_ut_agent.tools import RepositoryReadTools, ToolRegistry, register_repository_read_tools


class StubRunner:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def completed(*, stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


def rg_match(path, line, column, text, match):
    return json.dumps(
        {
            "type": "match",
            "data": {
                "path": {"text": path},
                "lines": {"text": f"{text}\n"},
                "line_number": line,
                "submatches": [{"match": {"text": match}, "start": column - 1, "end": column - 1 + len(match)}],
            },
        }
    )


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    return root


def assert_failure(result, code, source):
    assert result.success is False
    assert result.diagnostics[0].code == code
    assert result.provenance[0].source == source


class TestReadFile:
    def test_reads_an_inclusive_line_range_with_provenance(self, repository):
        source = repository / "src" / "sample.cc"
        source.parent.mkdir()
        source.write_bytes(b"zero\none\ntwo\n")
        tools = RepositoryReadTools(repository)

        result = tools.read_file({"path": "src/sample.cc", "start_line": 2, "end_line": 3})

        assert result.success is True
        assert result.data == {
            "path": "src/sample.cc",
            "content": "one\ntwo\n",
            "start_line": 2,
            "end_line": 3,
            "total_lines": 3,
        }
        assert result.provenance[0].source == "read_file"
        assert result.provenance[0].location == "src/sample.cc"
        assert result.provenance[0].metadata == {"start_line": 2, "end_line": 3}

    @pytest.mark.parametrize(
        ("arguments", "code"),
        [
            ({"path": "missing.cc"}, "path_not_found"),
            ({"path": "."}, "not_a_file"),
        ],
    )
    def test_reports_expected_path_and_argument_failures(self, repository, arguments, code):
        result = RepositoryReadTools(repository).read_file(arguments)

        assert_failure(result, code, "read_file")

    @pytest.mark.parametrize(
        "line_arguments",
        [{"start_line": 0}, {"start_line": 2, "end_line": 1}],
    )
    def test_rejects_invalid_line_range(self, repository, line_arguments):
        (repository / "sample.cc").write_text("line one\nline two\n", encoding="utf-8")

        result = RepositoryReadTools(repository).read_file({"path": "sample.cc", **line_arguments})

        assert_failure(result, "invalid_arguments", "read_file")

    def test_rejects_paths_outside_repository(self, repository):
        outside = repository.parent / "outside.txt"
        outside.write_text("outside", encoding="utf-8")

        result = RepositoryReadTools(repository).read_file({"path": str(outside)})

        assert_failure(result, "path_outside_repository", "read_file")

    def test_reports_non_utf8_file(self, repository):
        (repository / "binary.dat").write_bytes(b"\xff\xfe")

        result = RepositoryReadTools(repository).read_file({"path": "binary.dat"})

        assert_failure(result, "file_decode_error", "read_file")


class TestListFiles:
    def test_lists_matching_files_in_deterministic_order_and_skips_git_directory(self, repository):
        (repository / "src" / "nested").mkdir(parents=True)
        (repository / ".git").mkdir()
        (repository / "src" / "z.py").write_text("", encoding="utf-8")
        (repository / "src" / "a.txt").write_text("", encoding="utf-8")
        (repository / "src" / "nested" / "b.py").write_text("", encoding="utf-8")
        (repository / ".git" / "ignored.py").write_text("", encoding="utf-8")

        result = RepositoryReadTools(repository).list_files({"glob": "*.py"})

        assert result.success is True
        assert result.data == {
            "files": ["src/z.py", "src/nested/b.py"],
            "count": 2,
            "truncated": False,
        }
        assert result.provenance[0].source == "list_files"
        assert result.provenance[0].location == "."

    def test_supports_non_recursive_listing_and_truncation_diagnostic(self, repository):
        (repository / "b.cc").write_text("", encoding="utf-8")
        (repository / "a.cc").write_text("", encoding="utf-8")
        (repository / "nested").mkdir()
        (repository / "nested" / "hidden.cc").write_text("", encoding="utf-8")

        result = RepositoryReadTools(repository).list_files(
            {"glob": "*.cc", "recursive": False, "max_results": 1}
        )

        assert result.success is True
        assert result.data == {"files": ["a.cc"], "count": 1, "truncated": True}
        assert result.diagnostics[0].code == "results_truncated"
        assert result.diagnostics[0].severity.value == "warning"

    @pytest.mark.parametrize(
        ("path", "code"),
        [("missing", "path_not_found"), ("file.txt", "not_a_directory")],
    )
    def test_reports_invalid_directories(self, repository, path, code):
        (repository / "file.txt").write_text("", encoding="utf-8")

        result = RepositoryReadTools(repository).list_files({"path": path})

        assert_failure(result, code, "list_files")


class TestRgSearch:
    def test_returns_structured_matches_and_uses_shell_free_process_arguments(self, repository):
        output = "\n".join(
            [
                rg_match("src/one.cc", 4, 3, "  Target();", "Target"),
                rg_match("src/two.cc", 8, 1, "Target();", "Target"),
            ]
        )
        runner = StubRunner(completed(stdout=output))
        tools = RepositoryReadTools(repository, runner=runner)

        result = tools.rg_search({"pattern": "Target", "path": ".", "glob": "*.cc"})

        assert result.success is True
        assert result.data == {
            "matches": [
                {"path": "src/one.cc", "line": 4, "column": 3, "text": "  Target();"},
                {"path": "src/two.cc", "line": 8, "column": 1, "text": "Target();"},
            ],
            "count": 2,
            "truncated": False,
        }
        command, kwargs = runner.calls[0]
        assert command == ["rg", "--json", "--color", "never", "--glob", "*.cc", "--", "Target", "."]
        assert kwargs["cwd"] == repository.resolve()
        assert "shell" not in kwargs
        assert result.provenance[0].metadata["pattern"] == "Target"

    def test_no_matches_is_a_successful_empty_result(self, repository):
        runner = StubRunner(completed(returncode=1))

        result = RepositoryReadTools(repository, runner=runner).rg_search({"pattern": "absent"})

        assert result.success is True
        assert result.data == {"matches": [], "count": 0, "truncated": False}

    def test_truncates_matches_with_a_warning(self, repository):
        output = "\n".join(
            [
                rg_match("one.cc", 1, 1, "match", "match"),
                rg_match("two.cc", 1, 1, "match", "match"),
            ]
        )
        runner = StubRunner(completed(stdout=output))

        result = RepositoryReadTools(repository, runner=runner).rg_search(
            {"pattern": "match", "max_results": 1}
        )

        assert result.success is True
        assert result.data["count"] == 1
        assert result.data["truncated"] is True
        assert result.diagnostics[0].code == "results_truncated"

    def test_reports_ripgrep_failure(self, repository):
        runner = StubRunner(completed(returncode=2, stderr="invalid regex"))

        result = RepositoryReadTools(repository, runner=runner).rg_search({"pattern": "["})

        assert_failure(result, "rg_failed", "rg_search")
        assert result.diagnostics[0].details == {"returncode": 2, "stderr": "invalid regex"}

    def test_reports_unavailable_ripgrep(self, repository):
        runner = StubRunner(FileNotFoundError())

        result = RepositoryReadTools(repository, runner=runner).rg_search({"pattern": "text"})

        assert_failure(result, "tool_unavailable", "rg_search")


class TestGitTools:
    def test_git_diff_returns_text_and_scoped_paths(self, repository):
        runner = StubRunner(completed(stdout="diff --git a/a.cc b/a.cc\n+new line\n"))
        tools = RepositoryReadTools(repository, runner=runner)

        result = tools.git_diff({"staged": True, "paths": ["src/missing.cc"]})

        assert result.success is True
        assert result.data == {
            "diff": "diff --git a/a.cc b/a.cc\n+new line\n",
            "staged": True,
            "paths": ["src/missing.cc"],
        }
        command, kwargs = runner.calls[0]
        assert command == [
            "git",
            "diff",
            "--no-ext-diff",
            "--no-color",
            "--cached",
            "--",
            "src/missing.cc",
        ]
        assert kwargs["cwd"] == repository.resolve()
        assert result.provenance[0].source == "git_diff"

    def test_git_status_returns_parsed_branch_and_changes(self, repository):
        raw = "## feature...origin/feature [ahead 1]\n M src/a.cc\n?? tests/new.cc\n"
        runner = StubRunner(completed(stdout=raw))

        result = RepositoryReadTools(repository, runner=runner).git_status({})

        assert result.success is True
        assert result.data == {
            "branch": "feature...origin/feature [ahead 1]",
            "entries": [
                {"status": " M", "path": "src/a.cc"},
                {"status": "??", "path": "tests/new.cc"},
            ],
            "clean": False,
            "raw": raw,
        }
        assert result.provenance[0].metadata == {"change_count": 2, "clean": False}

    @pytest.mark.parametrize(("method_name", "arguments"), [("git_diff", {}), ("git_status", {})])
    def test_git_command_failures_are_structured(self, repository, method_name, arguments):
        runner = StubRunner(completed(returncode=128, stderr="not a git repository"))
        tools = RepositoryReadTools(repository, runner=runner)

        result = getattr(tools, method_name)(arguments)

        assert_failure(result, "git_failed", method_name)
        assert result.summary == "not a git repository"


def test_registration_and_dispatch_normalize_repository_results(repository):
    source = repository / "sample.cc"
    source.write_text("content\n", encoding="utf-8")
    registry = ToolRegistry()
    register_repository_read_tools(registry, repository, runner=StubRunner(completed(returncode=1)))

    observation = registry.dispatch("read_file", {"path": "sample.cc"})
    failure = registry.dispatch("read_file", {"path": "missing.cc"})

    assert registry.names == ("git_diff", "git_status", "list_files", "read_file", "rg_search")
    assert observation.tool_name == "read_file"
    assert observation.success is True
    assert observation.data["content"] == "content\n"
    assert observation.provenance[0].location == "sample.cc"
    assert failure.tool_name == "read_file"
    assert failure.success is False
    assert failure.diagnostics[0].code == "path_not_found"
    assert failure.provenance[0].source == "read_file"


def test_registration_is_atomic_when_a_repository_tool_name_already_exists(repository):
    registry = ToolRegistry()
    registry.register("read_file", lambda _arguments: None)

    with pytest.raises(ValueError, match="already registered"):
        RepositoryReadTools(repository).register(registry)

    assert registry.names == ("read_file",)
