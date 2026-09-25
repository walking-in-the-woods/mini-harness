"""Тесты harness.processing.code (CodeProcessor).

Без реальной модели. Покрывает:
  * _line_col_to_char_offset — конвертация координат AST
  * split — def/async/class, декораторы, preamble, ошибки
  * merge — replace_block, ornament, strip-preamble
  * validate — все пять проверок, отключение через конфиг
  * интеграция split → merge → validate
"""

from __future__ import annotations

import pytest

from harness.processing.base import (
    Chunk,
    ProcessError,
    has_errors,
)
from harness.processing.code import (
    CodeProcessor,
    _line_col_to_char_offset,
)
from harness.processing.config import CodeConfig, DefaultsConfig


# ══════════════════════════════════════════════════════════════════════════
# _line_col_to_char_offset
# ══════════════════════════════════════════════════════════════════════════

class TestLineColToCharOffset:
    def test_first_line_first_col(self):
        assert _line_col_to_char_offset("abc\ndef", 1, 0) == 0

    def test_first_line_middle(self):
        assert _line_col_to_char_offset("abcdef", 1, 3) == 3

    def test_second_line_col_zero(self):
        assert _line_col_to_char_offset("abc\ndef", 2, 0) == 4

    def test_second_line_middle(self):
        assert _line_col_to_char_offset("abc\ndef", 2, 1) == 5

    def test_cyrillic_line_end(self):
        assert _line_col_to_char_offset("Привет\nworld", 2, 0) == 7

    def test_cyrillic_col_offset_is_bytes(self):
        src = "Привет x"
        assert _line_col_to_char_offset(src, 1, 13) == 7

    def test_cyrillic_mixed_line(self):
        src = "a\nПривет\nb"
        assert _line_col_to_char_offset(src, 2, 0) == 2
        assert _line_col_to_char_offset(src, 2, 12) == 8

    def test_lineno_zero_raises(self):
        with pytest.raises(ProcessError, match="lineno"):
            _line_col_to_char_offset("abc", 0, 0)

    def test_lineno_out_of_range(self):
        with pytest.raises(ProcessError, match="out of range"):
            _line_col_to_char_offset("abc\ndef", 5, 0)

    def test_col_offset_too_large(self):
        with pytest.raises(ProcessError, match="col_offset"):
            _line_col_to_char_offset("abc", 1, 100)

    def test_col_offset_on_utf8_boundary_ok(self):
        with pytest.raises(ProcessError, match="UTF-8 boundary"):
            _line_col_to_char_offset("Привет", 1, 1)


# ══════════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def code_proc() -> CodeProcessor:
    """CodeProcessor с chunk_min_bytes=0.

    Для тестов не нужно склеивание мелких чанков.
    """
    return CodeProcessor(CodeConfig(), DefaultsConfig(chunk_min_bytes=0))


@pytest.fixture
def code_proc_preamble() -> CodeProcessor:
    return CodeProcessor(
        CodeConfig(include_preamble=True),
        DefaultsConfig(chunk_min_bytes=0),
    )


# ══════════════════════════════════════════════════════════════════════════
# split — базовые случаи
# ══════════════════════════════════════════════════════════════════════════

class TestCodeSplitBasics:
    def test_empty_raises(self, code_proc: CodeProcessor):
        with pytest.raises(ProcessError, match="empty source"):
            code_proc.split("", 10000)

    def test_whitespace_raises(self, code_proc: CodeProcessor):
        with pytest.raises(ProcessError, match="empty source"):
            code_proc.split("   \n\n", 10000)

    def test_budget_too_small_raises(self, code_proc: CodeProcessor):
        with pytest.raises(ProcessError, match="budget_bytes"):
            code_proc.split("def f(): pass", 5)

    def test_whole_when_fits(self, code_proc: CodeProcessor):
        src = "def f():\n    return 1\n"
        chunks, issues = code_proc.split(src, 10000)
        assert len(chunks) == 1
        assert chunks[0].kind == "whole"
        assert chunks[0].start == 0
        assert chunks[0].end == len(src)
        assert issues == []

    def test_not_python_raises(self, code_proc: CodeProcessor):
        with pytest.raises(ProcessError, match="does not parse"):
            code_proc.split("def f(  # broken\n" * 20, 100)


