"""Pytest configuration — force matplotlib Agg backend before any test imports it.

On Windows (Microsoft Store Python) the Tk/Tcl libraries are not properly installed,
which causes a `_tkinter` ImportError when matplotlib tries to use the default TkAgg
backend. Forcing Agg here prevents that failure for any test that exercises chart code.
"""

import matplotlib

matplotlib.use("Agg")
