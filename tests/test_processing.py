"""Тесты пакета harness.processing.

Покрывает:
  * base.py     — estimate_tokens, compute_budget_bytes,
                  check_budget_consistency, Chunk
  * config.py   — загрузка YAML, дефолты, fail-fast, warning
  * docs.py     — DocProcessor.split / merge / validate

Без реальной модели. Без сети. Все тесты синхронные.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from harness.processing.base import (
    CHUNK_OVERHEAD_TOKENS,
    Chunk,
    ProcessError,
    ValidationIssue,
    check_budget_consistency,
    compute_budget_bytes,
    estimate_tokens,
    has_errors,
)
from harness.processing.config import (
    CodeConfig,
    DefaultsConfig,
    DocsConfig,
    ProcessingConfig,
    ProcessingConfigError,
    load as load_config,
)
from harness.processing.docs import DocProcessor


# ══════════════════════════════════════════════════════════════════════════
# base: estimate_tokens
# ══════════════════════════════════════════════════════════════════════════

class TestEstimateTokens:
    def test_empty(self):
        assert estimate_tokens("") == 0

    def test_ascii_short(self):
        for s in ("a", "ab", "abc", "abcd"):
            assert estimate_tokens(s) == 1, s

    def test_ascii_medium(self):
        assert estimate_tokens("abcde") == 2
        assert estimate_tokens("abcdefgh") == 2

    def test_cyrillic_is_two_bytes_per_char(self):
        # "Привет" = 6 символов × 2 байта = 12 байт → 3 токена
        assert estimate_tokens("Привет") == 3

    def test_cyrillic_longer(self):
        # "привет мир" = 9 кириллических символов × 2 + 1 пробел = 19
        assert estimate_tokens("привет мир") == 5

    def test_mixed_ascii_cyrillic(self):
        assert estimate_tokens("Hello Привет") == 5

    def test_emoji(self):
        assert estimate_tokens("🎉") == 1

    def test_newlines_count(self):
        assert estimate_tokens("\n\n\n\n\n") == 2

    def test_proportional_to_bytes_not_chars(self):
        ascii_s = "a" * 100       # 100 байт
        cyr_s   = "а" * 100       # 200 байт
        assert estimate_tokens(cyr_s) == 2 * estimate_tokens(ascii_s)


# ══════════════════════════════════════════════════════════════════════════
# base: compute_budget_bytes
# ══════════════════════════════════════════════════════════════════════════

class TestComputeBudgetBytes:
    def test_happy_path(self):
        result = compute_budget_bytes(
            num_ctx=4096,
            prompt_text="",
            output_reserve_ratio=0.4,
            budget_safety=0.7,
        )
        expected = int(4046 * 0.6 * 0.7 * 4)
        assert result == expected

    def test_cyrillic_prompt_consumes_more_budget(self):
        ascii_budget = compute_budget_bytes(
            num_ctx=4096, prompt_text="a" * 100,
            output_reserve_ratio=0.4, budget_safety=0.7,
        )
        cyr_budget = compute_budget_bytes(
            num_ctx=4096, prompt_text="а" * 100,
            output_reserve_ratio=0.4, budget_safety=0.7,
        )
        assert cyr_budget < ascii_budget

    def test_num_ctx_zero(self):
        with pytest.raises(ProcessError, match="num_ctx must be positive"):
            compute_budget_bytes(
                num_ctx=0, prompt_text="",
                output_reserve_ratio=0.4, budget_safety=0.7,
            )

    def test_num_ctx_negative(self):
        with pytest.raises(ProcessError, match="num_ctx must be positive"):
            compute_budget_bytes(
                num_ctx=-1, prompt_text="",
                output_reserve_ratio=0.4, budget_safety=0.7,
            )

    def test_prompt_too_large(self):
        with pytest.raises(ProcessError, match="prompt too large"):
            compute_budget_bytes(
                num_ctx=2048,
                prompt_text="x" * 20000,
                output_reserve_ratio=0.4, budget_safety=0.7,
            )

    def test_budget_too_small(self):
        with pytest.raises(ProcessError, match="computed budget"):
            compute_budget_bytes(
                num_ctx=100, prompt_text="",
                output_reserve_ratio=0.4, budget_safety=0.7,
            )

    def test_output_reserve_ratio_out_of_range_low(self):
        with pytest.raises(ProcessError, match="output_reserve_ratio"):
            compute_budget_bytes(
                num_ctx=4096, prompt_text="",
                output_reserve_ratio=0.0, budget_safety=0.7,
            )

    def test_output_reserve_ratio_out_of_range_high(self):
        with pytest.raises(ProcessError, match="output_reserve_ratio"):
            compute_budget_bytes(
                num_ctx=4096, prompt_text="",
                output_reserve_ratio=1.0, budget_safety=0.7,
            )

    def test_budget_safety_out_of_range_zero(self):
        with pytest.raises(ProcessError, match="budget_safety"):
            compute_budget_bytes(
                num_ctx=4096, prompt_text="",
                output_reserve_ratio=0.4, budget_safety=0.0,
            )

    def test_budget_safety_out_of_range_high(self):
        with pytest.raises(ProcessError, match="budget_safety"):
            compute_budget_bytes(
                num_ctx=4096, prompt_text="",
                output_reserve_ratio=0.4, budget_safety=1.5,
            )

    def test_custom_chunk_overhead(self):
        base = compute_budget_bytes(
            num_ctx=4096, prompt_text="",
            output_reserve_ratio=0.4, budget_safety=0.7,
            chunk_overhead_tokens=50,
        )
        larger = compute_budget_bytes(
            num_ctx=4096, prompt_text="",
            output_reserve_ratio=0.4, budget_safety=0.7,
            chunk_overhead_tokens=200,
        )
        assert larger < base

    def test_default_overhead_constant(self):
        a = compute_budget_bytes(
            num_ctx=4096, prompt_text="",
            output_reserve_ratio=0.4, budget_safety=0.7,
        )
        b = compute_budget_bytes(
            num_ctx=4096, prompt_text="",
            output_reserve_ratio=0.4, budget_safety=0.7,
            chunk_overhead_tokens=CHUNK_OVERHEAD_TOKENS,
        )
        assert a == b


# ══════════════════════════════════════════════════════════════════════════
# base: check_budget_consistency
# ══════════════════════════════════════════════════════════════════════════

class TestCheckBudgetConsistency:
    def test_budget_greater_than_min_ok(self):
        check_budget_consistency(1000, 800)

    def test_budget_equals_min_ok(self):
        check_budget_consistency(800, 800)

    def test_budget_less_than_min_fails(self):
        with pytest.raises(ProcessError, match="budget"):
            check_budget_consistency(700, 800)


# ══════════════════════════════════════════════════════════════════════════
# base: Chunk
# ══════════════════════════════════════════════════════════════════════════

class TestChunk:
    def test_byte_size(self):
        c = Chunk(index=1, total=1, text="Привет",
                  preamble="", start=0, end=6, kind="whole")
        assert c.byte_size == 12

    def test_preamble_byte_size_empty(self):
        c = Chunk(index=1, total=1, text="x",
                  preamble="", start=0, end=1, kind="whole")
        assert c.preamble_byte_size == 0
        assert c.total_byte_size == 1

    def test_total_byte_size(self):
        c = Chunk(index=1, total=1, text="abc",
                  preamble="Привет", start=0, end=3, kind="whole")
        assert c.byte_size == 3
        assert c.preamble_byte_size == 12
        assert c.total_byte_size == 15

    def test_frozen(self):
        c = Chunk(index=1, total=1, text="x",
                  preamble="", start=0, end=1, kind="whole")
        with pytest.raises(Exception):
            c.index = 2  # type: ignore[misc]


# ══════════════════════════════════════════════════════════════════════════
# base: has_errors
# ══════════════════════════════════════════════════════════════════════════

class TestHasErrors:
    def test_empty(self):
        assert not has_errors([])

    def test_only_warnings(self):
        assert not has_errors([
            ValidationIssue("warning", "x", "y"),
            ValidationIssue("info", "x", "y"),
        ])

    def test_with_error(self):
        assert has_errors([
            ValidationIssue("warning", "x", "y"),
            ValidationIssue("error", "x", "y"),
        ])


# ══════════════════════════════════════════════════════════════════════════
# config: load
# ══════════════════════════════════════════════════════════════════════════

def _write(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "processing.yaml"
    p.write_text(dedent(content), encoding="utf-8")
    return p


class TestConfigLoad:
    def test_none_path_returns_defaults(self):
        cfg = load_config(None)
        assert cfg == ProcessingConfig()
        assert cfg.defaults.chunk_min_bytes == 800
        assert cfg.defaults.budget_safety == 0.7

    def test_missing_file_returns_defaults(self, tmp_path: Path):
        cfg = load_config(tmp_path / "missing.yaml")
        assert cfg.defaults.chunk_min_bytes == 800

    def test_empty_file_returns_defaults(self, tmp_path: Path):
        p = tmp_path / "processing.yaml"
        p.write_text("", encoding="utf-8")
        cfg = load_config(p)
        assert cfg.defaults.chunk_min_bytes == 800

    def test_only_comments_returns_defaults(self, tmp_path: Path):
        p = _write(tmp_path, """
            # только комментарии
        """)
        cfg = load_config(p)
        assert cfg.defaults.chunk_min_bytes == 800

    def test_full_valid_config(self, tmp_path: Path):
        p = _write(tmp_path, """
            version: 1
            defaults:
              chunk_target_bytes: 2000
              chunk_min_bytes: 500
              budget_safety: 0.6
              output_reserve_ratio: 0.3
              overlap_bytes: 100
              on_chunk_failure: partial
              on_merge_invalid: fail
              on_insufficient_context: skip
              insufficient_context_marker: "NEED_MORE"
            code:
              extensions: [.py]
              include_preamble: true
              preamble_max_bytes: 3000
              validate: [ast_parseable, defs_preserved]
            docs:
              extensions: [.md, .txt]
              header_levels: [1, 2, 3]
              validate: [headers_preserved]
        """)
        cfg = load_config(p)
        assert cfg.defaults.chunk_target_bytes == 2000
        assert cfg.defaults.chunk_min_bytes == 500
        assert cfg.defaults.budget_safety == 0.6
        assert cfg.defaults.output_reserve_ratio == 0.3
        assert cfg.defaults.on_chunk_failure == "partial"
        assert cfg.defaults.on_insufficient_context == "skip"
        assert cfg.code.extensions == (".py",)
        assert cfg.code.include_preamble is True
        assert cfg.code.preamble_max_bytes == 3000
        assert cfg.docs.extensions == (".md", ".txt")
        assert cfg.docs.header_levels == (1, 2, 3)

    def test_top_level_unknown_key_fails(self, tmp_path: Path):
        p = _write(tmp_path, "unknown_top: 1")
        with pytest.raises(ProcessingConfigError, match="unknown key"):
            load_config(p)

    def test_defaults_unknown_key_fails(self, tmp_path: Path):
        p = _write(tmp_path, """
            defaults:
              chunk_target_byte: 2000
        """)
        with pytest.raises(ProcessingConfigError, match="unknown key"):
            load_config(p)

    def test_code_unknown_key_fails(self, tmp_path: Path):
        p = _write(tmp_path, """
            code:
              magic: true
        """)
        with pytest.raises(ProcessingConfigError, match="unknown key"):
            load_config(p)

    def test_int_type_fail(self, tmp_path: Path):
        p = _write(tmp_path, """
            defaults:
              chunk_min_bytes: "abc"
        """)
        with pytest.raises(ProcessingConfigError, match="expected int"):
            load_config(p)

    def test_bool_as_int_fails(self, tmp_path: Path):
        p = _write(tmp_path, """
            defaults:
              chunk_min_bytes: true
        """)
        with pytest.raises(ProcessingConfigError, match="got bool"):
            load_config(p)

    def test_float_range_fail_high(self, tmp_path: Path):
        p = _write(tmp_path, """
            defaults:
              budget_safety: 1.5
        """)
        with pytest.raises(ProcessingConfigError, match="must be in"):
            load_config(p)

    def test_float_range_fail_low(self, tmp_path: Path):
        p = _write(tmp_path, """
            defaults:
              output_reserve_ratio: 0.0
        """)
        with pytest.raises(ProcessingConfigError, match="must be in"):
            load_config(p)

    def test_enum_fail(self, tmp_path: Path):
        p = _write(tmp_path, """
            defaults:
              on_chunk_failure: maybe
        """)
        with pytest.raises(ProcessingConfigError, match="must be one of"):
            load_config(p)

    def test_extensions_normalized(self, tmp_path: Path):
        p = _write(tmp_path, """
            code:
              extensions: [py, .PY, .pyw]
            docs:
              extensions: [md]
        """)
        cfg = load_config(p)
        assert cfg.code.extensions == (".py", ".pyw")
        assert cfg.docs.extensions == (".md",)

    def test_header_levels_sorted_unique(self, tmp_path: Path):
        p = _write(tmp_path, """
            docs:
              header_levels: [3, 1, 2, 1]
        """)
        cfg = load_config(p)
        assert cfg.docs.header_levels == (1, 2, 3)

    def test_header_levels_out_of_range(self, tmp_path: Path):
        p = _write(tmp_path, """
            docs:
              header_levels: [0, 1]
        """)
        with pytest.raises(ProcessingConfigError, match="outside"):
            load_config(p)

    def test_validate_unknown_check_fails(self, tmp_path: Path):
        p = _write(tmp_path, """
            code:
              validate: [magic_check]
        """)
        with pytest.raises(ProcessingConfigError, match="not in"):
            load_config(p)

    def test_invalid_regex_fails(self, tmp_path: Path):
        p = _write(tmp_path, """
            docs:
              paragraph_separator: "["
        """)
        with pytest.raises(ProcessingConfigError, match="regex"):
            load_config(p)

    def test_warning_on_partial_chunk_failure(self, tmp_path: Path,
                                               capsys):
        p = _write(tmp_path, """
            defaults:
              on_chunk_failure: partial
        """)
        load_config(p)
        err = capsys.readouterr().err
        assert "on_chunk_failure=partial" in err

    def test_warning_on_partial_merge(self, tmp_path: Path, capsys):
        p = _write(tmp_path, """
            defaults:
              on_merge_invalid: partial
        """)
        load_config(p)
        err = capsys.readouterr().err
        assert "on_merge_invalid=partial" in err

    def test_no_warning_on_fail(self, tmp_path: Path, capsys):
        p = _write(tmp_path, """
            defaults:
              on_chunk_failure: fail
              on_merge_invalid: fail
        """)
        load_config(p)
        err = capsys.readouterr().err
        assert "partial" not in err


# ══════════════════════════════════════════════════════════════════════════
# DocProcessor fixtures
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def docs_proc() -> DocProcessor:
    """Дефолтный DocProcessor с chunk_min_bytes=0.

    Ноль нужен, чтобы мелкие тестовые чанки не склеивались в один
    (в проде дефолт 800 байт — там это правильно, здесь мешает).
    """
    return DocProcessor(DocsConfig(), DefaultsConfig(chunk_min_bytes=0))


@pytest.fixture
def docs_proc_min100() -> DocProcessor:
    return DocProcessor(
        DocsConfig(),
        DefaultsConfig(chunk_min_bytes=0),
    )


# ══════════════════════════════════════════════════════════════════════════
# DocProcessor: split
# ══════════════════════════════════════════════════════════════════════════

class TestDocSplit:
    def test_empty_source_raises(self, docs_proc: DocProcessor):
        with pytest.raises(ProcessError, match="empty source"):
            docs_proc.split("", 10000)

    def test_whitespace_only_raises(self, docs_proc: DocProcessor):
        with pytest.raises(ProcessError, match="empty source"):
            docs_proc.split("   \n\n  \t ", 10000)

    def test_budget_too_small(self, docs_proc: DocProcessor):
        with pytest.raises(ProcessError, match="budget_bytes"):
            docs_proc.split("hello", 5)

    def test_whole_when_fits(self, docs_proc: DocProcessor):
        src = "short text"
        chunks, issues = docs_proc.split(src, 10000)
        assert len(chunks) == 1
        assert chunks[0].kind == "whole"
        assert chunks[0].index == 1 and chunks[0].total == 1
        assert chunks[0].start == 0 and chunks[0].end == len(src)
        assert chunks[0].text == src
        assert issues == []

    def test_split_by_headers(self, docs_proc: DocProcessor):
        src = (
            "# A\n\n"
            "Content A.\n\n"
            "# B\n\n"
            "Content B.\n\n"
            "# C\n\n"
            "Content C.\n"
        )
        chunks, issues = docs_proc.split(src, 30)
        assert len(chunks) == 3
        assert all(c.kind == "section" for c in chunks)
        assert all(c.total == 3 for c in chunks)

    def test_preamble_before_first_header(self, docs_proc: DocProcessor):
        src = (
            "Intro paragraph.\n\n"
            "# A\n\n"
            "Content A.\n\n"
            "# B\n\n"
            "Content B.\n"
        )
        chunks, _ = docs_proc.split(src, 30)
        kinds = [c.kind for c in chunks]
        assert kinds[0] == "preamble"
        assert "section" in kinds

    def test_paragraph_split_no_headers(self,
                                        docs_proc_min100: DocProcessor):
        src = "P1" * 40 + "\n\n" + "P2" * 40 + "\n\n" + "P3" * 40
        chunks, issues = docs_proc_min100.split(src, 100)
        assert len(chunks) >= 3
        assert all(c.kind == "paragraph" for c in chunks)
        assert issues == []

    def test_hard_split_single_paragraph(self,
                                          docs_proc_min100: DocProcessor):
        src = "x" * 1000
        chunks, issues = docs_proc_min100.split(src, 100)
        assert len(chunks) > 1
        assert all(c.kind == "hard_split" for c in chunks)
        assert any(i.code == "hard_split_used" for i in issues)
        assert all(i.level == "warning" for i in issues)

    def test_offsets_cover_source_contiguously(self,
                                               docs_proc: DocProcessor):
        src = "# A\n\nAAA\n\n# B\n\nBBB\n\n# C\n\nCCC\n"
        chunks, _ = docs_proc.split(src, 20)
        for i in range(len(chunks) - 1):
            assert chunks[i].end == chunks[i + 1].start
        assert chunks[0].start == 0
        assert chunks[-1].end == len(src)

    def test_headers_in_fence_not_a_boundary(self,
                                              docs_proc_min100: DocProcessor):
        src = (
            "# Real header\n\n"
            "```\n"
            "# Not a header\n"
            "```\n\n"
            "# Real header 2\n\n"
            "content\n"
        )
        chunks, _ = docs_proc_min100.split(src, 40)
        joined = "".join(c.text for c in chunks)
        assert joined == src
        for c in chunks:
            if "# Not a header" in c.text:
                assert "# Real header" in c.text

    def test_single_header_no_split(self, docs_proc: DocProcessor):
        src = "# Only header\n\nfirst\n\nsecond\n\nthird\n"
        chunks, _ = docs_proc.split(src, 20)
        assert all(c.kind != "section" for c in chunks)

    def test_merge_small_chunks(self):
        proc = DocProcessor(
            DocsConfig(),
            DefaultsConfig(chunk_min_bytes=500),
        )
        src = "A\n\nB\n\nC"
        chunks, _ = proc.split(src, 10000)
        assert len(chunks) == 1

    def test_no_split_when_headers_absent_and_paragraphs_fit(self,
                                                              docs_proc: DocProcessor):
        src = "just one paragraph, no breaks"
        chunks, _ = docs_proc.split(src, 10000)
        assert len(chunks) == 1
        assert chunks[0].kind == "whole"


# ══════════════════════════════════════════════════════════════════════════
# DocProcessor: merge
# ══════════════════════════════════════════════════════════════════════════

class TestDocMerge:
    def test_length_mismatch_raises(self, docs_proc: DocProcessor):
        c = Chunk(index=1, total=1, text="x",
                  preamble="", start=0, end=1, kind="whole")
        with pytest.raises(ProcessError, match="mismatch"):
            docs_proc.merge("x", [c], [])

    def test_simple_concatenation(self, docs_proc: DocProcessor):
        src = "# A\n\naaa\n\n# B\n\nbbb\n"
        chunks = [
            Chunk(1, 2, "# A\n\naaa\n\n", "", 0, 10, "section"),
            Chunk(2, 2, "# B\n\nbbb\n",  "", 10, len(src), "section"),
        ]
        out = docs_proc.merge(src, chunks, ["# A\n\nAAA\n\n",
                                              "# B\n\nBBB\n"])
        assert out == "# A\n\nAAA\n\n# B\n\nBBB\n"

    def test_partial_none_keeps_original(self, docs_proc: DocProcessor):
        src = "# A\n\naaa\n\n# B\n\nbbb\n"
        chunks = [
            Chunk(1, 2, "# A\n\naaa\n\n", "", 0, 10, "section"),
            Chunk(2, 2, "# B\n\nbbb\n",  "", 10, len(src), "section"),
        ]
        out = docs_proc.merge(src, chunks, ["# A\n\nAAA\n\n", None])
        assert "# A\n\nAAA\n\n" in out
        assert "# B\n\nbbb\n" in out


# ══════════════════════════════════════════════════════════════════════════
# DocProcessor: validate
# ══════════════════════════════════════════════════════════════════════════

class TestDocValidate:
    def test_headers_preserved_ok(self, docs_proc: DocProcessor):
        src = "# A\n\ntext\n\n# B\n\ntext\n"
        merged = "# A\n\nNEW text\n\n# B\n\nNEW text\n"
        issues = docs_proc.validate(src, merged)
        assert not has_errors(issues)

    def test_headers_missing(self, docs_proc: DocProcessor):
        src = "# A\n\ntext\n\n# B\n\ntext\n"
        merged = "# A\n\ntext\n\ntext\n"
        issues = docs_proc.validate(src, merged)
        assert has_errors(issues)
        assert any(i.code == "headers_missing" for i in issues)

    def test_headers_added_is_warning(self, docs_proc: DocProcessor):
        src = "# A\n\ntext\n"
        merged = "# A\n\ntext\n\n## Subsection\n\nmore\n"
        issues = docs_proc.validate(src, merged)
        assert not has_errors(issues)
        assert any(i.code == "headers_added" for i in issues)

    def test_duplicate_header_lost_is_error(self, docs_proc: DocProcessor):
        src = "# Same\n\nx\n\n# Same\n\ny\n"
        merged = "# Same\n\nx\n\ny\n"
        issues = docs_proc.validate(src, merged)
        assert has_errors(issues)

    def test_header_inside_fence_ignored(self, docs_proc: DocProcessor):
        src = "# Real\n\n```\n# Not header\n```\n"
        merged = "# Real\n\n```\n# Not header CHANGED\n```\n"
        issues = docs_proc.validate(src, merged)
        assert not has_errors(issues)

    def test_fences_balanced_ok(self, docs_proc: DocProcessor):
        merged = "# Title\n\n```python\ncode\n```\n"
        issues = docs_proc.validate("", merged)
        assert not has_errors(issues)

    def test_fences_unbalanced(self, docs_proc: DocProcessor):
        merged = "# Title\n\n```python\ncode\n"
        issues = docs_proc.validate("", merged)
        assert has_errors(issues)
        assert any(i.code == "fences_unbalanced" for i in issues)

    def test_tilde_fences_unbalanced(self, docs_proc: DocProcessor):
        merged = "~~~\ncode\n"
        issues = docs_proc.validate("", merged)
        assert has_errors(issues)

    def test_mixed_fence_types_all_balanced(self, docs_proc: DocProcessor):
        merged = "```\na\n```\n\n~~~\nb\n~~~\n"
        issues = docs_proc.validate("", merged)
        assert not has_errors(issues)

    def test_validate_disabled_by_config(self):
        proc = DocProcessor(
            DocsConfig(validate=()),
            DefaultsConfig(),
        )
        merged = "# A\n\n```\ncode\n"
        issues = proc.validate("# A\n", merged)
        assert issues == []

    def test_only_headers_check(self):
        proc = DocProcessor(
            DocsConfig(validate=("headers_preserved",)),
            DefaultsConfig(),
        )
        merged = "```\nno close\n"
        issues = proc.validate("", merged)
        assert not any(i.code.startswith("fences") for i in issues)


# ══════════════════════════════════════════════════════════════════════════
# Интеграция: split → merge → validate
# ══════════════════════════════════════════════════════════════════════════

class TestDocPipeline:
    def test_roundtrip_identity(self, docs_proc: DocProcessor):
        src = "# A\n\nAAA\n\n# B\n\nBBB\n"
        chunks, _ = docs_proc.split(src, 20)
        outputs = [c.text for c in chunks]
        merged = docs_proc.merge(src, chunks, outputs)
        assert merged == src
        issues = docs_proc.validate(src, merged)
        assert not has_errors(issues)

    def test_roundtrip_with_modification(self, docs_proc: DocProcessor):
        src = "# A\n\nAAA\n\n# B\n\nBBB\n"
        chunks, _ = docs_proc.split(src, 20)
        outputs = [
            c.text.replace("AAA", "MODIFIED")
            if "AAA" in c.text else c.text
            for c in chunks
        ]
        merged = docs_proc.merge(src, chunks, outputs)
        assert "MODIFIED" in merged
        assert "# A" in merged and "# B" in merged
        issues = docs_proc.validate(src, merged)
        assert not has_errors(issues)