# ══════════════════════════════════════════════════════════════════════════
# split — разбиение по def / class
# ══════════════════════════════════════════════════════════════════════════

class TestCodeSplitBoundaries:
    def test_two_functions(self, code_proc: CodeProcessor):
        src = (
            "def add(a, b):\n"
            "    return a + b\n"
            "\n"
            "def multiply(a, b):\n"
            "    return a * b\n"
        )
        chunks, issues = code_proc.split(src, 50)
        assert len(chunks) == 2
        assert chunks[0].kind == "function"
        assert chunks[1].kind == "function"
        assert "def add" in chunks[0].text
        assert "def multiply" in chunks[1].text
        assert issues == []

    def test_async_def(self, code_proc: CodeProcessor):
        src = (
            "async def fetch(url):\n"
            "    return url\n"
            "\n"
            "async def parse(data):\n"
            "    return data\n"
        )
        chunks, _ = code_proc.split(src, 40)
        assert len(chunks) == 2
        assert all(c.kind == "function" for c in chunks)

    def test_class_as_boundary(self, code_proc: CodeProcessor):
        src = (
            "class Counter:\n"
            "    def __init__(self):\n"
            "        self.value = 0\n"
            "\n"
            "def standalone():\n"
            "    return 1\n"
        )
        chunks, _ = code_proc.split(src, 70)
        assert len(chunks) == 2
        assert chunks[0].kind == "class"
        assert chunks[1].kind == "function"

    def test_decorators_included(self, code_proc: CodeProcessor):
        src = (
            "@property\n"
            "def value(self):\n"
            "    return self._v\n"
            "\n"
            "@staticmethod\n"
            "def helper():\n"
            "    return 42\n"
        )
        chunks, _ = code_proc.split(src, 50)
        assert len(chunks) == 2
        assert chunks[0].text.startswith("@property")
        assert chunks[1].text.startswith("@staticmethod")

    def test_imports_not_chunks(self, code_proc: CodeProcessor):
        src = (
            "import re\n"
            "from typing import Optional\n"
            "\n"
            "def f(x):\n"
            "    return x\n"
            "\n"
            "def g(y):\n"
            "    return y\n"
        )
        chunks, _ = code_proc.split(src, 30)
        assert len(chunks) == 2
        for c in chunks:
            assert "import re" not in c.text
        assert chunks[0].text.startswith("def f")

    def test_no_boundaries_raises(self, code_proc: CodeProcessor):
        src = "import os\n" * 100
        with pytest.raises(ProcessError, match="no top-level"):
            code_proc.split(src, 50)

    def test_single_def_over_budget_raises(self, code_proc: CodeProcessor):
        body = "    x = 1\n" * 100
        src = f"def huge():\n{body}"
        with pytest.raises(ProcessError, match="exceeds budget"):
            code_proc.split(src, 200)

    def test_class_over_budget_raises(self, code_proc: CodeProcessor):
        body = "    x = 1\n" * 100
        src = f"class Huge:\n{body}"
        with pytest.raises(ProcessError, match="exceeds budget"):
            code_proc.split(src, 200)

    def test_offsets_contiguous_for_chunks(self, code_proc: CodeProcessor):
        src = (
            "def a():\n    return 1\n"
            "\n"
            "def b():\n    return 2\n"
            "\n"
            "def c():\n    return 3\n"
        )
        chunks, _ = code_proc.split(src, 25)
        for c in chunks:
            assert c.start < c.end
        for i in range(len(chunks) - 1):
            assert chunks[i].end <= chunks[i + 1].start
        for c in chunks:
            assert src[c.start:c.end] == c.text

    def test_total_matches_len(self, code_proc: CodeProcessor):
        src = "def a(): pass\ndef b(): pass\ndef c(): pass\n"
        chunks, _ = code_proc.split(src, 20)
        for c in chunks:
            assert c.total == len(chunks)


# ══════════════════════════════════════════════════════════════════════════
# split — preamble
# ══════════════════════════════════════════════════════════════════════════

