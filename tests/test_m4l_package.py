from __future__ import annotations

import contextlib
import io
import json
import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

from codex_devtools.cli import main
from codex_devtools.maxfiles import decode_file
from codex_devtools.m4l_package import M4L_PROJECT_TYPE, package_m4l_candidate


def midi_patch(dependencies=()):
    boxes = [
        {"box": {"id": "midi-in", "maxclass": "newobj", "text": "midiin", "numinlets": 1, "numoutlets": 1}},
        {"box": {"id": "midi-out", "maxclass": "newobj", "text": "midiout", "numinlets": 1, "numoutlets": 1}},
    ]
    return {"patcher": {
        "fileversion": 1,
        "project": {"amxdtype": M4L_PROJECT_TYPE},
        "boxes": boxes,
        "lines": [],
        "dependency_cache": [{"name": name, "type": "TEXT", "implicit": 1, "bootpath": "."} for name in dependencies],
    }}


def nested_patcher(dependencies=(), boxes=()):
    children = []
    for index, name in enumerate(boxes):
        child = {"boxes": [], "lines": [],
                 "dependency_cache": [{"name": dependency, "bootpath": "."}
                                      for dependency in dependencies]}
        children.append({"box": {"id": f"nested-{index}", "maxclass": "newobj",
                                  "text": name, "patcher": child}})
    return {"boxes": children, "lines": [],
            "dependency_cache": [{"name": name, "type": "TEXT", "bootpath": "."}
                                 for name in dependencies]}


def template_bytes(patch=None, flag=0):
    root = midi_patch() if patch is None else patch
    body = (json.dumps(root, sort_keys=True) + "\n").encode() + b"\0"
    header = (b"ampf" + struct.pack("<I", 4) + b"mmmm" + b"meta" + struct.pack("<I", 4)
              + struct.pack("<I", flag) + b"ptch")
    return header + struct.pack("<I", len(body)) + body


def _chunk(tag, payload):
    return tag + struct.pack(">I", len(payload) + 8) + payload


def archived_template_bytes():
    root = (json.dumps(midi_patch(), sort_keys=True) + "\n").encode() + b"\0"
    asset = b"unrelated frozen asset"
    boundary = 16 + len(root) + len(asset)
    records = []
    offset = 16
    for name, data in ((b"root.maxpat", root), (b"old.bin", asset)):
        fields = b"".join((_chunk(b"fnam", name + b"\0"),
                           _chunk(b"of32", struct.pack(">I", offset)),
                           _chunk(b"sz32", struct.pack(">I", len(data)))))
        records.append(_chunk(b"dire", fields))
        offset += len(data)
    body = b"mx@c" + struct.pack(">III", 16, 0, boundary) + root + asset + _chunk(b"dlst", b"".join(records))
    return template_bytes()[:28] + struct.pack("<I", len(body)) + body


