"""Private, disposable migrated-template ownership for explicitly eligible tests.

Nothing here provisions a production database. Each cache belongs to one
testcontainer in one process. Credentials/catalogs remain in memory; errors
expose categories rather than connection strings or SQL exception arguments.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.metadata
import json
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import weakref
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from urllib.parse import quote, urlparse

from sqlalchemy import create_engine, text

_ROOT = Path(__file__).resolve().parents[3]
_WAIT_SECONDS = 300
_BUILD_SECONDS = 300
_CONTROL_SECONDS = 20
_CACHES_LOCK = threading.RLock()
_CACHES: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_BUILD_PHASES = {"input", "create-database", "extensions", "bootstrap", "migration"}
_FAILURE_CATEGORIES = {"database", "migration-admission", "import", "source", "unknown"}
_SQLSTATES = {
    "42501",
    "23505",
    "42P01",
    "42703",
    "23502",
    "23514",
    "42704",
    "42P07",
    "2BP01",
    "42883",
    "22023",
    "42601",
    "55000",
    "57P03",
    "UNKNOWN",
}


class TemplateError(RuntimeError):
    """A closed, content-free failure in owned test provisioning."""


def _builder_failure(error: BaseException, state: dict) -> dict:
    """Project only fixed categories; never format an exception or its operands."""
    from sqlalchemy.exc import DBAPIError

    from butlers.bootstrap_prerequisite import BootstrapPrerequisiteError

    category, sqlstate = "unknown", "UNKNOWN"
    if isinstance(error, DBAPIError):
        category = "database"
        candidate = getattr(error.orig, "pgcode", None)
        if type(candidate) is str and candidate in _SQLSTATES:
            sqlstate = candidate
    elif isinstance(error, BootstrapPrerequisiteError):
        category = "migration-admission"
    elif isinstance(error, ImportError):
        category = "import"
    elif isinstance(error, TemplateError):
        category = "source"
    return {
        "phase": state["phase"],
        "stage": state["stage"],
        "category": category,
        "sqlstate": sqlstate,
    }


def _read_builder_failure(path: Path) -> str:
    """Read at most one tiny owned status file, refusing all untrusted fields."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_size > 1024:
                return ""
            raw = os.read(descriptor, 1025)
        finally:
            os.close(descriptor)
        value = json.loads(raw)
    except (OSError, ValueError):
        return ""
    if (
        type(value) is not dict
        or set(value) != {"phase", "stage", "category", "sqlstate"}
        or type(value["phase"]) is not str
        or value["phase"] not in _BUILD_PHASES
        or type(value["stage"]) is not int
        or not -1 <= value["stage"] <= 64
        or type(value["category"]) is not str
        or value["category"] not in _FAILURE_CATEGORIES
        or type(value["sqlstate"]) is not str
        or value["sqlstate"] not in _SQLSTATES
    ):
        return ""
    return ":" + ":".join(str(value[key]) for key in ("phase", "stage", "category", "sqlstate"))


def _closed_failure(function):
    """SQLAlchemy formats bound parameters; none may escape the cache API."""

    @wraps(function)
    def guarded(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except TemplateError:
            raise
        except Exception:
            raise TemplateError("owned-template-operation-failed") from None

    return guarded


def _active_group_members(group: int) -> bool:
    """Observe only PID/group/state, never command lines or process environments.

    The Linux CI owner can prove descendants cannot continue mutating SQL after
    its direct child exits. Descendant reaping belongs to their OS parent;
    this process proves none remain active and reaps its own direct child.
    """
    proc = Path("/proc")
    if not proc.is_dir():
        raise TemplateError("process-group-observation-unavailable")
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / "stat").read_text().rpartition(")")[2].split()
        except (FileNotFoundError, ProcessLookupError):
            continue
        if len(fields) < 3:
            raise TemplateError("process-group-observation-unavailable")
        if int(fields[2]) == group and fields[0] not in ("Z", "X"):
            return True
    return False