class TestCodeSplitPreamble:
    def test_no_preamble_by_default(self, code_proc: CodeProcessor):
        src = (
            "import re\n"
            "\n"
            "def f():\n    return 1\n"
            "\n"
            "def g():\n    return 2\n"
        )
        chunks, _ = code_proc.split(src, 30)
        for c in chunks:
            assert c.preamble == ""

    def test_include_preamble(self, code_proc_preamble: CodeProcessor):
        """Preamble попадает в каждый chunk при разбиении.

        Файл должен не влезать в бюджет целиком (иначе split вернёт
        one whole chunk без preamble), при этом preamble + отдельный
        chunk должны влезать. Обёртка preamble весит ~100 байт, чанк
        одной функции ~20 байт. Берём длинный файл из многих мелких
        функций.
        """
        funcs = "\n\n".join(
            f"def f{i}():\n    return {i}" for i in range(10)
        )
        src = (
            "import re\n"
            "from typing import Optional\n"
            "\n"
            f"{funcs}\n"
        )
        # Файл ~270 байт, бюджет 200 — не влезает. Chunk ~23 байта,
        # preamble ~123 байта → 146 < 200.
        chunks, issues = code_proc_preamble.split(src, 200)
        assert len(chunks) >= 5
        for c in chunks:
            assert "import re" in c.preamble
            assert "from typing import Optional" in c.preamble
            assert "read-only" in c.preamble
        assert any(
            i.code == "preamble_included" and i.level == "info"
            for i in issues
        )

    def test_preamble_empty_when_no_header(self,
                                            code_proc_preamble: CodeProcessor):
        src = "def f(): pass\ndef g(): pass\n"
        chunks, issues = code_proc_preamble.split(src, 20)
        for c in chunks:
            assert c.preamble == ""
        assert not any(i.code == "preamble_included" for i in issues)

    def test_preamble_too_large_raises(self):
        proc = CodeProcessor(
            CodeConfig(include_preamble=True,
                       preamble_max_bytes=100),
            DefaultsConfig(chunk_min_bytes=0),
        )
        src = (
            "import very_long_module_name_" + "x" * 200 + "\n"
            "\n"
            "def f(): return 1\n"
            "\n"
            "def g(): return 2\n"
        )
        with pytest.raises(ProcessError, match="preamble"):
            proc.split(src, 50)

    def test_preamble_consumes_budget(self):
        proc = CodeProcessor(
            CodeConfig(include_preamble=True),
            DefaultsConfig(chunk_min_bytes=0),
        )
        src = (
            "import re\n"
            "\n"
            "def f():\n    return 1\n"
            "\n"
            "def g():\n    return 2\n"
        )
        with pytest.raises(ProcessError, match="exceeds budget"):
            proc.split(src, 40)


# ══════════════════════════════════════════════════════════════════════════
# merge
# ══════════════════════════════════════════════════════════════════════════

