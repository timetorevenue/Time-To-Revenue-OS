"""Bounded WorkflowFigure design properties (schema v5), twin of workflow-graphic.js. No file or model access.

The 215679e WorkflowFigure is a text workflow story. null colours, scale 100 and order "after" are the
website's own values. Version 1 SVG properties migrate through website_studio_migrate.py.
"""
import copy
import math
import re

DEFAULT = {'width': 100, 'horizontal': 'center', 'vertical': 'center', 'accent': None, 'line': None, 'titleScale': 100, 'mobileOrder': 'after'}


def _number(value, low, high):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


def _colour(value):
    return value is None or (isinstance(value, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', value) is not None)


def validate_graphic(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULT):
        raise ValueError('Invalid workflow graphic properties.')
    if value['horizontal'] not in ('start', 'center', 'end') or value['vertical'] not in ('start', 'center', 'end') or value['mobileOrder'] not in ('after', 'before'):
        raise ValueError('Invalid workflow graphic option.')
    if not _number(value['width'], 25, 100):
        raise ValueError('Graphic width must be 25–100%.')
    if not _number(value['titleScale'], 70, 140):
        raise ValueError('Graphic title size must be 70–140%.')
    if not _colour(value['accent']) or not _colour(value['line']):
        raise ValueError('Invalid workflow graphic colour.')
    return copy.deepcopy(value)
