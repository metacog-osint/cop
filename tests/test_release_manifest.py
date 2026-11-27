"""tools/release_manifest.py: the checksum files of a snapshot release."""
import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import release_manifest as rm  # noqa: E402


def asset(name, body):
    return {"name": name, "size": len(body), "digest": "sha256:" + hashlib.sha256(body).hexdigest(),
            "updated_at": "2026-11-27T00:00:00Z"}


with tempfile.TemporaryDirectory() as tmp:
    local, out = Path(tmp, "local"), Path(tmp, "out")
    local.mkdir()
    day, slot = b"day copy", b"slot copy"
    (local / "2026-11-27.geojson.gz").write_bytes(day)
    assets = [asset("2026-11-27T00Z.snap.json.gz", slot), asset("2026-11-27.geojson.gz", day),
              asset(rm.SUMS, b"old"), asset(rm.JSON_NAME, b"old")]

    assert rm.release("metacog-osint/cop", "snapshots-2026-11", out, local, assets) is True
    m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert m["format"] == "cop-manifest/1" and m["release"] == "snapshots-2026-11"
    assert [f["name"] for f in m["files"]] == ["2026-11-27.geojson.gz", "2026-11-27T00Z.snap.json.gz"], m
    assert m["files"][1]["sha256"] == hashlib.sha256(slot).hexdigest()
    sums = (out / rm.SUMS).read_bytes()
    assert sums.startswith(hashlib.sha256(day).hexdigest().encode() + b"  2026-11-27.geojson.gz\n"), sums

    # An unchanged release gives the same bytes, so nothing is uploaded again.
    held = assets[:2] + [asset(rm.JSON_NAME, (out / rm.JSON_NAME).read_bytes()), asset(rm.SUMS, sums)]
    assert rm.release("metacog-osint/cop", "snapshots-2026-11", out, local, held) is False

    assert rm.verify(m, local) == []
    (local / "2026-11-27.geojson.gz").write_bytes(b"day cop!")
    assert len(rm.verify(m, local)) == 1
    # A local copy that is not what GitHub holds fails; so does a file with no digest and no local copy.
    for bad, folder in ((assets, local), ([{"name": "x", "size": 1}], None)):
        try:
            rm.build("metacog-osint/cop", "snapshots-2026-11", bad, folder)
        except rm.Mismatch:
            pass
        else:
            raise AssertionError("no Mismatch")

print("release manifest: ok")
