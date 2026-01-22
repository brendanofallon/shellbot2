"""
Tools package for shellbot.

This package contains all tool modules that can be used by assistants.
"""

from . import botfunctions, memorytool, docstoretool
from . import fastmailtool, cal, imagetool

__all__ = [
    'botfunctions',
    'memorytool',
    'docstoretool',
    'fastmailtool',
    'cal',
    'imagetool',
]
