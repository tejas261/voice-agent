"""Exercise the one-command installer with isolated fake downloads and tools."""

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def executable(path, contents):
    path.write_text(contents, encoding='utf-8')
    path.chmod(0o755)


@pytest.fixture
def installer_fixture(tmp_path):
    project = tmp_path / 'project with spaces'
    (project / 'requirements').mkdir(parents=True)
    (project / 'scripts').mkdir()
    shutil.copy2(ROOT / 'setup.sh', project / 'setup.sh')
    for name in ('voice.lock', 'documents.lock', 'qwen-mlx.lock'):
        (project / 'requirements' / name).write_text('')
    for name in ('setup_models.py', 'setup_retrieval.py', 'setup_qwen.py'):
        (project / 'scripts' / name).write_text('')
    (project / '.env.example').write_text('# test\nAPI_TOKEN=\nLLM_PROVIDER=extractive\n')
    executable(project / 'run.sh', '#!/bin/sh\nprintf "start\\n" >> "$TEST_LOG"\n')
    tools = tmp_path / 'tools'
    tools.mkdir()
    executable(tools / 'uname', '#!/bin/sh\ncase "$1" in -s) echo "${TEST_OS:-Darwin}";; -m) echo arm64;; esac\n')
    python_wrapper = tmp_path / 'mock-python'
    executable(python_wrapper, '''#!/bin/sh
if [ "$1" = -c ]; then
  case "$2" in
    'import platform,sys;'*) exit "${TEST_BAD_PYTHON:-0}" ;;
    'from src.config import Settings;'*) printf '%s\\n' "$TEST_QWEN_PYTHON"; exit 0 ;;
  esac
fi
case "$1" in
  scripts/setup_*.py)
    printf '%s\\n' "$1" >> "$TEST_LOG"
    if [ "${TEST_FAIL_MODEL:-}" = "$1" ]; then exit 9; fi
    exit 0 ;;
esac
exec "$TEST_REAL_PYTHON" "$@"
''')
    uv = tmp_path / 'mock-uv'
    executable(uv, '''#!/bin/sh
printf 'uv %s\\n' "$*" >> "$TEST_LOG"
if [ "$1" = venv ]; then
  for target do :; done
  mkdir -p "$target/bin"
  cp "$TEST_PYTHON_WRAPPER" "$target/bin/python"
  : > "$target/pyvenv.cfg"
fi
''')
    shutil.copy2(uv, tools / 'uv')
    log = tmp_path / 'actions.log'
    env = {
        **os.environ, 'PATH': str(tools) + ':/usr/bin:/bin:/usr/sbin:/sbin',
        'TEST_LOG': str(log), 'TEST_REAL_PYTHON': sys.executable, 'TEST_PYTHON_WRAPPER': str(python_wrapper),
        'TEST_QWEN_PYTHON': str(project / '.venv-qwen' / 'bin' / 'python'),
        'TEST_UV': str(uv), 'TEST_PROJECT': str(project),
    }
    return project, tools, log, env


def run_setup(project, env, *args, cwd=None):
    return subprocess.run(['sh', str(project / 'setup.sh'), *args], cwd=cwd or project, env=env,
                          capture_output=True, text=True, check=False)


def test_fresh_setup_and_rerun_preserve_existing_data(installer_fixture):
    project, _, log, env = installer_fixture
    first = run_setup(project, env, '--no-start')
    assert first.returncode == 0, first.stderr
    data = (project / '.env').read_text()
    token = data.split('API_TOKEN=', 1)[1].splitlines()[0]
    assert len(token) >= 40 and token not in first.stdout
    assert stat.S_IMODE((project / '.env').stat().st_mode) == 0o600
    actions = log.read_text()
    assert all(name in actions for name in ('requirements/voice.lock', 'requirements/documents.lock',
                                            'requirements/qwen-mlx.lock', 'scripts/setup_models.py',
                                            'scripts/setup_retrieval.py', 'scripts/setup_qwen.py'))
    assert '\nstart\n' not in '\n' + actions
    assert (project / '.venv-local' / 'pyvenv.cfg').is_file()
    assert (project / '.venv-qwen' / 'pyvenv.cfg').is_file()
    saved = project / 'runtime' / 'businesses' / 'saved.txt'
    saved.parent.mkdir(parents=True)
    saved.write_text('business data')
    log.write_text('')
    again = run_setup(project, env, '--no-start')
    assert again.returncode == 0, again.stderr
    assert (project / '.env').read_text() == data and saved.read_text() == 'business data'
    assert 'uv venv' not in log.read_text()


def test_setup_stops_on_model_failure_without_starting(installer_fixture):
    project, _, log, env = installer_fixture
    env['TEST_FAIL_MODEL'] = 'scripts/setup_qwen.py'
    result = run_setup(project, env)
    assert result.returncode != 0
    assert 'Setup complete' not in result.stdout
    assert '\nstart\n' not in '\n' + log.read_text()


def test_incompatible_or_broken_environment_is_not_replaced(installer_fixture):
    project, _, _, env = installer_fixture
    existing = project / '.venv-local'
    existing.mkdir()
    marker = existing / 'important.txt'
    marker.write_text('preserve')
    result = run_setup(project, env, '--no-start')
    assert result.returncode != 0 and 'incomplete' in result.stderr
    assert marker.read_text() == 'preserve'
    assert not (project / '.env').exists()


def test_standalone_setup_clones_and_bootstraps_uv(installer_fixture, tmp_path):
    project, tools, log, env = installer_fixture
    (tools / 'uv').unlink()
    installer = tmp_path / 'fake-uv-installer'
    executable(installer, '''#!/bin/sh
mkdir -p "$UV_UNMANAGED_INSTALL"
cp "$TEST_UV" "$UV_UNMANAGED_INSTALL/uv"
chmod +x "$UV_UNMANAGED_INSTALL/uv"
''')
    executable(tools / 'curl', '''#!/bin/sh
while [ "$#" -gt 0 ]; do
  if [ "$1" = -o ]; then cp "$TEST_INSTALLER" "$2"; exit; fi
  shift
done
exit 1
''')
    executable(tools / 'git', '''#!/bin/sh
printf 'clone %s\\n' "$2" >> "$TEST_LOG"
cp -R "$TEST_PROJECT" "$3"
''')
    standalone = tmp_path / 'setup.sh'
    shutil.copy2(ROOT / 'setup.sh', standalone)
    target_parent = tmp_path / 'new laptop'
    target_parent.mkdir()
    env['TEST_INSTALLER'] = str(installer)
    env['TEST_QWEN_PYTHON'] = str(target_parent / 'voice-agent' / '.venv-qwen' / 'bin' / 'python')
    result = subprocess.run(['sh', str(standalone)], cwd=target_parent, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert (target_parent / 'voice-agent' / '.setup-tools' / 'bin' / 'uv').is_file()
    assert (target_parent / 'voice-agent' / '.env').is_file()
    assert 'clone https://github.com/tejas261/voice-agent.git' in log.read_text()
    assert log.read_text().endswith('start\n')
    assert not (project / '.env').exists()


def test_non_apple_platform_is_rejected_before_changes(installer_fixture):
    project, _, log, env = installer_fixture
    env['TEST_OS'] = 'Linux'
    result = run_setup(project, env)
    assert result.returncode != 0 and 'Apple Silicon' in result.stderr
    assert not (project / '.env').exists() and not log.exists()