class PackageM4LCandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="devtools-m4l-package-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source_root = self.base / "source"
        self.source_root.mkdir()
        self.source = self.source_root / "device.maxpat"
        self.template = self.base / "template.amxd"
        self.allowlist = self.base / "allowlist.json"
        self.output = self.base / "candidate"
        self._write_source()
        self.template.write_bytes(template_bytes())
        self._allow([])

    def _write_source(self, dependencies=(), patch=None):
        self.source.write_text(json.dumps(patch or midi_patch(dependencies), indent=2) + "\n", encoding="utf-8")

    def _allow(self, rows):
        self.allowlist.write_text(json.dumps(rows), encoding="utf-8")

    def _package(self, out=None):
        return package_m4l_candidate(self.source_root, self.source, self.template, self.allowlist,
                                     out or self.output)

    def test_roundtrip_hashes_declared_sidecar_and_rebuild_are_deterministic(self):
        self._write_source(["knob.js"])
        self._allow([{"name": "knob.js", "source": "js/knob.js"}])
        dependency = self.source_root / "js" / "knob.js"
        dependency.parent.mkdir()
        dependency.write_bytes(b"// reviewed source sidecar\n")
        source_before = self.source.read_bytes()
        template_before = self.template.read_bytes()
        first = self._package(self.base / "candidate-a")
        second = self._package(self.base / "candidate-b")
        self.assertEqual(first, second)
        self.assertEqual((self.base / "candidate-a" / "device.amxd").read_bytes(),
                         (self.base / "candidate-b" / "device.amxd").read_bytes())
        artifact = (self.base / "candidate-a" / "device.amxd").read_bytes()
        self.assertEqual(artifact[:28], template_before[:28], "preserve the supported Live-created wrapper prefix")
        _, entries, decoded = decode_file(artifact)
        self.assertEqual(entries, [])
        self.assertEqual(decoded, json.loads(self.source.read_text(encoding="utf-8")))
        manifest = json.loads((self.base / "candidate-a" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["format"], "codex-devtools-m4l-candidate-v1")
        self.assertEqual(manifest["source"]["sha256"], hashlib.sha256(source_before).hexdigest())
        self.assertEqual(manifest["template"]["sha256"], hashlib.sha256(template_before).hexdigest())
        self.assertEqual(manifest["dependencies"][0]["sha256"], hashlib.sha256(dependency.read_bytes()).hexdigest())
        self.assertTrue(manifest["roundtrip"]["root_equal_to_source"])
        self.assertFalse(manifest["freeze"]["performed"])
        self.assertFalse(manifest["dependencies"][0]["embedded"])
        self.assertNotIn(str(self.base), json.dumps(manifest), "manifest does not leak machine-specific roots")
        self.assertEqual(self.source.read_bytes(), source_before)
        self.assertEqual(self.template.read_bytes(), template_before)

    def test_supported_header_flag_one_roundtrips_without_rewriting_prefix(self):
        self.template.write_bytes(template_bytes(flag=1))
        template_before = self.template.read_bytes()
        self._package()
        artifact = (self.output / "device.amxd").read_bytes()
        self.assertEqual(artifact[:28], template_before[:28])
        self.assertEqual(decode_file(artifact)[0], "amxd")

    def test_existing_directory_file_or_dangling_symlink_is_not_overwritten(self):
        existing = self.base / "existing"
        existing.mkdir()
        sentinel = existing / "keep.bin"
        sentinel.write_bytes(b"original bytes")
        with self.assertRaises(FileExistsError):
            self._package(existing)
        self.assertEqual(sentinel.read_bytes(), b"original bytes")
        self.assertEqual(list(existing.iterdir()), [sentinel])

        existing_file = self.base / "existing-file"
        existing_file.write_bytes(b"keep")
        with self.assertRaises(FileExistsError):
            self._package(existing_file)
        self.assertEqual(existing_file.read_bytes(), b"keep")

        dangling = self.base / "dangling"
        dangling.symlink_to(self.base / "missing-target")
        with self.assertRaises(FileExistsError):
            self._package(dangling)
        self.assertTrue(dangling.is_symlink())

    def test_partial_write_failure_rolls_back_new_candidate_directory(self):
        original_open = Path.open
        output = self.output

        class FailingWriter:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                self.stream.__enter__()
                return self

            def write(self, data):
                self.stream.write(data[:4])
                raise OSError("injected candidate write failure")

            def __exit__(self, exc_type, exc, traceback):
                return self.stream.__exit__(exc_type, exc, traceback)

        def injected_open(path, *args, **kwargs):
            stream = original_open(path, *args, **kwargs)
            if path == output / "device.amxd" and args and args[0] == "xb":
                return FailingWriter(stream)
            return stream

        with mock.patch.object(Path, "open", injected_open):
            with self.assertRaisesRegex(OSError, "injected candidate write failure"):
                self._package(output)
        self.assertFalse(output.exists(), "failed packaging removes its partial artifact and new output directory")

    def test_output_directory_must_be_outside_source_root(self):
        with self.assertRaisesRegex(ValueError, "outside the declared source root"):
            self._package(self.source_root / "candidate")

    def test_wrong_template_kind_and_missing_stream_fail_closed(self):
        wrong = midi_patch()
        wrong["patcher"]["project"]["amxdtype"] = 0
        self.template.write_bytes(template_bytes(wrong))
        with self.assertRaisesRegex(ValueError, "project type"):
            self._package()
        self.template.write_bytes(template_bytes({"patcher": {"project": {"amxdtype": M4L_PROJECT_TYPE}, "boxes": [], "lines": []}}))
        with self.assertRaisesRegex(ValueError, "midiin.*midiout"):
            self._package()

    def test_embedded_template_archive_is_rejected(self):
        self.template.write_bytes(archived_template_bytes())
        with self.assertRaisesRegex(ValueError, "embedded archive assets"):
            self._package()

    def test_unknown_encrypted_or_malformed_template_headers_are_rejected(self):
        self.template.write_bytes(b"not an AMXD")
        with self.assertRaisesRegex(ValueError, "not a supported AMXD"):
            self._package()
        for offset, replacement in ((8, b"zzzz"), (12, b"JUNK"), (16, struct.pack("<I", 5)),
                                    (20, struct.pack("<I", 2)), (24, b"ciph")):
            malformed = bytearray(template_bytes())
            malformed[offset:offset + len(replacement)] = replacement
            self.template.write_bytes(malformed)
            with self.subTest(offset=offset):
                with self.assertRaises(ValueError):
                    self._package(self.base / f"bad-{offset}")

    def test_source_kind_project_marker_and_graph_are_validated(self):
        self._write_source(patch={"patcher": {"project": {"amxdtype": 0}, "boxes": [], "lines": []}})
        with self.assertRaisesRegex(ValueError, "source metadata conflicts"):
            self._package()
        self._write_source(patch={"patcher": {"project": {"amxdtype": M4L_PROJECT_TYPE}, "boxes": [], "lines": []}})
        with self.assertRaisesRegex(ValueError, "source root is not a MIDI-effect"):
            self._package()
        invalid = midi_patch()
        invalid["patcher"]["lines"] = [{"patchline": {"source": ["missing", 0], "destination": ["midi-out", 0]}}]
        self._write_source(patch=invalid)
        with self.assertRaisesRegex(ValueError, "source Max graph is invalid"):
            self._package()

    def test_source_path_kind_and_root_are_checked(self):
        outside = self.base / "outside.maxpat"
        outside.write_text(json.dumps(midi_patch()), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "inside the declared source root"):
            package_m4l_candidate(self.source_root, outside, self.template, self.allowlist, self.output)
        wrong_extension = self.source_root / "device.mxf"
        wrong_extension.write_text(self.source.read_text(encoding="utf-8"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "source must be a .maxpat"):
            package_m4l_candidate(self.source_root, wrong_extension, self.template, self.allowlist, self.output)

    def test_dependency_allowlist_must_be_exact_and_paths_are_safe(self):
        self._write_source(["needed.js"])
        self._allow([])
        with self.assertRaisesRegex(ValueError, "allowlist must match"):
            self._package()
        for source in ("../needed.js", "/tmp/needed.js", "folder\\needed.js"):
            self._allow([{"name": "needed.js", "source": source}])
            with self.subTest(source=source), self.assertRaises(ValueError):
                self._package(self.base / ("bad-" + str(len(source))))

    def test_inline_nested_and_deep_dependency_caches_are_in_exact_closure(self):
        patch = midi_patch()
        deep = nested_patcher(["deep.js"])
        nested = nested_patcher(["nested.js"], boxes=["deep patch"])
        nested["boxes"][0]["box"]["patcher"] = deep
        patch["patcher"]["boxes"].append({"box": {
            "id": "inline", "maxclass": "newobj", "text": "nested patch", "patcher": nested,
        }})
        self._write_source(patch=patch)
        (self.source_root / "nested.js").write_bytes(b"nested sidecar")
        (self.source_root / "deep.js").write_bytes(b"deep sidecar")

        self._allow([])
        with self.assertRaisesRegex(ValueError, r"all source and nested dependency_cache entries.*deep.js.*nested.js"):
            self._package(self.base / "nested-missing")

        self._allow([
            {"name": "nested.js", "source": "nested.js"},
            {"name": "deep.js", "source": "deep.js"},
        ])
        manifest = self._package()
        self.assertEqual([item["name"] for item in manifest["dependencies"]], ["deep.js", "nested.js"])
        self.assertEqual((self.output / "nested.js").read_bytes(), b"nested sidecar")
        self.assertEqual((self.output / "deep.js").read_bytes(), b"deep sidecar")

    def test_nested_dependency_cache_rejects_casefold_collision_and_malformed_cache(self):
        patch = midi_patch()
        patch["patcher"]["boxes"].append({"box": {
            "id": "inline", "maxclass": "newobj", "text": "nested",
            "patcher": nested_patcher(["Nested.js"]),
        }})
        self._write_source(patch=patch)
        self._allow([{"name": "Nested.js", "source": "one.js"},
                     {"name": "nested.js", "source": "two.js"}])
        with self.assertRaisesRegex(ValueError, "duplicate dependency destination name"):
            self._package(self.base / "nested-collision")

        patch["patcher"]["boxes"][2]["box"]["patcher"]["dependency_cache"] = "not-an-array"
        self._write_source(patch=patch)
        self._allow([])
        with self.assertRaisesRegex(ValueError, "dependency_cache must be an array"):
            self._package(self.base / "nested-malformed")

    def test_sidecar_maxpat_dependency_cache_is_transitive(self):
        patch = midi_patch(["helper.maxpat"])
        self._write_source(patch=patch)
        helper = self.source_root / "helper.maxpat"
        helper.write_text(json.dumps({"patcher": {
            "boxes": [], "lines": [], "dependency_cache": [{"name": "helper.js", "bootpath": "."}],
        }}), encoding="utf-8")
        (self.source_root / "helper.js").write_bytes(b"helper source")
        self._allow([{"name": "helper.maxpat", "source": "helper.maxpat"}])
        with self.assertRaisesRegex(ValueError, r"missing=\['helper.js'\]"):
            self._package(self.base / "transitive-missing")
        self._allow([{"name": "helper.maxpat", "source": "helper.maxpat"},
                     {"name": "helper.js", "source": "helper.js"}])
        manifest = self._package()
        self.assertEqual({item["name"] for item in manifest["dependencies"]}, {"helper.maxpat", "helper.js"})

    def test_missing_and_escaping_dependency_sources_are_rejected(self):
        self._write_source(["needed.js"])
        self._allow([{"name": "needed.js", "source": "needed.js"}])
        with self.assertRaisesRegex(ValueError, "is missing"):
            self._package()
        outside = self.base / "outside.js"
        outside.write_text("// outside", encoding="utf-8")
        (self.source_root / "link.js").symlink_to(outside)
        self._allow([{"name": "needed.js", "source": "link.js"}])
        with self.assertRaisesRegex(ValueError, "escapes declared source root"):
            self._package()

    def test_allowlist_duplicate_names_and_source_paths_are_rejected(self):
        self._write_source(["Alpha.js", "alpha.js"])
        self._allow([{"name": "Alpha.js", "source": "one.js"}, {"name": "alpha.js", "source": "two.js"}])
        with self.assertRaisesRegex(ValueError, "duplicate dependency destination name"):
            self._package()
        self._write_source(["one.js", "two.js"])
        self._allow([{"name": "one.js", "source": "same.js"}, {"name": "two.js", "source": "same.js"}])
        with self.assertRaisesRegex(ValueError, "duplicate dependency source path"):
            self._package()

    def test_dependency_cache_names_bootpaths_and_frozen_containers_are_rejected(self):
        self._write_source(["needed.js"])
        self._allow([{"name": "needed.js", "source": "needed.js"}])
        (self.source_root / "needed.js").write_bytes(b"source")
        patch = midi_patch(["needed.js"])
        patch["patcher"]["dependency_cache"][0]["bootpath"] = "elsewhere"
        self._write_source(patch=patch)
        with self.assertRaisesRegex(ValueError, "nonlocal bootpath"):
            self._package()
        self._write_source(["needed.js"])
        (self.source_root / "needed.js").write_bytes(b"ampf" + bytes(10))
        with self.assertRaisesRegex(ValueError, "packaged/frozen Max container"):
            self._package()

    def test_dependency_names_cannot_collide_with_artifact_or_manifest(self):
        for name in ("device.amxd", "MANIFEST.JSON"):
            self._write_source([name])
            (self.source_root / "payload.bin").write_bytes(b"source")
            self._allow([{"name": name, "source": "payload.bin"}])
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "collides with a dependency"):
                self._package(self.base / ("collision-" + name.replace(".", "-")))

    def test_source_without_marker_inherits_only_template_project_metadata(self):
        source = midi_patch()
        del source["patcher"]["project"]
        self._write_source(patch=source)
        self._package()
        _, entries, packaged = decode_file((self.output / "device.amxd").read_bytes())
        self.assertEqual(entries, [])
        self.assertEqual(packaged["patcher"]["project"]["amxdtype"], M4L_PROJECT_TYPE)
        self.assertEqual(packaged["patcher"]["dependency_cache"], [])
        self.assertEqual(packaged["patcher"]["boxes"], source["patcher"]["boxes"])

    def test_cli_entrypoint_writes_candidate_and_manifest(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            status = main(["max", "package-m4l-candidate", "--source-root", str(self.source_root),
                           "--source", str(self.source), "--template", str(self.template),
                           "--dependency-allowlist", str(self.allowlist), "--out", str(self.output)])
        self.assertEqual(status, 0)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["manifest"]["format"], "codex-devtools-m4l-candidate-v1")
        self.assertTrue((self.output / "device.amxd").is_file())
        self.assertTrue((self.output / "manifest.json").is_file())


if __name__ == "__main__":
    unittest.main()
