"""Tests for syntax validation."""

import pytest
from unittest.mock import Mock, patch

from sc_repl_mcp.syntax import (
    SyntaxValidator,
    get_validator,
    validate_syntax,
    get_grammar_path,
    TREE_SITTER_AVAILABLE,
)
from sc_repl_mcp.sclang import (
    escape_for_sc_string,
    parse_sclang_errors,
    validate_syntax_sclang,
)


class TestEscapeForScString:
    """Tests for escape_for_sc_string function."""

    def test_escapes_backslashes(self):
        assert escape_for_sc_string("a\\b") == "a\\\\b"

    def test_escapes_double_quotes(self):
        assert escape_for_sc_string('say "hello"') == 'say \\"hello\\"'

    def test_escapes_newlines(self):
        assert escape_for_sc_string("line1\nline2") == "line1\\nline2"

    def test_escapes_carriage_returns(self):
        assert escape_for_sc_string("line1\rline2") == "line1\\rline2"

    def test_escapes_tabs(self):
        assert escape_for_sc_string("col1\tcol2") == "col1\\tcol2"

    def test_escapes_mixed(self):
        code = 'var x = "hello\\nworld";'
        expected = 'var x = \\"hello\\\\nworld\\";'
        assert escape_for_sc_string(code) == expected

    def test_empty_string(self):
        assert escape_for_sc_string("") == ""

    def test_no_escaping_needed(self):
        assert escape_for_sc_string("SinOsc.ar(440)") == "SinOsc.ar(440)"

    def test_removes_null_bytes(self):
        """Null bytes should be removed as they can't be in SC strings."""
        assert escape_for_sc_string("a\0b") == "ab"
        assert escape_for_sc_string("\0start") == "start"
        assert escape_for_sc_string("end\0") == "end"


class TestParseSclangErrors:
    """Tests for parse_sclang_errors function."""

    def test_parses_error_message(self):
        output = "ERROR: syntax error, unexpected BINOP"
        errors = parse_sclang_errors(output)
        assert len(errors) == 1
        assert "syntax error" in errors[0]["message"].lower()

    def test_parses_line_number(self):
        output = "ERROR: syntax error at line 5"
        errors = parse_sclang_errors(output)
        assert len(errors) == 1
        assert errors[0]["line"] == 5

    def test_handles_no_line_number(self):
        output = "ERROR: unknown error"
        errors = parse_sclang_errors(output)
        assert len(errors) == 1
        assert errors[0]["line"] == 1  # Default to line 1

    def test_parses_parse_error(self):
        output = "Parse error in interpreted code: unexpected token"
        errors = parse_sclang_errors(output)
        assert len(errors) == 1
        assert "unexpected token" in errors[0]["message"]

    def test_empty_output(self):
        errors = parse_sclang_errors("")
        assert errors == []

    def test_filters_noise(self):
        output = "compiling class library\nERROR: actual error"
        errors = parse_sclang_errors(output)
        assert len(errors) == 1
        assert "actual error" in errors[0]["message"]


class TestSyntaxValidator:
    """Tests for SyntaxValidator class."""

    @pytest.fixture
    def validator(self):
        """Create a SyntaxValidator instance."""
        return SyntaxValidator()

    def test_backend_property(self, validator):
        """Validator should report its backend."""
        assert validator.backend in ["tree-sitter", "sclang", "none"]

    def test_empty_code_is_valid(self, validator):
        """Empty code should be considered valid."""
        is_valid, message, errors = validator.validate("")
        assert is_valid
        assert errors == []

    def test_whitespace_only_is_valid(self, validator):
        """Whitespace-only code should be considered valid."""
        is_valid, message, errors = validator.validate("   \n\t  ")
        assert is_valid
        assert errors == []


class TestTreeSitterValidation:
    """Tests for tree-sitter validation (if available)."""

    @pytest.fixture
    def tree_sitter_validator(self):
        """Create a validator with tree-sitter if available."""
        validator = SyntaxValidator()
        if validator.backend != "tree-sitter":
            pytest.skip("tree-sitter not available")
        return validator

    def test_valid_simple_expression(self, tree_sitter_validator):
        """Simple valid expression should pass."""
        # Note: SC requires semicolons at end of statements
        is_valid, msg, errors = tree_sitter_validator.validate("SinOsc.ar(440);")
        assert is_valid
        assert errors == []

    def test_valid_synthdef(self, tree_sitter_validator):
        """Valid SynthDef should pass."""
        code = """
        SynthDef(\\test, {
            var sig = SinOsc.ar(440);
            Out.ar(0, sig);
        });
        """
        is_valid, msg, errors = tree_sitter_validator.validate(code)
        assert is_valid

    def test_valid_block(self, tree_sitter_validator):
        """Valid block should pass."""
        is_valid, msg, errors = tree_sitter_validator.validate("{ |x| x * 2 };")
        assert is_valid

    def test_invalid_mismatched_brackets(self, tree_sitter_validator):
        """Mismatched brackets should fail."""
        is_valid, msg, errors = tree_sitter_validator.validate("{ SinOsc.ar(440 }")
        assert not is_valid
        assert len(errors) > 0

    def test_invalid_unclosed_paren(self, tree_sitter_validator):
        """Unclosed parenthesis should fail."""
        is_valid, msg, errors = tree_sitter_validator.validate("SinOsc.ar(440")
        assert not is_valid

    def test_invalid_unclosed_string(self, tree_sitter_validator):
        """Unclosed string should fail."""
        is_valid, msg, errors = tree_sitter_validator.validate('"hello')
        assert not is_valid

    def test_error_has_line_number(self, tree_sitter_validator):
        """Errors should include line numbers."""
        code = """line1;
line2;
{ unclosed"""
        is_valid, msg, errors = tree_sitter_validator.validate(code)
        assert not is_valid
        assert len(errors) > 0
        # Error should be on line 3
        assert any(e["line"] == 3 for e in errors)

    def test_multiple_errors(self, tree_sitter_validator):
        """Multiple errors should all be reported."""
        code = "{ ( }"  # Multiple bracket issues
        is_valid, msg, errors = tree_sitter_validator.validate(code)
        assert not is_valid
        assert len(errors) >= 1


