#!/usr/bin/env python3
"""Build tree-sitter-supercollider grammar.

This script clones the tree-sitter-supercollider grammar from GitHub
and compiles it into a shared library for use with py-tree-sitter.

Usage:
    python scripts/build_grammar.py

Requirements:
    - tree-sitter>=0.21.0,<0.22.0 (for Language.build_library)
    - git (for cloning the grammar repository)
    - C compiler (gcc/clang)
"""

import platform
import subprocess
import sys
import tempfile
from pathlib import Path

GRAMMAR_REPO = "https://github.com/madskjeldgaard/tree-sitter-supercollider.git"
OUTPUT_DIR = Path(__file__).parent.parent / "sc_repl_mcp" / "grammars"


def get_library_filename() -> str:
    """Get the appropriate library filename for the current platform."""
    system = platform.system()
    if system == "Darwin":
        return "supercollider.dylib"
    elif system == "Windows":
        return "supercollider.dll"
    else:  # Linux and others
        return "supercollider.so"


def build() -> bool:
    """Build the SuperCollider grammar.

    Returns:
        True if successful, False otherwise.
    """
    try:
        from tree_sitter import Language
    except ImportError:
        print("Error: tree-sitter not installed. Run: pip install 'tree-sitter>=0.21.0,<0.22.0'")
        return False

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / get_library_filename()

    print(f"Building SuperCollider grammar...")
    print(f"  Repository: {GRAMMAR_REPO}")
    print(f"  Output: {output_path}")

    with tempfile.TemporaryDirectory() as tmpdir:
        # Clone grammar repo
        print("  Cloning grammar repository...")
        result = subprocess.run(
            ["git", "clone", "--depth=1", GRAMMAR_REPO, tmpdir],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"Error cloning repository: {result.stderr}")
            return False

        # Build using tree-sitter
        print("  Compiling grammar...")
        try:
            Language.build_library(str(output_path), [tmpdir])
        except Exception as e:
            print(f"Error building grammar: {e}")
            return False

    print(f"Grammar built successfully: {output_path}")
    return True


def check_grammar() -> bool:
    """Check if the grammar is already built."""
    output_path = OUTPUT_DIR / get_library_filename()
    return output_path.exists()


if __name__ == "__main__":
    if "--check" in sys.argv:
        if check_grammar():
            print("Grammar already built.")
            sys.exit(0)
        else:
            print("Grammar not found.")
            sys.exit(1)
    else:
        success = build()
        sys.exit(0 if success else 1)
