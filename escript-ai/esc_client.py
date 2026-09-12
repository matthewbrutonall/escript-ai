"""
Minimal eScriptorium REST client for the AI write-path prototype.

Talks to a local/throwaway instance only (base URL + token from
~/.config/escript-ai/). Never production. Writes AI output as a NEW named
Transcription layer via the stock API. Note: stock REST still cannot stamp
`version_source` (editable=False); Escript AI's ORM path can. See
ARCHITECTURE.md §4. `LineSerializer` exposes `baseline`/`mask`.
"""
from __future__ import annotations

import os
import requests

CFG = os.path.expanduser("~/.config/escript-ai")


def _read(name: str) -> str:
    with open(os.path.join(CFG, name)) as f:
        return f.read().strip()


class Esc:
    def __init__(self, base: str | None = None, token: str | None = None):
        self.base = (base or _read("esc_base")).rstrip("/")
        self.token = token or _read("esc_token")
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Token {self.token}"

    # --- low level ---------------------------------------------------------
    def _url(self, path: str) -> str:
        return f"{self.base}/api/{path.lstrip('/')}"

    def get(self, path, **kw):
        r = self.s.get(self._url(path), **kw); r.raise_for_status(); return r.json()

    def post(self, path, **kw):
        r = self.s.post(self._url(path), **kw)
        if not r.ok:
            raise RuntimeError(f"POST {path} -> {r.status_code}: {r.text[:400]}")
        return r.json()

    @staticmethod
    def pk(obj: dict) -> int:
        """eScriptorium serializers vary: projects use `id`, most use `pk`."""
        return obj.get("pk", obj.get("id"))

    # --- objects -----------------------------------------------------------
    def create_project(self, name: str) -> dict:
        return self.post("projects/", json={"name": name})

    def create_document(self, name: str, project_slug: str,
                        main_script: str = "Latin", **extra) -> dict:
        # `project` is a SlugRelatedField (slug, not pk); main_script is required.
        body = {"name": name, "project": project_slug, "main_script": main_script}
        body.update(extra)
        return self.post("documents/", json=body)

    def upload_part(self, doc_pk: int, image_path: str) -> dict:
        with open(image_path, "rb") as fh:
            files = {"image": (os.path.basename(image_path), fh, "image/jpeg")}
            r = self.s.post(self._url(f"documents/{doc_pk}/parts/"), files=files)
        if not r.ok:
            raise RuntimeError(f"upload_part -> {r.status_code}: {r.text[:400]}")
        return r.json()

    def create_line(self, doc_pk: int, part_pk: int, mask, baseline=None) -> dict:
        body = {"document_part": part_pk, "mask": mask}
        if baseline:
            body["baseline"] = baseline
        return self.post(f"documents/{doc_pk}/parts/{part_pk}/lines/", json=body)

    def list_lines(self, doc_pk: int, part_pk: int) -> list:
        return self.get(f"documents/{doc_pk}/parts/{part_pk}/lines/")["results"]

    def create_transcription(self, doc_pk: int, name: str) -> dict:
        return self.post(f"documents/{doc_pk}/transcriptions/", json={"name": name})

    def write_line_transcription(self, doc_pk, part_pk, line_pk, trans_pk,
                                 content, version_source) -> dict:
        return self.post(
            f"documents/{doc_pk}/parts/{part_pk}/transcriptions/",
            json={"line": line_pk, "transcription": trans_pk,
                  "content": content, "version_source": version_source})

    def list_line_transcriptions(self, doc_pk, part_pk, trans_pk) -> list:
        rows = self.get(f"documents/{doc_pk}/parts/{part_pk}/transcriptions/",
                        params={"transcription": trans_pk})
        return rows["results"] if isinstance(rows, dict) else rows
