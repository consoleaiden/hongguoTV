#!/usr/bin/env python3
"""Release helpers for the upstream-sync workflow.

Subcommands: version, patch, manifest, verify-signer, self-test.
Importing this module has no side effects.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

PACKAGE = "com.hongguotv.nativeapp"
TAG_RE = re.compile(r"kotlin-v[0-9][0-9A-Za-z.+-]{0,80}")
VERSION_RE = re.compile(r"[0-9][0-9A-Za-z.+-]{0,63}")
APK_NAME_RE = re.compile(r"hongguotv-kotlin-[0-9][0-9A-Za-z.+-]{0,80}-android[568]\.apk")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
MAX_APK_BYTES = 80 * 1024 * 1024

PATCH_TARGETS = (
    "kotlin-tv/core/src/main/kotlin/com/hongguotv/core/AppUpdates.kt",
    "kotlin-tv/core/src/test/kotlin/com/hongguotv/core/AppUpdatesTest.kt",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"::error::{message}")


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def entry(path: Path) -> dict:
    return {"name": path.name, "size": path.stat().st_size, "sha256": sha256(path)}


def parse_gradle(path: Path) -> tuple[str, int]:
    text = path.read_text(encoding="utf-8")
    name = re.search(r'versionName\s*=\s*System\.getenv\([^)]*\)\s*\?:\s*"([^"]+)"', text)
    code = re.search(r"versionCode\s*=\s*System\.getenv\([^)]*\)\?\.toInt\(\)\s*\?:\s*(\d+)", text)
    require(name is not None, f"cannot parse versionName from {path}")
    require(code is not None, f"cannot parse versionCode from {path}")
    require(VERSION_RE.fullmatch(name.group(1)) is not None, "upstream versionName is unsafe")
    return name.group(1), int(code.group(1))


def validate_manifest(data: dict, outputs: Path) -> None:
    """Mirror AppUpdate.parse() so a bad manifest is rejected before publishing."""
    require(data.get("schema") == 1, "schema must be 1")
    require(data.get("packageName") == PACKAGE, "packageName mismatch")
    require(1 <= data.get("versionCode", 0) <= 2147483647, "versionCode out of range")
    require(VERSION_RE.fullmatch(data.get("versionName", "")) is not None, "versionName rejected")
    version_name = data.get("versionName", "")
    require(TAG_RE.fullmatch(data.get("tag", "")) is not None, "tag rejected")
    require(data.get("tag") == "kotlin-v" + version_name, "tag/versionName mismatch")
    require(21 <= data.get("minSdk", 0) <= 100, "minSdk out of range")
    require(COMMIT_RE.fullmatch(data.get("sourceCommit", "")) is not None, "sourceCommit rejected")
    require(len(data.get("notes", "")) <= 1500, "notes too long")
    for key in ("apk", "platformApk"):
        require(key in data, f"{key} missing")
        item = data[key]
        require(APK_NAME_RE.fullmatch(item["name"]) is not None, f"{key} filename rejected: {item['name']}")
        if key == "platformApk":
            require(item["name"].endswith("-android6.apk"), "platformApk must be the android6 universal APK")
        require(SHA256_RE.fullmatch(item["sha256"]) is not None, f"{key} sha256 rejected")
        require(1 <= item["size"] <= MAX_APK_BYTES, f"{key} size out of range")
        path = outputs / item["name"]
        require(path.is_file(), f"{key} file missing: {path}")
        require(path.stat().st_size == item["size"], f"{key} size mismatch")
        require(sha256(path) == item["sha256"], f"{key} sha256 mismatch")


def cmd_version(args: argparse.Namespace) -> None:
    base_name, base_code = parse_gradle(Path(args.gradle))
    tags = Path(args.tags_file).read_text(encoding="utf-8").split() if args.tags_file else []
    previous = 0
    for raw in tags:
        match = re.search(r"-auto\.(\d+)$", raw.strip())
        if match:
            previous = max(previous, int(match.group(1)))
    code = max(base_code + 1, previous + 1)
    require(code <= 2147483647, "versionCode overflow")
    version_name = f"{base_name}-auto.{code}"
    tag = f"kotlin-v{version_name}"
    require(VERSION_RE.fullmatch(version_name) is not None, f"generated versionName rejected: {version_name}")
    require(TAG_RE.fullmatch(tag) is not None, f"generated tag rejected: {tag}")
    lines = "\n".join(
        (
            f"version_name={version_name}",
            f"version_code={code}",
            f"tag={tag}",
            f"base_version={base_name}",
        )
    ) + "\n"
    if args.output:
        with Path(args.output).open("a", encoding="utf-8") as stream:
            stream.write(lines)
    sys.stdout.write(lines)


def cmd_patch(args: argparse.Namespace) -> None:
    repository = args.repo
    require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is not None, "invalid repository name")
    source = Path(PATCH_TARGETS[0])
    test = Path(PATCH_TARGETS[1])
    for path in (source, test):
        require(path.is_file(), f"patch target missing: {path}")

    src_text = source.read_text(encoding="utf-8")
    src_text, count = re.subn(
        r'(const val REPOSITORY = ")[^"]+(")',
        lambda m: m.group(1) + repository + m.group(2),
        src_text,
    )
    require(count >= 1, f"REPOSITORY anchor missing in {source}")
    source.write_text(src_text, encoding="utf-8")

    test_text = test.read_text(encoding="utf-8")
    test_text, count = re.subn(
        r"https://github\.com/[^/]+/[^/]+/releases/download/",
        f"https://github.com/{repository}/releases/download/",
        test_text,
    )
    require(count >= 1, f"downloadUrl anchor missing in {test}")
    test.write_text(test_text, encoding="utf-8")

    for path in (source, test):
        text = path.read_text(encoding="utf-8")
        require("N3urda/hongguoTV" not in text, f"upstream channel remains in {path}")
    print(f"update channel -> {repository} (working tree only, not committed)")


def cmd_manifest(args: argparse.Namespace) -> None:
    outputs = Path(args.outputs)
    version = args.version_name
    names = {
        "apk": f"hongguotv-kotlin-{version}-android5.apk",
        "platformApk": f"hongguotv-kotlin-{version}-android6.apk",
        "arm32": f"hongguotv-kotlin-{version}-android6-arm32-v7a.apk",
        "arm64": f"hongguotv-kotlin-{version}-android6-arm64-v8a.apk",
    }
    paths = {key: outputs / name for key, name in names.items()}
    for key, path in paths.items():
        require(path.is_file(), f"missing build output ({key}): {path}")

    notes = (
        f"自动同步上游 N3urda/hongguoTV 构建（{version}，versionCode {args.version_code}）。"
        "使用本仓库专用发布证书签名；与上游官方包签名不同，首次安装需先卸载旧版。"
        "自本版本起，后续同签名更新可覆盖安装并保留本机收藏、观看记录与设置。"
    )
    data = {
        "schema": 1,
        "packageName": PACKAGE,
        "versionCode": int(args.version_code),
        "versionName": version,
        "minSdk": 21,
        "tag": args.tag,
        "sourceCommit": args.commit,
        "apk": entry(paths["apk"]),
        "platformApk": entry(paths["platformApk"]),
        "notes": notes,
    }
    validate_manifest(data, outputs)
    (outputs / "update.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    files = [paths["apk"], paths["platformApk"], paths["arm32"], paths["arm64"], outputs / "update.json"]
    (outputs / "SHA256SUMS").write_text(
        "".join(f"{sha256(path)}  {path.name}\n" for path in files), encoding="utf-8"
    )
    (outputs / "release-notes.md").write_text(
        f"hongguoTV {version}（versionCode {args.version_code}），Android 5.0+。\n\n"
        f"{notes}\n\n"
        f"源码提交：{args.commit}。\n"
        "四个 APK：android5（API 21，含 FFmpeg）、android6 通用包、"
        "android6-arm32-v7a、android6-arm64-v8a。证书 SHA256 见 docs/SIGNING.md。\n"
        "自签版本之间可覆盖安装并保留本机数据；与上游官方包签名不同，首次安装需卸载旧版。\n",
        encoding="utf-8",
    )
    print(f"manifest ok: {args.tag}")


def cmd_verify_signer(args: argparse.Namespace) -> None:
    expected = args.expected.lower().replace(":", "")
    require(SHA256_RE.fullmatch(expected) is not None, "expected certificate sha256 is invalid")
    android_home = os.environ.get("ANDROID_HOME", "")
    apksigner = Path(android_home) / "build-tools/36.0.0/apksigner"
    require(apksigner.is_file(), f"apksigner not found: {apksigner}")
    apks = sorted(Path(args.outputs).glob("hongguotv-kotlin-*.apk"))
    require(len(apks) == 4, f"expected 4 APKs, found {len(apks)}")
    for apk in apks:
        text = subprocess.check_output(
            [str(apksigner), "verify", "--print-certs", "--min-sdk-version", "21", str(apk)],
            text=True,
        )
        digests = re.findall(r"Signer #\d+ certificate SHA-256 digest: ([0-9a-fA-F]+)", text)
        require(digests == [expected], f"{apk.name}: signer mismatch: {digests}")
        print(f"{apk.name}: signer {digests[0]}")
    print("all APKs signed by the expected release certificate")


def cmd_self_test(args: argparse.Namespace) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        outputs = Path(tmp)
        version = "0.16.11-auto.33"
        for suffix in ("android5", "android6", "android6-arm32-v7a", "android6-arm64-v8a"):
            (outputs / f"hongguotv-kotlin-{version}-{suffix}.apk").write_bytes(
                b"fake-apk-" + suffix.encode()
            )
        base = {
            "schema": 1,
            "packageName": PACKAGE,
            "versionCode": 33,
            "versionName": version,
            "minSdk": 21,
            "tag": f"kotlin-v{version}",
            "sourceCommit": "a" * 40,
            "apk": entry(outputs / f"hongguotv-kotlin-{version}-android5.apk"),
            "platformApk": entry(outputs / f"hongguotv-kotlin-{version}-android6.apk"),
            "notes": "self-test",
        }
        validate_manifest(base, outputs)
        rejected = 0
        for mutation in (
            {"tag": "auto-v1"},
            {"packageName": "com.example.other"},
            {"platformApk": dict(base["platformApk"], name="evil.apk")},
            {"notes": "x" * 1501},
            {"versionName": "../bad"},
        ):
            try:
                validate_manifest(dict(base, **mutation), outputs)
            except SystemExit:
                rejected += 1
            else:
                raise SystemExit(f"::error::self-test accepted invalid mutation: {mutation}")
        require(rejected == 5, f"self-test rejected {rejected}/5 invalid manifests")
    print("self-test ok: accept path + 5 reject paths passed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("version", help="compute the next auto release version")
    p.add_argument("--gradle", default="kotlin-tv/app/build.gradle.kts")
    p.add_argument("--tags-file")
    p.add_argument("--output", help="append key=value lines to this file (GITHUB_OUTPUT)")
    p.set_defaults(func=cmd_version)

    p = sub.add_parser("patch", help="point the in-app updater at this repository")
    p.add_argument("--repo", required=True)
    p.set_defaults(func=cmd_patch)

    p = sub.add_parser("manifest", help="build update.json, SHA256SUMS and release notes")
    p.add_argument("--outputs", default="outputs")
    p.add_argument("--tag", required=True)
    p.add_argument("--version-name", required=True)
    p.add_argument("--version-code", required=True)
    p.add_argument("--commit", required=True)
    p.set_defaults(func=cmd_manifest)

    p = sub.add_parser("verify-signer", help="assert every APK uses the release certificate")
    p.add_argument("--outputs", default="outputs")
    p.add_argument("--expected", required=True)
    p.set_defaults(func=cmd_verify_signer)

    p = sub.add_parser("self-test", help="validate the release contract without a real build")
    p.set_defaults(func=cmd_self_test)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
