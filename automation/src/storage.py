"""Where published imports, state, backups and reports live.

LocalStore is a folder laid out like the GCS bucket of a DCP deployment (ingestion/input/<import>/...), so the
local run exercises the same paths a Cloud Run job would. GcsStore is used once the GCP resources exist.
"""

import shutil
from pathlib import Path


class LocalStore:
    def __init__(self, root):
        self.root = Path(root)

    def describe(self, key):
        return str(self.root / key)

    def read_text(self, key):
        path = self.root / key
        return path.read_text(encoding="utf-8") if path.exists() else None

    def write_text(self, key, text):
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8", newline="")
        tmp.replace(path)

    def upload_file(self, local_path, key):
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local_path, path)

    def list(self, prefix):
        base = self.root / prefix
        if not base.exists():
            return []
        return sorted(str(p.relative_to(self.root)).replace("\\", "/") for p in base.rglob("*") if p.is_file())


class GcsStore:
    def __init__(self, bucket_name, project_id):
        from google.cloud import storage  # only needed in GCP mode

        self.bucket_name = bucket_name
        self.bucket = storage.Client(project=project_id).bucket(bucket_name)

    def describe(self, key):
        return f"gs://{self.bucket_name}/{key}"

    def read_text(self, key):
        blob = self.bucket.blob(key)
        return blob.download_as_text() if blob.exists() else None

    def write_text(self, key, text):
        self.bucket.blob(key).upload_from_string(text, content_type="text/plain; charset=utf-8")

    def upload_file(self, local_path, key):
        self.bucket.blob(key).upload_from_filename(str(local_path))

    def list(self, prefix):
        return sorted(b.name for b in self.bucket.list_blobs(prefix=prefix))