class TestCodeMerge:
    def test_length_mismatch_raises(self, code_proc: CodeProcessor):
        c = Chunk(1, 1, "x", "", 0, 1, "whole")
        with pytest.raises(ProcessError, match="mismatch"):
            code_proc.merge("x", [c], [])

    def test_identity_roundtrip(self, code_proc: CodeProcessor):
        src = (
            "import re\n"
            "\n"
            "def add(a, b):\n"
            "    return a + b\n"
            "\n"
            "def multiply(a, b):\n"
            "    return a * b\n"
        )
        chunks, _ = code_proc.split(src, 50)
        outputs = [c.text for c in chunks]
        merged = code_proc.merge(src, chunks, outputs)
        assert merged == src

    def test_replace_block_modification(self, code_proc: CodeProcessor):
        src = (
            "def add(a, b):\n"
            "    return a + b\n"
            "\n"
            "def multiply(a, b):\n"
            "    return a * b\n"
        )
        chunks, _ = code_proc.split(src, 40)
        outputs = []
        for c in chunks:
            if "def add" in c.text:
                outputs.append(
                    "def add(a, b):\n"
                    '    """Adds two numbers."""\n'
                    "    return a + b\n"
                )
            else:
                outputs.append(c.text)
        merged = code_proc.merge(src, chunks, outputs)
        assert '"""Adds two numbers."""' in merged
        assert "def multiply(a, b):\n    return a * b" in merged
        assert merged.count("def ") == 2

    def test_none_output_uses_ornament(self, code_proc: CodeProcessor):
        src = (
            "def add(a, b):\n"
            "    return a + b\n"
            "\n"
            "def multiply(a, b):\n"
            "    return a * b\n"
        )
        chunks, _ = code_proc.split(src, 40)
        outputs = [c.text if "add" in c.text else None for c in chunks]
        merged = code_proc.merge(src, chunks, outputs)
        assert "def add(a, b):\n    return a + b" in merged
        assert "NOT PROCESSED" in merged
        assert "chunk 2/2" in merged
        assert "END NOT PROCESSED" in merged
        assert "def multiply(a, b):\n    return a * b" in merged

    def test_strip_preamble_helper(self, code_proc_preamble: CodeProcessor):
        """_strip_preamble снимает копию preamble с начала output.

        Проверяем helper напрямую: интеграционный тест через split
        невозможен, потому что preamble (~100 байт с обёрткой)
        не оставляет места чанку в бюджете короткого файла.
        """
        preamble = code_proc_preamble._format_preamble(
            "import re\n"
        )
        # Модель скопировала preamble в выход.
        output = preamble + "def f():\n    return 1\n"
        stripped = code_proc_preamble._strip_preamble(output, preamble)
        assert stripped == "def f():\n    return 1\n"
        assert "import re" not in stripped

    def test_strip_preamble_no_preamble_returns_unchanged(
        self, code_proc_preamble: CodeProcessor,
    ):
        """Если preamble пуст — output не трогаем."""
        output = "def f():\n    return 1\n"
        stripped = code_proc_preamble._strip_preamble(output, "")
        assert stripped == output

    def test_strip_preamble_not_copied_returns_unchanged(
        self, code_proc_preamble: CodeProcessor,
    ):
        """Если модель не копировала preamble — output не трогаем."""
        preamble = code_proc_preamble._format_preamble("import re\n")
        output = "def f():\n    return 1\n"
        stripped = code_proc_preamble._strip_preamble(output, preamble)
        assert stripped == output

    def test_overlap_detected(self, code_proc: CodeProcessor):
        src = "def a(): pass\ndef b(): pass\n"
        chunks = [
            Chunk(1, 2, "def a(): pass\n", "", 0, 14, "function"),
            Chunk(2, 2, "pass\n",          "", 10, 20, "function"),
        ]
        with pytest.raises(ProcessError, match="overlap"):
            code_proc.merge(src, chunks, ["a", "b"])


# ══════════════════════════════════════════════════════════════════════════
# validate — ast_parseable
# ══════════════════════════════════════════════════════════════════════════

class TestCodeValidateAst:
    def test_valid(self, code_proc: CodeProcessor):
        src = "def f():\n    return 1\n"
        issues = code_proc.validate(src, src)
        assert not has_errors(issues)

    def test_merged_invalid(self, code_proc: CodeProcessor):
        src = "def f():\n    return 1\n"
        merged = "def f(\n    return 1\n"
        issues = code_proc.validate(src, merged)
        assert has_errors(issues)
        assert any(i.code == "merged_invalid" for i in issues)

    def test_source_invalid(self, code_proc: CodeProcessor):
        issues = code_proc.validate("def f(", "def f(")
        assert has_errors(issues)
        assert any(i.code == "source_invalid" for i in issues)

    def test_ast_parseable_disabled(self):
        proc = CodeProcessor(
            CodeConfig(validate=("defs_preserved",)),
            DefaultsConfig(),
        )
        merged = "def f(\n"
        issues = proc.validate("def f():\n    pass\n", merged)
        assert issues == []


# ══════════════════════════════════════════════════════════════════════════
# validate — defs_preserved
# ══════════════════════════════════════════════════════════════════════════