class TestSclangFallback:
    """Tests for sclang fallback validation."""

    def test_validate_syntax_sclang_mocked(self, mocker):
        """Test sclang validation with mocked eval_sclang."""
        mock_eval = mocker.patch(
            "sc_repl_mcp.sclang.eval_sclang",
            return_value=(True, "SYNTAX_OK"),
        )

        is_valid, msg, errors = validate_syntax_sclang("SinOsc.ar(440)")

        assert is_valid
        assert errors == []
        mock_eval.assert_called_once()

    def test_validate_syntax_sclang_error_mocked(self, mocker):
        """Test sclang validation error with mocked eval_sclang."""
        mocker.patch(
            "sc_repl_mcp.sclang.eval_sclang",
            return_value=(True, "ERROR: syntax error, unexpected BINOP"),
        )

        is_valid, msg, errors = validate_syntax_sclang("{ broken")

        assert not is_valid
        assert len(errors) > 0

    def test_validate_syntax_sclang_timeout_mocked(self, mocker):
        """Test sclang validation timeout with mocked eval_sclang."""
        mocker.patch(
            "sc_repl_mcp.sclang.eval_sclang",
            return_value=(False, "sclang execution timed out after 10s"),
        )

        is_valid, msg, errors = validate_syntax_sclang("{ very long code }")

        assert not is_valid
        # Should have at least one error
        assert len(errors) >= 1


class TestGetValidator:
    """Tests for get_validator singleton function."""

    def test_returns_validator(self):
        """get_validator should return a SyntaxValidator."""
        validator = get_validator()
        assert isinstance(validator, SyntaxValidator)

    def test_returns_same_instance(self):
        """get_validator should return the same instance."""
        v1 = get_validator()
        v2 = get_validator()
        assert v1 is v2


class TestValidateSyntaxConvenience:
    """Tests for validate_syntax convenience function."""

    def test_returns_tuple(self):
        """validate_syntax should return a 3-tuple."""
        result = validate_syntax("")
        assert isinstance(result, tuple)
        assert len(result) == 3

    def test_valid_code(self):
        """Valid code should pass."""
        # Note: SC requires semicolons at end of statements
        is_valid, msg, errors = validate_syntax("440;")
        assert is_valid


class TestGrammarPath:
    """Tests for grammar path resolution."""

    def test_grammar_path_is_path(self):
        """get_grammar_path should return a Path."""
        from pathlib import Path
        path = get_grammar_path()
        assert isinstance(path, Path)

    def test_grammar_path_has_correct_extension(self):
        """Grammar path should have platform-appropriate extension."""
        import platform
        path = get_grammar_path()
        system = platform.system()

        if system == "Darwin":
            assert path.suffix == ".dylib"
        elif system == "Windows":
            assert path.suffix == ".dll"
        else:
            assert path.suffix == ".so"


