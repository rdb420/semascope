"""The `./oss` shim, run against a fake `docker` that records every call.

The shim is copied into a scratch "repository" with its own env file, and a
`docker` script on PATH writes each call's arguments and standard input to
files, so these tests see exactly what the shim asks Docker to do without a
Docker daemon. The parts that run inside the containers are exercised by the
integration and fresh-clone runs (scripts/integration.sh,
.github/scripts/fresh-clone.sh).
"""

# ruff: noqa: S105, S108 - fake old secrets in a scratch env file; /tmp paths inside the container

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]

FAKE_DOCKER = r"""#!/usr/bin/env bash
dir="$FAKE_DOCKER_DIR"
n=$(( $(cat "$dir/count" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$dir/count"
printf '%s\0' "$@" > "$dir/call-$n.args"
printf '%s' "${COMPOSE_FILE:-}" > "$dir/call-$n.compose_file"
if [ ! -t 0 ]; then cat > "$dir/call-$n.stdin"; fi
joined="$*"
if [ -n "${FAKE_FAIL:-}" ] && [[ "$joined" == *"$FAKE_FAIL"* ]]; then
  echo "fake docker: failing $joined" >&2
  exit 1
fi
if [ -n "${FAKE_STDOUT:-}" ] && [[ "$joined" == *" cli oss "* ]]; then
  printf '%s' "$FAKE_STDOUT"
fi
exit 0
"""

ENV = """\
SEMASCOPE_SECRET_KEY=old-secret-key-0123456789
POSTGRES_PASSWORD=old-pg-password
NEO4J_PASSWORD=old-neo4j-password
SOLR_PASSWORD=old-solr-password
VALKEY_PASSWORD=old-valkey-password
SEMASCOPE_ADMIN_PASSWORD=old-admin-password
SEMASCOPE_API_TOKEN=oss_old-api-token
SEMASCOPE_DEFAULT_ROLE=none
"""


@dataclass
class Call:
    args: list[str]
    stdin: bytes
    compose_file: str = ""


@dataclass
class Shim:
    repo: Path
    work: Path
    fake: Path
    path: str

    def run(
        self, *args: str, cwd: Path | None = None, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        environ = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("SEMASCOPE_", "COMPOSE_", "POSTGRES_", "VALKEY_"))
        }
        environ.update(PATH=self.path, FAKE_DOCKER_DIR=str(self.fake), **(env or {}))
        return subprocess.run(  # noqa: S603 - the shim under test, fixed arguments
            ["bash", str(self.repo / "oss"), *args],  # noqa: S607 - bash from the test PATH
            cwd=cwd or self.work,
            env=environ,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )

    def calls(self) -> list[Call]:
        count = int((self.fake / "count").read_text()) if (self.fake / "count").exists() else 0
        out = []
        for n in range(1, count + 1):
            args = (self.fake / f"call-{n}.args").read_bytes().split(b"\0")[:-1]
            stdin_file = self.fake / f"call-{n}.stdin"
            stdin = stdin_file.read_bytes() if stdin_file.exists() else b""
            compose_file = (self.fake / f"call-{n}.compose_file").read_text()
            out.append(Call([a.decode() for a in args], stdin, compose_file))
        return out

    def env(self) -> dict[str, str]:
        out = {}
        for line in (self.repo / ".env").read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep:
                out[key] = value
        return out


@pytest.fixture
def shim(tmp_path: Path) -> Shim:
    repo = tmp_path / "semascope"
    repo.mkdir()
    shutil.copy2(REPO / "oss", repo / "oss")
    (repo / ".env").write_text(ENV)
    work = tmp_path / "work"
    work.mkdir()
    fake = tmp_path / "fake"
    fake.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(0o755)
    # system tools only: no uv, unless a test puts it there
    return Shim(repo, work, fake, f"{bin_dir}:/usr/bin:/bin")


def tar_members(data: bytes) -> dict[str, bytes]:
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        return {
            m.name: tar.extractfile(m).read()  # type: ignore[union-attr]
            for m in tar.getmembers()
            if m.isfile()
        }


# --- host files: upload, pack, eval, export (SEMA-019) -----------------------------------------


