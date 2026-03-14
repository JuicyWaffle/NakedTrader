"""nakedtrader.utils — Gedeelde utility functies."""

import json
from pathlib import Path


def atomic_write_json(path: Path | str, data, *, indent: int | None = None):
    """Schrijf JSON atomisch naar disk via tmp-file + rename.

    Args:
        path: Doelbestand.
        data: JSON-serializeerbare data.
        indent: Optionele JSON-indentatie (None = compact).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)
    tmp.replace(path)