@dataclass(frozen=True)
class MigrationStage:
    """An ordered real chain target; duplicate chains in different schemas survive."""

    chain: str
    schema: str | None = None
    revision: str | None = None

    def resolved(self) -> MigrationStage:
        from butlers.migrations import _chain_script_directory, _normalize_schema, get_chain_head

        if (
            type(self.chain) is not str
            or not self.chain
            or (self.schema is not None and type(self.schema) is not str)
            or (self.revision is not None and (type(self.revision) is not str or not self.revision))
        ):
            raise TemplateError("unresolved-stage")
        target = self.revision or get_chain_head(self.chain)
        directory = _chain_script_directory(self.chain)
        revision = directory.get_revision(target)
        if revision is None or revision.revision != target:
            raise TemplateError("unresolved-stage")
        return MigrationStage(self.chain, _normalize_schema(self.schema), target)


def _ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


@contextmanager
def _connection(url: str):
    engine = create_engine(
        url,
        isolation_level="AUTOCOMMIT",
        connect_args={
            "connect_timeout": 10,
            "options": "-c statement_timeout=20000 -c lock_timeout=5000",
        },
    )
    try:
        with engine.connect() as connection:
            yield connection
    finally:
        engine.dispose()


def _environment_inputs(module: ast.Module) -> set[str]:
    """Resolve finite os aliases; dynamic ambient migration inputs refuse."""
    aliases = {}
    constants = {}
    for node in ast.walk(module):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "os":
                    aliases[alias.asname or alias.name] = "os"
        elif isinstance(node, ast.ImportFrom) and node.module == "os":
            for alias in node.names:
                if alias.name == "*":
                    raise TemplateError("unbound-migration-environment")
                aliases[alias.asname or alias.name] = f"os.{alias.name}"

    def dotted(node):
        if isinstance(node, ast.Name):
            return aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return f"{dotted(node.value)}.{node.attr}"
        return ""

    for node in ast.walk(module):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    constants[target.id] = node.value.value
                elif dotted(node.value).startswith("os."):
                    aliases[target.id] = dotted(node.value)

    inputs = set()
    parents = {child: node for node in ast.walk(module) for child in ast.iter_child_nodes(node)}
    for node in ast.walk(module):
        argument = None
        if isinstance(node, ast.Call):
            function = dotted(node.func)
            if function in ("os.environ.get", "os.getenv"):
                if not node.args:
                    raise TemplateError("unbound-migration-environment")
                argument = node.args[0] if node.args else None
            elif function.startswith("os.environ."):
                raise TemplateError("unbound-migration-environment")
            elif (
                function in ("getattr", "vars")
                and node.args
                and dotted(node.args[0]) in ("os", "os.environ")
            ):
                raise TemplateError("unbound-migration-environment")
        elif isinstance(node, ast.Subscript) and dotted(node.value) == "os.environ":
            argument = node.slice
        else:
            continue
        if argument is None:
            continue
        key = (
            argument.value
            if isinstance(argument, ast.Constant)
            else constants.get(argument.id if isinstance(argument, ast.Name) else "")
        )
        if not isinstance(key, str):
            raise TemplateError("unbound-migration-environment")
        inputs.add(key)
    # Iteration, passing the entire mapping, reflection and indirect accessor
    # calls have no finite key witness. Simple aliases remain supported, but
    # each use of an alias must itself be a supported read or another alias.
    for node in ast.walk(module):
        if isinstance(node, (ast.Name, ast.Attribute)) and dotted(node) == "os.environ":
            parent = parents.get(node)
            supported = (
                (
                    isinstance(parent, ast.Attribute)
                    and parent.value is node
                    and parent.attr == "get"
                )
                or (isinstance(parent, ast.Subscript) and parent.value is node)
                or (
                    isinstance(parent, ast.Assign)
                    and parent.value is node
                    and len(parent.targets) == 1
                    and isinstance(parent.targets[0], ast.Name)
                )
                or (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store))
            )
            if not supported:
                raise TemplateError("unbound-migration-environment")
    return inputs


