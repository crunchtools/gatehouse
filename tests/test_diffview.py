"""Tests for the structured BEFORE/AFTER diff view (issue #19)."""

from __future__ import annotations

from gatehouse.diffview import render_diff_view

MODIFIED_DIFF = """\
diff --git a/src/app.py b/src/app.py
index 1234567..89abcde 100644
--- a/src/app.py
+++ b/src/app.py
@@ -10,3 +10,5 @@ def handler(request):
     user = request.user
-    query = f"SELECT * FROM t WHERE id = {user.id}"
+    if not user.is_authenticated:
+        raise PermissionError
+    query = build_query(user.id)
     return run(query)
"""

REMOVED_CHECK_DIFF = """\
diff --git a/src/auth.py b/src/auth.py
index 1234567..89abcde 100644
--- a/src/auth.py
+++ b/src/auth.py
@@ -5,4 +5,2 @@ def login(user, password):
     record = lookup(user)
-    if not verify_password(record, password):
-        raise AuthError("bad credentials")
     start_session(record)
"""

NEW_FILE_DIFF = """\
diff --git a/src/new.py b/src/new.py
new file mode 100644
index 0000000..89abcde
--- /dev/null
+++ b/src/new.py
@@ -0,0 +1,2 @@
+def hello():
+    return "hi"
"""

DELETED_FILE_DIFF = """\
diff --git a/src/gone.py b/src/gone.py
deleted file mode 100644
index 89abcde..0000000
--- a/src/gone.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def validate(token):
-    return check(token)
"""

RENAME_DIFF = """\
diff --git a/src/old_name.py b/src/new_name.py
similarity index 100%
rename from src/old_name.py
rename to src/new_name.py
"""

BINARY_DIFF = """\
diff --git a/logo.png b/logo.png
index 1234567..89abcde 100644
Binary files a/logo.png and b/logo.png differ
"""

NO_NEWLINE_DIFF = """\
diff --git a/f.txt b/f.txt
index 1234567..89abcde 100644
--- a/f.txt
+++ b/f.txt
@@ -1 +1 @@
-old
\\ No newline at end of file
+new
\\ No newline at end of file
"""


def test_modified_file_has_before_and_after() -> None:
    view = render_diff_view(MODIFIED_DIFF)
    assert view is not None
    assert "### src/app.py (modified)" in view
    assert "BEFORE" in view
    assert "AFTER" in view


def test_added_lines_marked_in_after() -> None:
    view = render_diff_view(MODIFIED_DIFF)
    assert view is not None
    assert "[+]| " in view
    assert "if not user.is_authenticated:" in view


def test_removed_lines_preserved_and_marked() -> None:
    view = render_diff_view(REMOVED_CHECK_DIFF)
    assert view is not None
    assert "if not verify_password(record, password):" in view
    assert "[-]| " in view


def test_after_block_uses_new_file_line_numbers() -> None:
    view = render_diff_view(MODIFIED_DIFF)
    assert view is not None
    # Hunk starts at new line 10; the first added line is new line 11.
    assert "   11 [+]|     if not user.is_authenticated:" in view


def test_context_lines_unmarked() -> None:
    view = render_diff_view(MODIFIED_DIFF)
    assert view is not None
    assert "   10    |     user = request.user" in view


def test_pure_addition_hunk_omits_before_block() -> None:
    view = render_diff_view(NEW_FILE_DIFF)
    assert view is not None
    assert "BEFORE" not in view.split("### src/new.py")[1]


def test_new_file_labeled() -> None:
    view = render_diff_view(NEW_FILE_DIFF)
    assert view is not None
    assert "### src/new.py (new file)" in view


def test_deleted_file_warns_about_removed_controls() -> None:
    view = render_diff_view(DELETED_FILE_DIFF)
    assert view is not None
    assert "### src/gone.py (deleted)" in view
    assert "DELETED" in view
    assert "def validate(token):" in view


def test_rename_without_content_change() -> None:
    view = render_diff_view(RENAME_DIFF)
    assert view is not None
    assert "renamed from src/old_name.py" in view
    assert "src/new_name.py" in view


def test_binary_file_noted() -> None:
    view = render_diff_view(BINARY_DIFF)
    assert view is not None
    assert "Binary file" in view


def test_no_newline_marker_skipped() -> None:
    view = render_diff_view(NO_NEWLINE_DIFF)
    assert view is not None
    assert "No newline" not in view
    assert "new" in view


def test_preamble_explains_direction() -> None:
    view = render_diff_view(MODIFIED_DIFF)
    assert view is not None
    assert "NOT a finding" in view
    assert "REMOVED" in view


def test_unparseable_input_returns_none() -> None:
    assert render_diff_view("this is not a diff") is None
    assert render_diff_view("") is None


def test_multiple_files() -> None:
    view = render_diff_view(MODIFIED_DIFF + REMOVED_CHECK_DIFF)
    assert view is not None
    assert "### src/app.py (modified)" in view
    assert "### src/auth.py (modified)" in view