class TestCodeValidateDefs:
    def test_defs_preserved(self, code_proc: CodeProcessor):
        src = "def a(): pass\ndef b(): pass\n"
        merged = "def a(): pass\ndef b(): pass\n"
        issues = code_proc.validate(src, merged)
        assert not has_errors(issues)

    def test_defs_lost(self, code_proc: CodeProcessor):
        src = "def a(): pass\ndef b(): pass\ndef c(): pass\n"
        merged = "def a(): pass\ndef c(): pass\n"
        issues = code_proc.validate(src, merged)
        assert has_errors(issues)
        assert any(i.code == "defs_lost" for i in issues)
        b_issues = [i for i in issues if i.code == "defs_lost"]
        assert "'b'" in b_issues[0].message

    def test_class_preserved(self, code_proc: CodeProcessor):
        src = "class A: pass\ndef f(): pass\n"
        merged = "class A: pass\ndef f(): pass\n"
        issues = code_proc.validate(src, merged)
        assert not has_errors(issues)


# ══════════════════════════════════════════════════════════════════════════
# validate — bodies_nonempty
# ══════════════════════════════════════════════════════════════════════════

class TestCodeValidateBodies:
    def test_bodies_ok(self, code_proc: CodeProcessor):
        src = "def f():\n    return 1\n"
        merged = "def f():\n    return 1\n"
        issues = code_proc.validate(src, merged)
        assert not has_errors(issues)

    def test_body_lost(self, code_proc: CodeProcessor):
        src = "def f():\n    return 1\n"
        merged = 'def f():\n    """Only docstring."""\n'
        issues = code_proc.validate(src, merged)
        assert has_errors(issues)
        assert any(i.code == "body_lost" for i in issues)

    def test_body_only_docstring_in_source_ignored(
        self, code_proc: CodeProcessor,
    ):
        src = 'def f():\n    """Docstring."""\n'
        merged = 'def f():\n    """New docstring."""\n'
        issues = code_proc.validate(src, merged)
        assert not has_errors(issues)

    def test_body_lost_message_mentions_name(
        self, code_proc: CodeProcessor,
    ):
        src = "def helper():\n    return 42\n"
        merged = 'def helper():\n    """doc"""\n'
        issues = code_proc.validate(src, merged)
        body_issues = [i for i in issues if i.code == "body_lost"]
        assert "'helper'" in body_issues[0].message


# ══════════════════════════════════════════════════════════════════════════
# validate — signature_args_preserved
# ══════════════════════════════════════════════════════════════════════════

class TestCodeValidateSignature:
    def test_signature_ok(self, code_proc: CodeProcessor):
        src = "def f(a, b): return a + b\n"
        merged = "def f(a, b): return a + b\n"
        assert not has_errors(code_proc.validate(src, merged))

    def test_signature_args_removed(self, code_proc: CodeProcessor):
        src = "def f(a, b): return a + b\n"
        merged = "def f(a): return a\n"
        issues = code_proc.validate(src, merged)
        assert has_errors(issues)
        assert any(i.code == "signature_changed" for i in issues)

    def test_signature_args_added(self, code_proc: CodeProcessor):
        src = "def f(a, b): return a + b\n"
        merged = "def f(a, b, c=None): return a + b\n"
        issues = code_proc.validate(src, merged)
        assert has_errors(issues)

    def test_signature_type_hints_ok(self, code_proc: CodeProcessor):
        src = "def add(a, b):\n    return a + b\n"
        merged = (
            "def add(a: int, b: int) -> int:\n"
            "    return a + b\n"
        )
        issues = code_proc.validate(src, merged)
        assert not has_errors(issues)

    def test_signature_defaults_ok(self, code_proc: CodeProcessor):
        src = "def f(a, b=1): return a + b\n"
        merged = "def f(a, b=2): return a + b\n"
        issues = code_proc.validate(src, merged)
        assert not has_errors(issues)

    def test_varargs_preserved(self, code_proc: CodeProcessor):
        src = "def f(*args, **kwargs): pass\n"
        merged = "def f(*args, **kwargs): pass\n"
        assert not has_errors(code_proc.validate(src, merged))

    def test_varargs_lost(self, code_proc: CodeProcessor):
        src = "def f(*args, **kwargs): return None\n"
        merged = "def f(*args): return None\n"
        issues = code_proc.validate(src, merged)
        assert has_errors(issues)

    def test_kwonly_preserved(self, code_proc: CodeProcessor):
        src = "def f(a, *, b=1): return a + b\n"
        merged = "def f(a, *, b=1): return a + b\n"
        assert not has_errors(code_proc.validate(src, merged))

    def test_class_skipped_by_signature_check(
        self, code_proc: CodeProcessor,
    ):
        src = "class A: pass\n"
        merged = "class A: pass\n"
        assert not has_errors(code_proc.validate(src, merged))