def source_profile(root: Path = _ROOT) -> str:
    """Bind actual bytes/modes, including dirty and new relevant working-tree inputs."""
    git_environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        env=git_environment,
        capture_output=True,
        timeout=10,
        check=True,
    )
    selected = sorted(
        {
            p
            for p in result.stdout.decode().split("\0")
            if p
            and (
                p.startswith(("src/", "alembic/", "roster/", "tests/"))
                or p
                in (
                    "scripts/init-db.sql",
                    "pyproject.toml",
                    "uv.lock",
                    "conftest.py",
                    "whatsapp-bridge/go.mod",
                    "whatsapp-bridge/go.sum",
                )
            )
        }
    )
    if not selected or not {"scripts/init-db.sql", "pyproject.toml", "uv.lock"} <= set(selected):
        raise TemplateError("incomplete-source-profile")
    digest = hashlib.sha256()
    digest.update(sys.version.encode())
    for package in ("alembic", "sqlalchemy", "psycopg2-binary", "asyncpg"):
        digest.update(package.encode() + importlib.metadata.version(package).encode())
    for name in selected:
        path = root / name
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            target = path.resolve(strict=True)
            if not target.is_relative_to(root):
                raise TemplateError("external-source-input")
            if target.is_dir():
                if not any(
                    target.is_relative_to(root / prefix) for prefix in ("src", "alembic", "roster")
                ):
                    raise TemplateError("unbound-directory-source-input")
                # The actual target's tracked/new files are separately bound by
                # the same complete prefix inventory; never dereference a dir as
                # a file or replace its Git link identity with an invented blob.
                payload = (
                    os.readlink(path).encode() + b"\0DIR\0" + str(target.relative_to(root)).encode()
                )
            else:
                payload = os.readlink(path).encode() + b"\0" + target.read_bytes()
        elif stat.S_ISREG(info.st_mode):
            payload = path.read_bytes()
        else:
            raise TemplateError("unsupported-source-input")
        digest.update(name.encode() + b"\0" + str(stat.S_IMODE(info.st_mode)).encode() + b"\0")
        digest.update(hashlib.sha256(payload).digest())
        if name.endswith(".py") and (
            name.startswith("alembic/versions/")
            or (name.startswith(("src/", "roster/")) and "/migrations/" in name)
        ):
            module = ast.parse(payload, filename=name)
            for setting in sorted(_environment_inputs(module)):
                # Hash only the actually referenced input; never emit its value.
                value = os.environ.get(setting)
                digest.update(
                    setting.encode()
                    + b"\0"
                    + (b"ABSENT" if value is None else b"PRESENT\0" + value.encode())
                )
    return digest.hexdigest()


@dataclass
class _Entry:
    source_name: str
    role: str
    password: str = field(repr=False)
    stages: tuple[MigrationStage, ...]
    profile: str
    authority: object = None
    database_state: object = None
    schema: bytes = field(default=b"", repr=False)
    ready: bool = False


