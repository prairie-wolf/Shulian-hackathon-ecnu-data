"""Allowlisted public fixtures in an independent workspace; no user files."""
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def public_workspace():
    with tempfile.TemporaryDirectory() as directory:
        workspace = Path(directory)
        for name in ("aiplatform", "app", "ontology"):
            shutil.copytree(ROOT / name, workspace / name,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for name in ("mappings.json", "check.sh"):
            shutil.copy(ROOT / name, workspace)
        fixtures = {
            "processed": ("institutions.csv", "scholars.csv", "publications.csv",
                          "venues.csv", "fields.csv", "affiliation.csv", "author_of.csv",
                          "published_in.csv", "belongs_to_field.csv"),
            "raw": ("companies.csv", "companies_v2.csv", "institution_labels.csv",
                    "scholar_labels.csv", "v2_inst_clean.json", "v2_fields_clean.json",
                    "v2_works_clean.json", "datasets.csv"),
        }
        for folder, names in fixtures.items():
            target = workspace / "data" / folder
            target.mkdir(parents=True)
            for name in names:
                shutil.copy(ROOT / "data" / folder / name, target)
        env = {k: v for k, v in os.environ.items()
               if k.upper() in ("SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "PATH",
                                "NUMBER_OF_PROCESSORS", "PYTHONPATH", "PROGRAMFILES")}
        env.update(TEMP=directory, TMP=directory, USERPROFILE=directory,
                   PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
        yield workspace, env
