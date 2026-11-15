"""
tests/test_dialog_parents.py - every dialog names the window it sits on, so none opens behind an always-on-top skin or
on the hidden default view (where Windows never shows it).

Run with:
    python -m unittest tests.test_dialog_parents -v
"""

import ast
import io
import os
import unittest

REPO = os.path.join(os.path.dirname(__file__), '..')
FILES = ('music_player.py', 'skin_mode.py', 'stats_ui.py', 'albums_browser.py')


def _tree(name):
    with io.open(os.path.join(REPO, name), encoding='utf-8') as f:
        return ast.parse(f.read())


class DialogParentTests(unittest.TestCase):
    def test_every_messagebox_and_file_dialog_names_a_parent(self):
        missing = []
        for name in FILES:
            for node in ast.walk(_tree(name)):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id in ('messagebox', 'filedialog', 'simpledialog')
                        and not any(k.arg == 'parent' for k in node.keywords)):
                    missing.append(f"{name}:{node.lineno} {node.func.value.id}.{node.func.attr}")
        self.assertEqual(missing, [])

    def test_every_player_dialog_window_is_tied_to_the_skin(self):
        """Each tk.Toplevel(...) the player makes is followed in the same function by self._own_dialog(it)."""
        loose = []
        for fn in ast.walk(_tree('music_player.py')):
            if not isinstance(fn, ast.FunctionDef):
                continue
            made, owned = {}, set()
            for node in ast.walk(fn):
                if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                        and isinstance(node.value.func, ast.Attribute) and node.value.func.attr == 'Toplevel'):
                    made[ast.unparse(node.targets[0])] = node.lineno
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == '_own_dialog' and node.args):
                    owned.add(ast.unparse(node.args[0]))
            loose += [f"{fn.name}:{line} {var}" for var, line in made.items() if var not in owned]
        self.assertEqual(loose, [])


if __name__ == '__main__':
    unittest.main()