def test_upload_sends_the_host_files_into_the_container(shim: Shim) -> None:
    (shim.work / "report.pdf").write_bytes(b"%PDF-1.7 report")
    (shim.work / "sub").mkdir()
    (shim.work / "sub" / "notes.txt").write_text("notes")
    proc = shim.run("upload", "report.pdf", "sub/notes.txt", "--collection", "mine")
    assert proc.returncode == 0, proc.stderr
    (call,) = shim.calls()
    assert call.args[:5] == ["compose", "run", "--rm", "-T", "cli"]
    script_at = call.args.index("-c") + 1
    assert call.args[script_at + 1 :] == [
        "sh",
        "/tmp/oss-upload",
        "drop",
        "upload",
        "/tmp/oss-upload/report.pdf",
        "/tmp/oss-upload/notes.txt",
        "--collection",
        "mine",
    ]
    assert tar_members(call.stdin) == {"report.pdf": b"%PDF-1.7 report", "notes.txt": b"notes"}
    # the bind-mounted import folder exists before Docker could create it as root
    assert (shim.repo / "data" / "import").is_dir()


def test_upload_refuses_what_it_cannot_send(shim: Shim) -> None:
    proc = shim.run("upload", "missing.pdf")
    assert proc.returncode != 0 and "missing.pdf is not a file" in proc.stderr
    (shim.work / "a").mkdir()
    (shim.work / "b").mkdir()
    (shim.work / "a" / "x.txt").write_text("1")
    (shim.work / "b" / "x.txt").write_text("2")
    proc = shim.run("upload", "a/x.txt", "b/x.txt")
    assert proc.returncode != 0 and "two inputs are named x.txt" in proc.stderr
    proc = shim.run("upload", "--collection", "mine")
    assert proc.returncode != 0 and "usage" in proc.stderr
    assert shim.calls() == []


def test_a_collection_named_like_a_file_is_not_sent(shim: Shim) -> None:
    (shim.work / "mine").write_text("a file called mine")
    (shim.work / "a.txt").write_text("a")
    assert shim.run("upload", "--collection", "mine", "a.txt").returncode == 0
    (call,) = shim.calls()
    assert call.args[-3:] == ["--collection", "mine", "/tmp/oss-upload/a.txt"]
    assert set(tar_members(call.stdin)) == {"a.txt"}


def test_pack_import_of_a_host_bundle_goes_to_the_data_volume(shim: Shim) -> None:
    bundle = shim.work / "my-pack"
    (bundle / "ontology").mkdir(parents=True)
    (bundle / "requirements.yaml").write_text("id: mine\n")
    (bundle / "ontology" / "x.ttl").write_text("# ttl\n")
    assert shim.run("pack", "import", "my-pack", "--yes").returncode == 0
    (call,) = shim.calls()
    tail = call.args[call.args.index("-c") + 2 :]
    assert tail[0] == "sh" and tail[2] == "keep"
    dest = tail[1]
    assert re.fullmatch(r"/data/packs/uploaded/\d{8}T\d{6}Z-\d+", dest), dest
    assert tail[3:] == ["pack", "import", f"{dest}/my-pack", "--yes"]
    assert tar_members(call.stdin) == {
        "my-pack/requirements.yaml": b"id: mine\n",
        "my-pack/ontology/x.ttl": b"# ttl\n",
    }


def test_pack_paths_inside_the_stack_pass_through(shim: Shim) -> None:
    assert shim.run("pack", "import", "/app/packs/demo/pack").returncode == 0
    assert shim.run("pack", "list").returncode == 0
    first, second = shim.calls()
    assert first.args[:5] == ["compose", "run", "--rm", "-T", "cli"]
    assert first.args[5:] == ["oss", "pack", "import", "/app/packs/demo/pack"]
    assert second.args[-3:] == ["oss", "pack", "list"]
    assert first.stdin == b""


