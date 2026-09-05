"""Test package for OpenTPT.

This file is not optional. PyOpenMagnetics 1.6.4 installs its whole source
tree into the site-packages root — including a top-level ``tests/__init__.py``
— which shadows a namespace-package ``tests/`` here and makes every
``python -m unittest tests.*`` fail with ModuleNotFoundError. A regular
package beats a namespace portion regardless of sys.path order, so declaring
this directory a package restores the local tests.
"""