class TestScValidateSyntaxTool:
    """Tests for sc_validate_syntax MCP tool."""

    def test_valid_code_returns_success_message(self, mocker):
        """Tool should return success message for valid code."""
        from sc_repl_mcp.tools import sc_validate_syntax

        # Mock the validator (mock where it's defined, not where imported)
        mock_validator = mocker.Mock()
        mock_validator.validate.return_value = (True, "Syntax valid", [])
        mock_validator.backend = "tree-sitter"
        mock_validator.fallback_reason = None
        mocker.patch("sc_repl_mcp.syntax.get_validator", return_value=mock_validator)

        result = sc_validate_syntax("SinOsc.ar(440);")

        assert "Syntax valid" in result
        assert "tree-sitter" in result

    def test_invalid_code_shows_errors_with_line_numbers(self, mocker):
        """Tool should format errors with line numbers."""
        from sc_repl_mcp.tools import sc_validate_syntax

        mock_validator = mocker.Mock()
        mock_validator.validate.return_value = (
            False,
            "Found 1 syntax error(s)",
            [{"line": 3, "column": 5, "message": "Unexpected syntax"}],
        )
        mock_validator.backend = "tree-sitter"
        mock_validator.fallback_reason = None
        mocker.patch("sc_repl_mcp.syntax.get_validator", return_value=mock_validator)

        result = sc_validate_syntax("broken code")

        assert "Syntax errors found" in result
        assert "Line 3" in result
        assert "col 5" in result
        assert "Unexpected syntax" in result

    def test_shows_fallback_reason_when_using_sclang(self, mocker):
        """Tool should show why sclang is being used."""
        from sc_repl_mcp.tools import sc_validate_syntax

        mock_validator = mocker.Mock()
        mock_validator.validate.return_value = (True, "Syntax valid", [])
        mock_validator.backend = "sclang"
        mock_validator.fallback_reason = "tree-sitter not installed"
        mocker.patch("sc_repl_mcp.syntax.get_validator", return_value=mock_validator)

        result = sc_validate_syntax("SinOsc.ar(440);")

        assert "sclang" in result
        assert "tree-sitter not installed" in result

    def test_handles_sclang_not_found(self, mocker):
        """Tool should show helpful message when sclang not found."""
        from sc_repl_mcp.tools import sc_validate_syntax

        mock_validator = mocker.Mock()
        mock_validator.validate.return_value = (
            False,
            "sclang unavailable",
            [{"line": 1, "column": 1, "message": "sclang not found - install SuperCollider"}],
        )
        mock_validator.backend = "sclang"
        mock_validator.fallback_reason = None
        mocker.patch("sc_repl_mcp.syntax.get_validator", return_value=mock_validator)

        result = sc_validate_syntax("SinOsc.ar(440);")

        assert "Cannot validate" in result
        assert "sclang not installed" in result

    def test_handles_timeout(self, mocker):
        """Tool should show helpful message on timeout."""
        from sc_repl_mcp.tools import sc_validate_syntax

        mock_validator = mocker.Mock()
        mock_validator.validate.return_value = (
            False,
            "Validation timed out",
            [{"line": 1, "column": 1, "message": "sclang timed out after 10s - code may be valid"}],
        )
        mock_validator.backend = "sclang"
        mock_validator.fallback_reason = None
        mocker.patch("sc_repl_mcp.syntax.get_validator", return_value=mock_validator)

        result = sc_validate_syntax("long code")

        assert "timed out" in result.lower()
        assert "may be valid" in result

    def test_column_only_shown_when_greater_than_1(self, mocker):
        """Tool should not show column when it's 1."""
        from sc_repl_mcp.tools import sc_validate_syntax

        mock_validator = mocker.Mock()
        mock_validator.validate.return_value = (
            False,
            "Found 1 syntax error(s)",
            [{"line": 5, "column": 1, "message": "Error"}],
        )
        mock_validator.backend = "tree-sitter"
        mock_validator.fallback_reason = None
        mocker.patch("sc_repl_mcp.syntax.get_validator", return_value=mock_validator)

        result = sc_validate_syntax("broken")

        assert "Line 5" in result
        assert "col" not in result  # Column 1 should not be shown


class TestValidateSyntaxSclangEdgeCases:
    """Edge case tests for validate_syntax_sclang."""

    def test_empty_code_returns_valid(self):
        """Empty code should return valid without calling sclang."""
        is_valid, msg, errors = validate_syntax_sclang("")
        assert is_valid
        assert errors == []

    def test_whitespace_only_returns_valid(self):
        """Whitespace-only code should return valid."""
        is_valid, msg, errors = validate_syntax_sclang("   \n\t  ")
        assert is_valid
        assert errors == []

    def test_timeout_returns_specific_error(self, mocker):
        """Timeout should return specific error, not generic syntax error."""
        mocker.patch(
            "sc_repl_mcp.sclang.eval_sclang",
            return_value=(False, "sclang execution timed out after 10s"),
        )

        is_valid, msg, errors = validate_syntax_sclang("long code")

        assert not is_valid
        assert "timed out" in msg.lower()
        assert "may be valid" in errors[0]["message"]

    def test_sclang_not_found_returns_specific_error(self, mocker):
        """sclang not found should return specific error."""
        mocker.patch(
            "sc_repl_mcp.sclang.eval_sclang",
            return_value=(False, "sclang not found. Make sure SuperCollider is installed"),
        )

        is_valid, msg, errors = validate_syntax_sclang("code")

        assert not is_valid
        assert "unavailable" in msg.lower()
        assert "install" in errors[0]["message"].lower()


class TestSyntaxValidatorFallbackReason:
    """Tests for fallback_reason property."""

    def test_fallback_reason_none_when_tree_sitter_active(self):
        """fallback_reason should be None when tree-sitter is active."""
        validator = SyntaxValidator()
        if validator.backend == "tree-sitter":
            assert validator.fallback_reason is None
        else:
            pytest.skip("tree-sitter not available")

    def test_fallback_reason_set_when_sclang(self):
        """fallback_reason should explain why sclang is being used."""
        validator = SyntaxValidator()
        if validator.backend == "sclang":
            assert validator.fallback_reason is not None
            assert len(validator.fallback_reason) > 0