class _Backend:
    def __init__(self, container: object):
        self.container = container
        self.admin_url = container.get_connection_url()
        self.host = container.get_container_host_ip()
        self.port = container.get_exposed_port(5432)

    def url(self, name: str, entry: _Entry) -> str:
        return (
            f"postgresql://{quote(entry.role, safe='')}:{quote(entry.password, safe='')}"
            f"@{self.host}:{self.port}/{quote(name, safe='')}"
        )

    def admin_db(self, name: str) -> str:
        return urlparse(self.admin_url)._replace(path=f"/{quote(name, safe='')}").geturl()

    def identity(self) -> tuple:
        with _connection(self.admin_url) as connection:
            server = connection.execute(
                text("SELECT system_identifier::text FROM pg_control_system()")
            ).scalar_one()
            version = connection.execute(text("SHOW server_version_num")).scalar_one()
            extensions = tuple(
                tuple(row)
                for row in connection.execute(
                    text(
                        "SELECT name,default_version FROM pg_available_extensions "
                        "WHERE name IN ('vector','pgcrypto','uuid-ossp','pg_trgm') ORDER BY name"
                    )
                )
            )
        if len(extensions) != 4:
            raise TemplateError("missing-server-extension")
        return (os.getpid(), server, version, extensions, self.host, str(self.port))

    def create_role(self, entry: _Entry) -> None:
        with _connection(self.admin_url) as connection:
            connection.execute(
                text(
                    f"CREATE ROLE {_ident(entry.role)} LOGIN NOINHERIT NOSUPERUSER NOCREATEROLE "
                    "NOCREATEDB NOREPLICATION PASSWORD :password"
                ),
                {"password": entry.password},
            )

    def construct(self, entry: _Entry, name: str, cancel: threading.Event) -> None:
        """Join/kill the actual owning builder before releasing any cache state."""
        with tempfile.TemporaryDirectory(prefix="butlers-template-status-") as directory:
            failure_path = Path(directory) / "failure.json"
            payload = json.dumps(
                {
                    "admin_url": self.admin_url,
                    "url": self.url(name, entry),
                    "name": name,
                    "role": entry.role,
                    "stages": [vars(stage) for stage in entry.stages],
                    "failure_path": str(failure_path),
                }
            ).encode()
            child = subprocess.Popen(
                [sys.executable, "-m", "butlers.testing.migrated_templates", "--build"],
                cwd=_ROOT,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                assert child.stdin is not None
                child.stdin.write(payload)
                child.stdin.close()
                deadline = time.monotonic() + _BUILD_SECONDS
                while child.poll() is None:
                    if cancel.is_set() or time.monotonic() >= deadline:
                        raise TemplateError(
                            "construction-cancelled" if cancel.is_set() else "construction-timeout"
                        )
                    time.sleep(0.02)
                if child.returncode != 0:
                    raise TemplateError("construction-failed" + _read_builder_failure(failure_path))
                if _active_group_members(child.pid):
                    raise TemplateError("construction-left-active-descendant")
            finally:
                # poll() reaps only the direct parent. Its descendants may still
                # own connections even after a nominal zero exit, so always kill
                # the owned group and prove no active member remains.
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait(timeout=_CONTROL_SECONDS)
                cleanup_deadline = time.monotonic() + _CONTROL_SECONDS
                while _active_group_members(child.pid):
                    if time.monotonic() >= cleanup_deadline:
                        raise TemplateError("construction-group-cleanup-incomplete")
                    time.sleep(0.02)

    def authority(self, entry: _Entry, *, complete: bool = False) -> tuple:
        with _connection(self.admin_url) as connection:
            roles = tuple(
                tuple(r)
                for r in connection.execute(
                    text(
                        "SELECT rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,"
                        "rolreplication,rolbypassrls,rolconnlimit,rolvaliduntil,rolconfig "
                        "FROM pg_roles ORDER BY rolname"
                    )
                )
            )
            edges = tuple(
                tuple(r)
                for r in connection.execute(
                    text(
                        "SELECT parent.rolname,member.rolname,grantor.rolname,"
                        "m.admin_option,m.inherit_option,m.set_option FROM pg_auth_members m "
                        "JOIN pg_roles parent ON parent.oid=m.roleid "
                        "JOIN pg_roles member ON member.oid=m.member "
                        "JOIN pg_roles grantor ON grantor.oid=m.grantor ORDER BY 1,2,3,4,5,6"
                    )
                )
            )
        if complete:
            return roles, edges

        # Fresh tests may create other ordinary migration logins. Their edges
        # cannot certify or alter this key's/fixed managed principals' authority.
        from butlers.testing.migration import disposable_migration_roles

        other_disposable = disposable_migration_roles() - {entry.role}

        def relevant(name):
            return name not in other_disposable

        return tuple(r for r in roles if relevant(r[0])), tuple(
            r for r in edges if all(relevant(name) for name in r[:3]) or entry.role in r[:3]
        )

    def database_state(self, name: str) -> tuple:
        with _connection(self.admin_url) as connection:
            owner, null_acl = connection.execute(
                text(
                    "SELECT pg_get_userbyid(datdba),datacl IS NULL "
                    "FROM pg_database WHERE datname=:name"
                ),
                {"name": name},
            ).one()
            acl = tuple(
                tuple(r)
                for r in connection.execute(
                    text(
                        "SELECT pg_get_userbyid(a.grantor),a.grantee=0,"
                        "CASE WHEN a.grantee=0 THEN NULL ELSE pg_get_userbyid(a.grantee) END,"
                        "a.privilege_type,a.is_grantable "
                        "FROM pg_database d CROSS JOIN LATERAL "
                        "aclexplode(COALESCE(d.datacl,acldefault('d',d.datdba))) a "
                        "WHERE d.datname=:name ORDER BY 1,2,3,4,5"
                    ),
                    {"name": name},
                )
            )
            settings = tuple(
                (r[0], tuple(r[1]))
                for r in connection.execute(
                    text(
                        "SELECT CASE WHEN s.setrole=0 THEN NULL "
                        "ELSE pg_get_userbyid(s.setrole) END,s.setconfig "
                        "FROM pg_db_role_setting s JOIN pg_database d ON d.oid=s.setdatabase "
                        "WHERE d.datname=:name ORDER BY s.setrole"
                    ),
                    {"name": name},
                )
            )
        return owner, null_acl, acl, settings

    def validate_stages(self, entry: _Entry, name: str) -> None:
        from butlers.migrations import get_chain_revision_ids

        with _connection(self.url(name, entry)) as connection:
            for stage in entry.stages:
                schema = stage.schema or "public"
                stamped = {
                    row[0]
                    for row in connection.execute(
                        text(f"SELECT version_num FROM {_ident(schema)}.alembic_version")
                    )
                }
                if stamped.intersection(get_chain_revision_ids(stage.chain)) != {stage.revision}:
                    raise TemplateError("incomplete-stage-stamps")

    def validate_source_flags(self, entry: _Entry) -> None:
        with _connection(self.admin_url) as connection:
            row = connection.execute(
                text(
                    "SELECT pg_get_userbyid(datdba),datistemplate,datallowconn "
                    "FROM pg_database WHERE datname=:name"
                ),
                {"name": entry.source_name},
            ).one_or_none()
            if row is None or tuple(row) != (entry.role, False, False):
                raise TemplateError("source-ownership-or-connection-flags-changed")

    def clone(self, entry: _Entry, name: str) -> None:
        with _connection(self.admin_url) as connection:
            connection.execute(
                text(
                    f"CREATE DATABASE {_ident(name)} OWNER {_ident(entry.role)} "
                    f"TEMPLATE {_ident(entry.source_name)}"
                )
            )
            owner, null_acl, acl, settings = entry.database_state
            if owner != entry.role:
                raise TemplateError("database-owner-changed")
            if not null_acl:
                grantees = {(True, None), (False, entry.role)}
                grantees.update((r[1], r[2]) for r in self.database_state(name)[2])
                for public, role in sorted(grantees, key=lambda r: (r[0], r[1] or "")):
                    connection.execute(
                        text(
                            f"REVOKE ALL ON DATABASE {_ident(name)} FROM "
                            + ("PUBLIC" if public else _ident(role))
                        )
                    )
                for grantor, public, grantee, privilege, grantable in acl:
                    if (
                        privilege not in {"CREATE", "CONNECT", "TEMPORARY"}
                        or type(grantable) is not bool
                    ):
                        raise TemplateError("unknown-database-acl")
                    connection.execute(text(f"SET ROLE {_ident(grantor)}"))
                    try:
                        connection.execute(
                            text(
                                f"GRANT {privilege} ON DATABASE {_ident(name)} TO "
                                + ("PUBLIC" if public else _ident(grantee))
                                + (" WITH GRANT OPTION" if grantable else "")
                            )
                        )
                    finally:
                        connection.execute(text("RESET ROLE"))
            for role, variables in settings:
                for variable in variables:
                    setting, separator, value = variable.partition("=")
                    if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", setting):
                        raise TemplateError("unknown-database-setting")
                    target = f"ALTER ROLE {_ident(role)} IN DATABASE" if role else "ALTER DATABASE"
                    connection.execute(
                        text(f"{target} {_ident(name)} SET {_ident(setting)} TO {_literal(value)}")
                    )
        if self.database_state(name) != entry.database_state:
            raise TemplateError("database-acl-settings-mismatch")

    def schema_dump(self, name: str) -> bytes:
        parsed = urlparse(self.admin_url)
        version = self.container.get_wrapped_container().exec_run(
            ["timeout", "20", "pg_dump", "--version"]
        )
        if version.exit_code != 0:
            raise TemplateError("schema-dump-client-unavailable")
        match = re.search(rb"PostgreSQL\) (\d+)\.", version.output)
        with _connection(self.admin_url) as connection:
            major = int(connection.execute(text("SHOW server_version_num")).scalar_one()) // 10000
        if match is None or int(match[1]) != major:
            raise TemplateError("schema-dump-client-server-mismatch")
        result = self.container.get_wrapped_container().exec_run(
            [
                "timeout",
                "20",
                "pg_dump",
                "--schema-only",
                "--host=127.0.0.1",
                "--port=5432",
                "--no-password",
                f"--username={parsed.username}",
                f"--dbname={name}",
            ],
            environment={"PGPASSWORD": parsed.password or ""},
        )
        if result.exit_code != 0:
            raise TemplateError("schema-dump-failed")
        raw = result.output
        # Recent matching clients may emit one unpredictable psql guard pair.
        tokens = re.findall(rb"(?m)^\\(restrict|unrestrict) ([A-Za-z0-9]+)\r?\n", raw)
        if tokens:
            if (
                len(tokens) != 2
                or tokens[0][0] != b"restrict"
                or tokens[1][0] != b"unrestrict"
                or tokens[0][1] != tokens[1][1]
            ):
                raise TemplateError("unknown-dump-wrapper")
            raw = re.sub(rb"(?m)^\\(?:un)?restrict [A-Za-z0-9]+\r?\n", b"", raw)
        return raw

    def allow_connections(self, name: str, allowed: bool) -> None:
        with _connection(self.admin_url) as connection:
            connection.execute(
                text(
                    f"ALTER DATABASE {_ident(name)} "
                    f"ALLOW_CONNECTIONS {'true' if allowed else 'false'}"
                )
            )

    def drop_db(self, name: str) -> None:
        with _connection(self.admin_url) as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS {_ident(name)} WITH (FORCE)"))

    def drop_role(self, role: str) -> None:
        with _connection(self.admin_url) as connection:
            connection.execute(text(f"DROP ROLE IF EXISTS {_ident(role)}"))


class TemplateCache:
    """One container/process owner, with bounded same-worker single-flight."""

    def __init__(self, backend: _Backend):
        self.backend = backend
        self.lock = threading.Lock()
        self.entries: dict[tuple, _Entry] = {}
        self.databases: set[str] = set()
        self.roles: set[str] = set()
        self.closed = False

    @contextmanager
    def _locked(self):
        if not self.lock.acquire(timeout=_WAIT_SECONDS):
            raise TemplateError("cache-owner-timeout")
        try:
            if self.closed:
                raise TemplateError("cache-owner-closed")
            yield
        finally:
            self.lock.release()

    @_closed_failure
    def borrow(self, name: str, stages: tuple[MigrationStage, ...], cancel: threading.Event) -> str:
        from butlers.testing.migration import provisioning_lock

        profile = source_profile()
        stages = tuple(stage.resolved() for stage in stages)
        key = (self.backend.identity(), stages, profile)
        with self._locked(), provisioning_lock():
            if cancel.is_set():
                raise TemplateError("borrow-cancelled")
            entry = self.entries.get(key)
            if entry is None:
                entry = _Entry(
                    f"template_{uuid.uuid4().hex[:16]}",
                    f"migration_cache_{uuid.uuid4().hex[:16]}",
                    uuid.uuid4().hex,
                    stages,
                    profile,
                )
                self.databases.add(entry.source_name)
                # A CREATE can commit before a connection/finalizer raises.
                # Register the exact owned identity before the first side effect.
                self.roles.add(entry.role)
                try:
                    self.backend.create_role(entry)
                    from butlers.testing.migration import _DISPOSABLE_MIGRATION_ROLES

                    _DISPOSABLE_MIGRATION_ROLES.add(entry.role)
                    self.backend.construct(entry, entry.source_name, cancel)
                    self.backend.validate_stages(entry, entry.source_name)
                    entry.database_state = self.backend.database_state(entry.source_name)
                    entry.authority = self.backend.authority(entry)
                    entry.schema = self.backend.schema_dump(entry.source_name)
                    if source_profile() != profile:
                        raise TemplateError("source-changed-during-build")
                    self.backend.allow_connections(entry.source_name, False)
                    entry.ready = True
                    self.entries[key] = entry
                except BaseException:
                    self.backend.drop_db(entry.source_name)
                    self.databases.discard(entry.source_name)
                    self.backend.drop_role(entry.role)
                    self.roles.discard(entry.role)
                    from butlers.testing.migration import _DISPOSABLE_MIGRATION_ROLES

                    _DISPOSABLE_MIGRATION_ROLES.discard(entry.role)
                    raise
            if not entry.ready or self.backend.authority(entry) != entry.authority:
                self.entries.pop(key, None)
                raise TemplateError("principal-authority-changed")
            try:
                self.backend.validate_source_flags(entry)
            except BaseException:
                self.entries.pop(key, None)
                raise
            self.databases.add(name)
            try:
                self.backend.clone(entry, name)
                self.backend.validate_stages(entry, name)
                if self.backend.schema_dump(name) != entry.schema:
                    self.entries.pop(key, None)
                    raise TemplateError("template-catalog-changed")
                if cancel.is_set() or source_profile() != profile:
                    raise TemplateError("borrow-cancelled-or-source-changed")
                if self.backend.authority(entry) != entry.authority:
                    self.entries.pop(key, None)
                    raise TemplateError("principal-authority-changed")
                return self.backend.url(name, entry)
            except BaseException:
                self.backend.drop_db(name)
                self.databases.discard(name)
                raise

    @_closed_failure
    def fresh_reference(self, clone_url: str) -> str:
        """Observe clone/global metadata BEFORE bootstrap can repair a defect."""
        name = urlparse(clone_url).path.lstrip("/")
        from butlers.testing.migration import provisioning_lock

        with self._locked(), provisioning_lock():
            entry = next(
                (e for e in self.entries.values() if self.backend.url(name, e) == clone_url), None
            )
            if entry is None or name not in self.databases:
                raise TemplateError("unowned-reference")
            frozen = (
                self.backend.schema_dump(name),
                self.backend.database_state(name),
                self.backend.authority(entry, complete=True),
            )
            reference = f"reference_{uuid.uuid4().hex[:16]}"
            self.databases.add(reference)
            try:
                self.backend.construct(entry, reference, threading.Event())
                self.backend.validate_stages(entry, reference)
                actual = (
                    self.backend.schema_dump(reference),
                    self.backend.database_state(reference),
                    self.backend.authority(entry, complete=True),
                )
                if frozen != actual:
                    raise TemplateError(
                        "clone-fresh-reference-mismatch:"
                        + ":".join(
                            str(left == right) for left, right in zip(frozen, actual, strict=True)
                        )
                    )
                return self.backend.url(reference, entry)
            except BaseException:
                self.backend.drop_db(reference)
                self.databases.discard(reference)
                raise

    @_closed_failure
    def assert_pristine_clone(self, clone_url: str) -> None:
        """A positioned full-catalog guard before a fixture mutates its clone."""
        name = urlparse(clone_url).path.lstrip("/")
        with self._locked():
            entry = next(
                (e for e in self.entries.values() if self.backend.url(name, e) == clone_url), None
            )
            if entry is None or name not in self.databases:
                raise TemplateError("unowned-clone-parity")
            if (
                self.backend.schema_dump(name) != entry.schema
                or self.backend.database_state(name) != entry.database_state
                or self.backend.authority(entry) != entry.authority
            ):
                raise TemplateError("clone-catalog-parity-mismatch")

    @_closed_failure
    def discard_clone(self, clone_url: str) -> None:
        name = urlparse(clone_url).path.lstrip("/")
        with self._locked():
            sources = {entry.source_name for entry in self.entries.values()}
            if name not in self.databases or name in sources:
                raise TemplateError("unowned-clone-cleanup")
            self.backend.drop_db(name)
            self.databases.remove(name)

    def close(self) -> None:
        # Failed cleanup retains exact ownership for a bounded retry. Borrow
        # remains closed even when teardown could not remove every resource.
        if not self.lock.acquire(timeout=_WAIT_SECONDS):
            raise TemplateError("cache-owner-timeout")
        try:
            self.closed = True
            refused = False
            for name in sorted(self.databases):
                try:
                    self.backend.drop_db(name)
                    self.databases.discard(name)
                except Exception:
                    refused = True
            for role in sorted(self.roles):
                try:
                    self.backend.drop_role(role)
                    from butlers.testing.migration import _DISPOSABLE_MIGRATION_ROLES

                    _DISPOSABLE_MIGRATION_ROLES.discard(role)
                    self.roles.discard(role)
                except Exception:
                    refused = True
            self.entries.clear()
            if refused:
                raise TemplateError("owned-cleanup-incomplete")
        finally:
            self.lock.release()


def template_cache(container: object) -> TemplateCache:
    with _CACHES_LOCK:
        cache = _CACHES.get(container)
        if cache is None:
            cache = TemplateCache(_Backend(container))
            _CACHES[container] = cache
        return cache


def close_template_cache(container: object) -> None:
    with _CACHES_LOCK:
        cache = _CACHES.get(container)
    if cache is not None:
        cache.close()
        with _CACHES_LOCK:
            if _CACHES.get(container) is cache:
                _CACHES.pop(container)


def _build(payload: dict, state: dict | None = None) -> None:
    from butlers.testing.migration import (
        _bootstrap_migration_prerequisites,
        _upgrade_chain_to_revision,
        bootstrap_extensions,
    )

    state = state if state is not None else {}
    state.update(phase="create-database", stage=-1)
    with _connection(payload["admin_url"]) as connection:
        connection.execute(
            text(f"CREATE DATABASE {_ident(payload['name'])} OWNER {_ident(payload['role'])}")
        )
    bootstrap_url = urlparse(payload["admin_url"])._replace(path=f"/{payload['name']}").geturl()
    state["phase"] = "extensions"
    bootstrap_extensions(bootstrap_url)
    state["phase"] = "bootstrap"
    _bootstrap_migration_prerequisites(bootstrap_url, payload["role"])
    for ordinal, stage in enumerate(payload["stages"]):
        state.update(phase="migration", stage=ordinal)
        _upgrade_chain_to_revision(payload["url"], **stage)


if __name__ == "__main__":
    if sys.argv[1:] != ["--build"]:
        raise SystemExit(2)
    state = {"phase": "input", "stage": -1}
    payload = None
    try:
        payload = json.load(sys.stdin)
        _build(payload, state)
    except BaseException as error:
        # This path is privately created by the owning parent, not caller SQL.
        # No raw exception, private input, URL, role or message reaches output.
        if isinstance(payload, dict) and "failure_path" in payload:
            with open(payload["failure_path"], "x", encoding="utf-8") as receipt:
                json.dump(_builder_failure(error, state), receipt, sort_keys=True)
        raise SystemExit(3) from None
