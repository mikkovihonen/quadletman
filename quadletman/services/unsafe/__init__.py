"""Functions that cannot carry ``@sanitized.enforce`` because they take plain ``str``.

Code in this package is exempt from the ``@sanitized.enforce`` rule that applies
to all other ``services/`` functions.  Each function here operates on internally
generated strings (Jinja2 output, OS-provided paths) that are never user-supplied.
"""