def test_pack_validate_sends_the_bundle_and_refuses_manifest(shim: Shim) -> None:
    (shim.work / "p").mkdir()
    (shim.work / "p" / "requirements.yaml").write_text("id: p\n")
    assert shim.run("pack", "validate", "p").returncode == 0
    (call,) = shim.calls()
    assert call.args[-3:] == ["pack", "validate", "/tmp/oss-pack/p"]
    proc = shim.run("pack", "validate", "p", "--manifest")
    assert proc.returncode != 0 and "uv run oss pack validate --manifest" in proc.stderr


def test_eval_sends_the_judgement_file(shim: Shim) -> None:
    (shim.work / "judgements.yaml").write_text("queries: []\n")
    assert shim.run("eval", "--k", "5", "judgements.yaml").returncode == 0
    (call,) = shim.calls()
    assert call.args[-4:] == ["eval", "--k", "5", "/tmp/oss-eval/judgements.yaml"]
    assert tar_members(call.stdin) == {"judgements.yaml": b"queries: []\n"}


def test_export_out_writes_the_file_on_this_machine(shim: Shim) -> None:
    out = {"FAKE_STDOUT": "@prefix x: <y> ."}
    proc = shim.run("export", "search", "odysseus", "--out", "hits.ttl", env=out)
    assert proc.returncode == 0, proc.stderr
    assert (shim.work / "hits.ttl").read_text() == "@prefix x: <y> ."
    (call,) = shim.calls()
    assert call.args[-4:] == ["oss", "export", "search", "odysseus"]


# --- index, up, backup (SEMA-068) --------------------------------------------------------------


def test_index_takes_the_path_anywhere_and_maps_it_under_import(shim: Shim) -> None:
    folder = shim.repo / "data" / "import" / "my-files"
    folder.mkdir(parents=True)
    proc = shim.run("index", "--collection", "mine", "../semascope/data/import/my-files")
    assert proc.returncode == 0, proc.stderr
    (call,) = shim.calls()
    assert call.args[-5:] == ["oss", "index", "/import/my-files", "--collection", "mine"]


def test_index_without_a_path_or_outside_import_says_so(shim: Shim) -> None:
    proc = shim.run("index")
    assert proc.returncode != 0 and "usage: ./oss index" in proc.stderr
    assert "unbound variable" not in proc.stderr
    (shim.work / "elsewhere").mkdir()
    # no import folder yet: it is created, and a path outside it is still refused
    proc = shim.run("index", "elsewhere")
    assert proc.returncode == 2 and "outside SEMASCOPE_IMPORT_DIR" in proc.stderr
    assert (shim.repo / "data" / "import").is_dir()
    assert shim.calls() == []


def test_up_creates_the_import_folder_and_shows_a_failed_pull(shim: Shim) -> None:
    with (shim.repo / ".env").open("a") as env:
        env.write("SEMASCOPE_IMPORT_DIR=./corpus  # where the files are\n")
    proc = shim.run("up", env={"FAKE_FAIL": "pull"})
    assert proc.returncode == 0, proc.stderr
    assert (shim.repo / "corpus").is_dir()
    assert "could not pull every image" in proc.stderr
    assert "fake docker: failing compose pull" in proc.stderr  # the pull's own error is shown
    verbs = [c.args[1] for c in shim.calls()]
    assert verbs == ["pull", "up", "run"]


def test_backup_takes_with_graph_in_any_position(shim: Shim) -> None:
    proc = shim.run("backup", "--with-graph", cwd=shim.repo)
    # the fake Solr writes no backup, so the run stops there; what matters is the folder
    assert proc.returncode != 0
    assert not (shim.repo / "--with-graph").exists()
    (stamp,) = (shim.repo / "data" / "backups").iterdir()
    assert re.fullmatch(r"\d{8}T\d{6}Z", stamp.name)
    proc = shim.run("backup", "here", "--bogus")
    assert proc.returncode != 0 and "unknown option --bogus" in proc.stderr


# --- rotate (SEMA-044) -------------------------------------------------------------------------