# ══════════════════════════════════════════════════════════════════════════
# validate — no_new_top_level
# ══════════════════════════════════════════════════════════════════════════

class TestCodeValidateNoNewTopLevel:
    def test_no_new(self, code_proc: CodeProcessor):
        src = "def a(): pass\n"
        merged = "def a(): pass\n"
        issues = code_proc.validate(src, merged)
        assert not has_errors(issues)
        assert not any(i.code == "new_top_level" for i in issues)

    def test_new_top_level_warning(self):
        proc = CodeProcessor(
            CodeConfig(validate=("ast_parseable", "no_new_top_level")),
            DefaultsConfig(),
        )
        src = "def a(): pass\n"
        merged = "def a(): pass\ndef helper(): pass\n"
        issues = proc.validate(src, merged)
        assert not has_errors(issues)
        assert any(i.code == "new_top_level" for i in issues)
        warn = [i for i in issues if i.code == "new_top_level"][0]
        assert warn.level == "warning"

    def test_new_top_level_disabled_by_default(
        self, code_proc: CodeProcessor,
    ):
        src = "def a(): pass\n"
        merged = "def a(): pass\ndef helper(): pass\n"
        issues = code_proc.validate(src, merged)
        assert not any(i.code == "new_top_level" for i in issues)


# ══════════════════════════════════════════════════════════════════════════
# Интеграция split → merge → validate
# ══════════════════════════════════════════════════════════════════════════

class TestCodePipeline:
    def test_identity_roundtrip(self, code_proc: CodeProcessor):
        src = (
            "import re\n"
            "\n"
            "def add(a, b):\n"
            "    return a + b\n"
            "\n"
            "class Counter:\n"
            "    def __init__(self):\n"
            "        self.v = 0\n"
        )
        chunks, _ = code_proc.split(src, 70)
        outputs = [c.text for c in chunks]
        merged = code_proc.merge(src, chunks, outputs)
        assert merged == src
        issues = code_proc.validate(src, merged)
        assert not has_errors(issues)

    def test_docstrings_added_roundtrip(self, code_proc: CodeProcessor):
        src = (
            "def add(a, b):\n"
            "    return a + b\n"
            "\n"
            "def multiply(a, b):\n"
            "    return a * b\n"
        )
        chunks, _ = code_proc.split(src, 40)

        def add_docstring(text: str) -> str:
            lines = text.splitlines(keepends=True)
            def_idx = next(
                i for i, l in enumerate(lines)
                if l.strip().startswith("def ")
            )
            lines.insert(
                def_idx + 1,
                '    """Google-style docstring."""\n',
            )
            return "".join(lines)

        outputs = [add_docstring(c.text) for c in chunks]
        merged = code_proc.merge(src, chunks, outputs)
        assert merged.count('"""Google-style docstring."""') == 2
        issues = code_proc.validate(src, merged)
        assert not has_errors(issues)

    def test_partial_mode_roundtrip(self, code_proc: CodeProcessor):
        src = (
            "def add(a, b):\n"
            "    return a + b\n"
            "\n"
            "def multiply(a, b):\n"
            "    return a * b\n"
        )
        chunks, _ = code_proc.split(src, 40)
        outputs: list[str | None] = []
        for c in chunks:
            if "add" in c.text:
                outputs.append(
                    "def add(a, b):\n"
                    '    """Adds."""\n'
                    "    return a + b\n"
                )
            else:
                outputs.append(None)
        merged = code_proc.merge(src, chunks, outputs)
        assert '"""Adds."""' in merged
        assert "NOT PROCESSED" in merged
        assert "return a * b" in merged
        issues = code_proc.validate(src, merged)
        assert not has_errors(issues), issues
