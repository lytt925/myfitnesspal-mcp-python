"""Keep the README, Dockerfile and code from drifting apart."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text()
DOCKERFILE = (ROOT / "Dockerfile").read_text()
SOURCES = {p.name: p.read_text() for p in (ROOT / "src" / "mfp_mcp").glob("*.py")}
ALL_SOURCE = "\n".join(SOURCES.values())

ENV_RE = re.compile(r"\bMFP_[A-Z][A-Z0-9_]*\b")
# Names that look like server env vars but are not: internal constants, and
# MFP_TOKEN, which is a client-side placeholder in the README's mcp-remote example.
NOT_ENV = {"MFP_API_BASE", "MFP_CLIENT_ID", "MFP_SESSION_URL", "MFP_LOGIN_URL", "MFP_TOKEN"}


def env_vars_read_by_code():
    names = set()
    for text in SOURCES.values():
        names |= set(re.findall(r"""environ(?:\.get)?\(?\[?\s*["'](MFP_[A-Z0-9_]+)["']""", text))
        names |= set(re.findall(r"""_env_flag\(\s*["'](MFP_[A-Z0-9_]+)["']""", text))
    return names


def test_the_code_reads_the_variables_we_think_it_does():
    assert env_vars_read_by_code() >= {
        "MFP_COOKIES_JSON", "MFP_CONFIG_DIR", "MFP_MCP_AUTH_TOKEN", "MFP_MCP_ALLOWED_HOSTS",
        "MFP_MCP_STATELESS", "MFP_MCP_ALLOW_NO_AUTH", "MFP_MCP_HOST", "MFP_MCP_PORT",
        "MFP_MCP_TRANSPORT",
    }


@pytest.mark.parametrize("name", sorted(env_vars_read_by_code()))
def test_every_variable_the_code_reads_is_documented(name):
    assert name in README, f"{name} is read by the code but not mentioned in the README"


def test_the_readme_does_not_mention_variables_that_no_longer_exist():
    mentioned = set(ENV_RE.findall(README)) - NOT_ENV
    unknown = {n for n in mentioned if n not in ALL_SOURCE}
    assert unknown == set(), f"README mentions variables the code does not use: {sorted(unknown)}"


def test_dockerfile_only_sets_variables_the_code_reads():
    set_in_docker = set(ENV_RE.findall(DOCKERFILE.split("ENV", 1)[1]))
    assert set_in_docker <= env_vars_read_by_code() | {"MFP_MCP_PORT"}, set_in_docker


def test_dockerfile_defaults_match_the_readme():
    assert "MFP_CONFIG_DIR=/data" in DOCKERFILE and "`/data`" in README
    assert "8000" in DOCKERFILE and "8000" in README


def test_dockerfile_healthcheck_hits_the_open_health_endpoint():
    from mfp_mcp import remote

    assert remote.HEALTH_PATH in DOCKERFILE


def slug(heading: str) -> str:
    """GitHub's anchor for a heading."""
    text = re.sub(r"`", "", heading.strip().lower())
    text = re.sub(r"[^\w\- ]", "", text, flags=re.UNICODE)
    return text.replace(" ", "-")


def headings():
    in_code = False
    for line in README.splitlines():
        if line.startswith("```"):
            in_code = not in_code
        elif not in_code and re.match(r"#{1,6} ", line):
            yield line.lstrip("#").strip()


def test_every_internal_readme_link_points_at_a_real_heading():
    anchors = {slug(h) for h in headings()}
    links = set(re.findall(r"\]\(#([^)]+)\)", README))
    assert links, "expected the README to link between its sections"
    assert {l for l in links if l not in anchors} == set()


def test_readme_documents_every_tool_the_server_exposes():
    from mfp_mcp import server

    tools = {t.name for t in server.mcp._tool_manager.list_tools()}
    assert tools, "no tools registered?"
    missing = {t for t in tools if f"`{t}`" not in README}
    assert missing == set(), f"tools missing from the README feature table: {sorted(missing)}"


def test_readme_does_not_document_tools_that_do_not_exist():
    from mfp_mcp import server

    tools = {t.name for t in server.mcp._tool_manager.list_tools()}
    table_tools = set(re.findall(r"^\| `(mfp_[a-z_]+)` \|", README, flags=re.MULTILINE))
    assert table_tools - tools == set()


def test_commands_shown_in_the_readme_are_real_subcommands():
    from mfp_mcp import remote

    parser = remote._parser()
    sub_actions = [a for a in parser._actions if a.dest == "command"][0]
    available = set(sub_actions.choices)
    mentioned = set(re.findall(r"mfp_mcp\.server (export-cookies|import-cookies)", README))
    assert mentioned and mentioned <= available