def test_rotate_changes_each_store_then_the_env_file(shim: Shim) -> None:
    proc = shim.run("rotate")
    assert proc.returncode == 0, proc.stderr + proc.stdout
    env = shim.env()
    old = dict(line.split("=", 1) for line in ENV.splitlines())
    for name in (
        "SEMASCOPE_SECRET_KEY",
        "POSTGRES_PASSWORD",
        "NEO4J_PASSWORD",
        "SOLR_PASSWORD",
        "VALKEY_PASSWORD",
        "SEMASCOPE_ADMIN_PASSWORD",
        "SEMASCOPE_API_TOKEN",
    ):
        assert env[name] != old[name] and len(env[name]) >= 32, name
    assert env["SEMASCOPE_API_TOKEN"].startswith("oss_")
    assert env["SEMASCOPE_DEFAULT_ROLE"] == "none"  # everything else kept
    # pseudonyms do not change: the old secret key becomes the pseudonym key
    assert env["SEMASCOPE_PSEUDONYM_KEY"] == old["SEMASCOPE_SECRET_KEY"]
    assert "GRAFANA_PASSWORD" not in env  # not running, not in the file: skipped
    assert (shim.repo / ".env").stat().st_mode & 0o777 == 0o600
    (before,) = shim.repo.glob(".env.before-rotate-*")
    assert before.read_text() == ENV and before.stat().st_mode & 0o777 == 0o600

    calls = shim.calls()
    postgres = next(c for c in calls if "postgres" in c.args and "exec" in c.args)
    assert b"ALTER USER semascope PASSWORD :'pw'" in postgres.stdin
    assert f"pw={env['POSTGRES_PASSWORD']}" in postgres.args
    neo4j = next(c for c in calls if "neo4j" in c.args and "exec" in c.args)
    assert f"NEO4J_PASSWORD={old['NEO4J_PASSWORD']}" in neo4j.args
    statement = f"FROM '{old['NEO4J_PASSWORD']}' TO '{env['NEO4J_PASSWORD']}'"
    assert statement.encode() in neo4j.stdin
    solr = next(c for c in calls if "solr" in c.args and "exec" in c.args)
    assert f'"semascope": "{env["SOLR_PASSWORD"]}"'.encode() in solr.stdin
    assert f"OSS_OLD={old['SOLR_PASSWORD']}" in solr.args
    managed = [c for c in calls if "oss-manage" in c.args]
    assert f"OSS_NEW={env['SEMASCOPE_ADMIN_PASSWORD']}" in managed[0].args
    assert f"OSS_NEW={env['SEMASCOPE_API_TOKEN']}" in managed[1].args
    assert f"OSS_OLD={old['SEMASCOPE_API_TOKEN']}" in managed[1].args
    # the stores are up first; afterwards the stack is recreated and checked
    assert calls[0].args[:3] == ["compose", "up", "-d"]
    assert calls[-2].args == ["compose", "up", "-d", "--wait"]
    assert calls[-1].args[-2:] == ["oss", "doctor"]


def test_rotate_stops_at_the_first_refusal_with_the_file_matching_the_stores(shim: Shim) -> None:
    proc = shim.run("rotate", env={"FAKE_FAIL": "NEO4J_USERNAME"})
    assert proc.returncode != 0 and "Neo4j refused the new password" in proc.stderr
    env = shim.env()
    assert env["POSTGRES_PASSWORD"] != "old-pg-password"  # applied before Neo4j
    assert env["NEO4J_PASSWORD"] == "old-neo4j-password"
    assert env["SOLR_PASSWORD"] == "old-solr-password"


def test_rotate_names_and_refusals(shim: Shim) -> None:
    proc = shim.run("rotate", "SEMASCOPE_PSEUDONYM_KEY")
    assert proc.returncode != 0 and "not rotated" in proc.stderr
    proc = shim.run("rotate", "NOPE")
    assert proc.returncode != 0 and "unknown secret NOPE" in proc.stderr
    proc = shim.run("rotate", "GRAFANA_PASSWORD")
    assert proc.returncode != 0 and "GRAFANA_PASSWORD is not in" in proc.stderr
    assert shim.calls() == []
    assert shim.run("rotate", "VALKEY_PASSWORD").returncode == 0
    env = shim.env()
    assert env["VALKEY_PASSWORD"] != "old-valkey-password"
    assert env["POSTGRES_PASSWORD"] == "old-pg-password"


# --- init without uv (O-03) --------------------------------------------------------------------


