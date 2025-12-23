"""Syntax validation using tree-sitter with sclang fallback.

This module provides a hybrid syntax validator for SuperCollider code:
1. Primary: tree-sitter (fast, ~5ms) - parses code without executing
2. Fallback: sclang compile() (accurate, ~200ms) - uses real SC parser

The tree-sitter grammar is from:
https://github.com/madskjeldgaard/tree-sitter-supercollider
"""

import logging
import platform
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Tree-sitter imports (optional - gracefully degrade if unavailable)
try:
    from tree_sitter import Language, Parser

    TREE_SITTER_AVAILABLE = True
except ImportError:
    TREE_SITTER_AVAILABLE = False
    Language = None  # type: ignore
    Parser = None  # type: ignore


def get_grammar_path() -> Path:
    """Get the path to the compiled grammar library."""
    grammars_dir = Path(__file__).parent / "grammars"

    system = platform.system()
    if system == "Darwin":
        filename = "supercollider.dylib"
    elif system == "Windows":
        filename = "supercollider.dll"
    else:  # Linux and others
        filename = "supercollider.so"

    return grammars_dir / filename


class SyntaxValidator:
    """Hybrid syntax validator: tree-sitter (fast) -> sclang (fallback)."""

    def __init__(self):
        self._parser: Optional["Parser"] = None
        self._language: Optional["Language"] = None
        self._backend: str = "none"
        self._init_tree_sitter()

    @property
    def backend(self) -> str:
        """Return the name of the active validation backend."""
        return self._backend

    def _init_tree_sitter(self) -> bool:
        """Initialize tree-sitter parser if available.

        Returns:
            True if tree-sitter was initialized successfully.
        """
        if not TREE_SITTER_AVAILABLE:
            logger.debug("tree-sitter not installed, using sclang fallback")
            self._backend = "sclang"
            return False

        grammar_path = get_grammar_path()
        if not grammar_path.exists():
            logger.warning(
                f"Grammar not found at {grammar_path}. "
                "Run 'python scripts/build_grammar.py' to build it."
            )
            self._backend = "sclang"
            return False

        try:
            self._language = Language(str(grammar_path), "supercollider")
            self._parser = Parser()
            self._parser.set_language(self._language)
            self._backend = "tree-sitter"
            logger.debug("tree-sitter initialized successfully")
            return True
        except Exception as e:
            logger.warning(f"Failed to initialize tree-sitter: {e}")
            self._backend = "sclang"
            return False

    def validate(self, code: str) -> tuple[bool, str, list[dict]]:
        """Validate SuperCollider code syntax.

        Args:
            code: SuperCollider code to validate.

        Returns:
            Tuple of (is_valid, message, errors) where errors is a list of
            dicts with 'line', 'column', and 'message' keys.
        """
        if not code or not code.strip():
            return True, "Empty code is valid", []

        if self._parser is not None:
            return self._validate_tree_sitter(code)
        return self._validate_sclang(code)

    def _validate_tree_sitter(self, code: str) -> tuple[bool, str, list[dict]]:
        """Fast validation using tree-sitter.

        Parses the code and checks for ERROR nodes in the syntax tree.
        """
        try:
            tree = self._parser.parse(bytes(code, "utf8"))
        except Exception as e:
            logger.error(f"tree-sitter parse failed: {e}")
            # Fall back to sclang if tree-sitter fails
            return self._validate_sclang(code)

        errors = []
        self._collect_errors(tree.root_node, code, errors)

        if errors:
            return False, f"Found {len(errors)} syntax error(s)", errors

        return True, "Syntax valid", []

    def _collect_errors(
        self, node, code: str, errors: list[dict], max_errors: int = 10
    ) -> None:
        """Recursively collect ERROR and MISSING nodes from the syntax tree."""
        if len(errors) >= max_errors:
            return

        if node.type == "ERROR" or node.is_missing:
            # Get the line and column (1-indexed for display)
            line = node.start_point[0] + 1
            column = node.start_point[1] + 1

            # Extract context around the error
            lines = code.split("\n")
            if 0 <= node.start_point[0] < len(lines):
                error_line = lines[node.start_point[0]]
                # Truncate long lines
                if len(error_line) > 60:
                    error_line = error_line[:60] + "..."
            else:
                error_line = ""

            if node.is_missing:
                message = f"Missing: {node.type}"
            else:
                message = f"Unexpected syntax near: {error_line.strip()}"

            errors.append(
                {
                    "line": line,
                    "column": column,
                    "message": message,
                }
            )

        # Recurse into children
        for child in node.children:
            self._collect_errors(child, code, errors, max_errors)

    def _validate_sclang(self, code: str) -> tuple[bool, str, list[dict]]:
        """Accurate validation using sclang compile().

        Uses sclang's interpreter.compile() to parse without executing.
        """
        from .sclang import validate_syntax_sclang

        return validate_syntax_sclang(code)


# Global validator instance (lazy initialization)
_validator: Optional[SyntaxValidator] = None


def get_validator() -> SyntaxValidator:
    """Get or create the global SyntaxValidator instance."""
    global _validator
    if _validator is None:
        _validator = SyntaxValidator()
    return _validator


def validate_syntax(code: str) -> tuple[bool, str, list[dict]]:
    """Convenience function to validate SuperCollider code syntax.

    Args:
        code: SuperCollider code to validate.

    Returns:
        Tuple of (is_valid, message, errors).
    """
    return get_validator().validate(code)
