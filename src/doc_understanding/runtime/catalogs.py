"""Opt-in local catalogs. Opening the application never selects example data."""
import json
import os
import re
from pathlib import Path


def read_catalog(variable):
    path = os.environ.get(variable)
    if not path:
        return []
    value = json.loads(Path(path).read_text(encoding='utf8'))
    if not isinstance(value, list):
        raise ValueError('CATALOG_MUST_BE_A_LIST')
    return value


def sample_catalog(root):
    samples = {}
    for item in read_catalog('DOCUMENT_SAMPLE_CATALOG'):
        key = item['id']
        if not re.fullmatch(r'[A-Za-z0-9_-]+', key) or key in samples:
            raise ValueError('INVALID_OR_DUPLICATE_SAMPLE_ID')
        path = (Path(root) / item['path']).resolve(strict=True)
        if not path.is_relative_to(Path(root).resolve()) or path.suffix.lower() != '.pdf':
            raise ValueError('SAMPLE_PATH_OUTSIDE_PROJECT_OR_NOT_PDF')
        samples[key] = (item['name'], path)
    return samples


def campaign_manifest(root):
    value = os.environ.get('DOCUMENT_CAMPAIGN_MANIFEST')
    if not value:
        return None
    path = (Path(root) / value).resolve(strict=True)
    if not path.is_relative_to(Path(root).resolve()) or not path.is_file():
        raise ValueError('CAMPAIGN_MANIFEST_OUTSIDE_PROJECT')
    return path