def test_init_without_uv_runs_in_the_app_image(shim: Shim) -> None:
    (shim.repo / ".env").unlink()
    assert shim.run("init", "--size", "medium").returncode == 0
    (call,) = shim.calls()
    repo = str(shim.repo.resolve())
    assert call.args[:4] == ["compose", "run", "--rm", "--no-deps"]
    assert f"{repo}:/semascope" in call.args
    assert call.args[call.args.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"
    assert call.args[call.args.index("cli") :] == [
        "cli",
        "oss",
        "init",
        "--env-file",
        "/semascope/.env",
        "--size",
        "medium",
    ]


def test_help_without_an_env_file(shim: Shim) -> None:
    (shim.repo / ".env").unlink()
    proc = shim.run("help")
    assert proc.returncode == 0 and "rotate" in proc.stdout and "upload" in proc.stdout
    assert shim.calls() == []


# --- Docker secrets (compose.secrets.yaml) -----------------------------------------------------


def test_secrets_mode_adds_the_overlay_and_leaves_valkey_alone(shim: Shim) -> None:
    (shim.repo / ".env").write_text(
        "SEMASCOPE_RUNTIME=gpu\nSEMASCOPE_SECRETS_DIR=./secrets\nSEMASCOPE_DEFAULT_ROLE=none\n"
    )
    assert shim.run("up").returncode == 0
    assert "VALKEY_PASSWORD" not in (shim.repo / ".env").read_text()
    assert {c.compose_file for c in shim.calls()} == {
        "compose.yaml:compose.gpu.yaml:compose.secrets.yaml"
    }
    proc = shim.run("rotate")
    assert proc.returncode != 0 and "rotate them by hand" in proc.stderr
    # an explicit COMPOSE_FILE still wins
    assert shim.run("stats", env={"COMPOSE_FILE": "compose.yaml"}).returncode == 0
    assert shim.calls()[-1].compose_file == "compose.yaml"


# --- restore puts the Solr configsets back (a RESTORE overwrites them) --------------------------


def fake_backup(folder: Path) -> Path:
    import gzip

    (folder / "solr").mkdir(parents=True)
    (folder / "postgres.sql.gz").write_bytes(gzip.compress(b"-- dump\n"))
    with tarfile.open(folder / "data.tar.gz", "w:gz") as tar:
        info = tarfile.TarInfo("blobs/x")
        tar.addfile(info, io.BytesIO(b""))
    for name in ("documents", "entities", "chunks"):
        with tarfile.open(folder / "solr" / f"{name}.tar", "w") as tar:
            data = b"backup"
            info = tarfile.TarInfo(f"./{name}/backup.properties")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return folder


def test_a_refused_restore_uploads_the_configsets_before_the_app_starts(shim: Shim) -> None:
    backup = fake_backup(shim.work / "backup")
    proc = shim.run("restore", "backup", env={"FAKE_FAIL": "action=RESTORE"})
    assert proc.returncode != 0 and "nothing was changed" in proc.stderr
    calls = [c.args for c in shim.calls()]
    restore = next(i for i, a in enumerate(calls) if any("action=RESTORE" in x for x in a))
    assert any("collection.configName=semascope-documents" in x for x in calls[restore])
    refresh = next(i for i, a in enumerate(calls) if "oss-manage" in a)
    restart = next(i for i, a in enumerate(calls) if a[:4] == ["compose", "up", "-d", "--wait"])
    assert restore < refresh < restart
    assert "upload_configset" in calls[refresh][-1]
    assert backup.is_dir()


def test_a_restore_uploads_the_configsets_after_the_switch(shim: Shim) -> None:
    fake_backup(shim.work / "backup")
    assert shim.run("restore", "backup").returncode == 0
    calls = [c.args for c in shim.calls()]
    switched = max(
        i
        for i, a in enumerate(calls)
        if any(x.startswith("action=RESTORE&name=chunks&collection=chunks&") for x in a)
    )
    refresh = [i for i, a in enumerate(calls) if "oss-manage" in a]
    restart = [i for i, a in enumerate(calls) if a[:4] == ["compose", "up", "-d", "--wait"]][-1]
    assert refresh and switched < refresh[-1] < restart
