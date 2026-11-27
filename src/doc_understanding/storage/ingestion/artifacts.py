from hashlib import sha256
from pathlib import Path, PurePosixPath

from doc_understanding.domain.models import ArtifactRef


class LocalArtifactStore:
    """Append-only store in a caller-owned run directory; rejects path traversal."""
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def write(self, key: str, data: bytes, media_type: str) -> ArtifactRef:
        parts = PurePosixPath(key)
        if (parts.is_absolute() or not parts.parts or ".." in parts.parts
                or "\\" in key or ":" in key):
            raise ValueError("unsafe artifact key")
        target = (self.root / key).resolve()
        if not target.is_relative_to(self.root):
            raise ValueError("artifact escapes run directory")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(data)
        return ArtifactRef(path=key, sha256=sha256(data).hexdigest(), media_type=media_type)
