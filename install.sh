#!/usr/bin/env bash
set -euo pipefail

RIPPLE_HOME_DIR="${RIPPLE_HOME_DIR:-$HOME/.ripple}"
RIPPLE_SRC_DIR="${RIPPLE_SRC_DIR:-$RIPPLE_HOME_DIR/src}"
RIPPLE_REPO_DIR="${RIPPLE_REPO_DIR:-$RIPPLE_SRC_DIR/Ripple}"
RIPPLE_REPO_URL="${RIPPLE_REPO_URL:-https://github.com/xyskywalker/Ripple.git}"
RIPPLE_REF="${RIPPLE_REF:-}"
RIPPLE_VENV_DIR="${RIPPLE_VENV_DIR:-$RIPPLE_HOME_DIR/venv}"
RIPPLE_BIN_DIR="${RIPPLE_BIN_DIR:-$RIPPLE_HOME_DIR/bin}"
RIPPLE_CLI_WRAPPER_PATH="${RIPPLE_CLI_WRAPPER_PATH:-$RIPPLE_BIN_DIR/ripple-cli}"
RIPPLE_PUBLIC_BIN_DIR="${RIPPLE_PUBLIC_BIN_DIR:-}"
RIPPLE_BREAK_SYSTEM_PACKAGES="${RIPPLE_BREAK_SYSTEM_PACKAGES:-auto}"
RIPPLE_CONFIG_PATH="${RIPPLE_REPO_DIR}/llm_config.yaml"
OPENCLAW_SKILL_NAME="ripple-orchestrator"
OPENCLAW_STATUS_MESSAGE=""
RIPPLE_PATH_STATUS_MESSAGE=""
RIPPLE_PUBLIC_CLI_PATH=""
PRESERVED_CONFIG_BACKUP=""
INSTALL_PYTHON_BIN=""

say() {
  printf '%s\n' "$*"
}

fail() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

trim_value() {
  local value="${1:-}"
  value="${value//$'\r'/}"
  value="${value//$'\n'/}"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  if [ "${#value}" -ge 2 ] && [ "${value#\"}" != "${value}" ] && [ "${value%\"}" != "${value}" ]; then
    value="${value:1:${#value}-2}"
  fi
  printf '%s' "${value}"
}

path_contains_dir() {
  local dir_path="$1"
  case ":${PATH}:" in
    *":${dir_path}:"*)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

require_command() {
  local command_name="$1"
  if ! command -v "$command_name" >/dev/null 2>&1; then
    fail "未找到 \`$command_name\`。请先自行安装并加入 PATH。"
  fi
}

detect_platform() {
  local os_name
  os_name="$(uname -s)"
  case "$os_name" in
    Darwin|Linux)
      return 0
      ;;
    *)
      fail "当前仅支持 macOS / Linux / Windows (WSL) 的 bash 环境。"
      ;;
  esac
}

choose_python() {
  if [ -n "${RIPPLE_PYTHON_BIN:-}" ]; then
    [ -x "${RIPPLE_PYTHON_BIN}" ] || fail "RIPPLE_PYTHON_BIN 不可执行：${RIPPLE_PYTHON_BIN}"
    printf '%s\n' "${RIPPLE_PYTHON_BIN}"
    return 0
  fi

  if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "${VIRTUAL_ENV}/bin/python" ]; then
    printf '%s\n' "${VIRTUAL_ENV}/bin/python"
    return 0
  fi

  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return 0
  fi

  if command -v python >/dev/null 2>&1; then
    command -v python
    return 0
  fi

  fail "未检测到可用的 Python。请先自行准备 Python 3.11+ 和 pip。"
}

ensure_python_requirements() {
  local python_bin="$1"
  local version_text
  local py_major
  local py_minor

  version_text="$("$python_bin" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')" \
    || fail "无法执行 Python：$python_bin"

  IFS=. read -r py_major py_minor _ <<EOF
$version_text
EOF

  if [ "${py_major:-0}" -lt 3 ] || { [ "${py_major:-0}" -eq 3 ] && [ "${py_minor:-0}" -lt 11 ]; }; then
    fail "检测到 Python ${version_text}。Ripple 需要 Python 3.11+。请先自行准备符合要求的 Python 环境和 pip。"
  fi

  "$python_bin" -m pip --version >/dev/null 2>&1 \
    || fail "当前 Python 缺少 pip。请先为 ${python_bin} 准备 pip 后再运行安装脚本。"
}

preserve_user_config() {
  if [ ! -f "${RIPPLE_CONFIG_PATH}" ]; then
    return 0
  fi

  if git -C "${RIPPLE_REPO_DIR}" diff --quiet -- "llm_config.yaml"; then
    return 0
  fi

  PRESERVED_CONFIG_BACKUP="$(mktemp "${TMPDIR:-/tmp}/ripple-llm-config.XXXXXX")"
  cp "${RIPPLE_CONFIG_PATH}" "${PRESERVED_CONFIG_BACKUP}"
  git -C "${RIPPLE_REPO_DIR}" checkout -- "llm_config.yaml"
}

restore_user_config() {
  if [ -z "${PRESERVED_CONFIG_BACKUP}" ] || [ ! -f "${PRESERVED_CONFIG_BACKUP}" ]; then
    return 0
  fi

  cp "${PRESERVED_CONFIG_BACKUP}" "${RIPPLE_CONFIG_PATH}"
  rm -f "${PRESERVED_CONFIG_BACKUP}"
  PRESERVED_CONFIG_BACKUP=""
}

cleanup() {
  restore_user_config
}

cleanup_temp_file() {
  local file_path="${1:-}"
  if [ -n "${file_path}" ] && [ -f "${file_path}" ]; then
    rm -f "${file_path}"
  fi
}

ensure_dir_is_writable() {
  local dir_path="$1"
  mkdir -p "${dir_path}" 2>/dev/null || true
  [ -d "${dir_path}" ] && [ -w "${dir_path}" ]
}

clone_or_update_repo() {
  mkdir -p "${RIPPLE_SRC_DIR}"

  if [ -d "${RIPPLE_REPO_DIR}/.git" ]; then
    preserve_user_config

    if [ -n "$(git -C "${RIPPLE_REPO_DIR}" status --porcelain --untracked-files=no)" ]; then
      fail "检测到 ${RIPPLE_REPO_DIR} 存在未提交修改。请先处理本地改动后再重新执行安装脚本。"
    fi

    say "==> Updating Ripple source in ${RIPPLE_REPO_DIR}"
    if [ -n "${RIPPLE_REF}" ]; then
      git -C "${RIPPLE_REPO_DIR}" fetch --tags origin "${RIPPLE_REF}"
      git -C "${RIPPLE_REPO_DIR}" checkout --quiet "${RIPPLE_REF}"
      if git -C "${RIPPLE_REPO_DIR}" rev-parse --verify --quiet "origin/${RIPPLE_REF}" >/dev/null; then
        git -C "${RIPPLE_REPO_DIR}" pull --ff-only origin "${RIPPLE_REF}"
      fi
    else
      git -C "${RIPPLE_REPO_DIR}" pull --ff-only
    fi
    return 0
  fi

  if [ -e "${RIPPLE_REPO_DIR}" ]; then
    fail "目标路径已存在但不是 Git 仓库：${RIPPLE_REPO_DIR}"
  fi

  say "==> Cloning Ripple into ${RIPPLE_REPO_DIR}"
  git clone "${RIPPLE_REPO_URL}" "${RIPPLE_REPO_DIR}"

  if [ -n "${RIPPLE_REF}" ]; then
    git -C "${RIPPLE_REPO_DIR}" checkout --quiet "${RIPPLE_REF}"
  fi
}

bootstrap_config() {
  local example_path="${RIPPLE_REPO_DIR}/llm_config.example.yaml"
  if [ ! -f "${RIPPLE_CONFIG_PATH}" ] && [ -f "${example_path}" ]; then
    cp "${example_path}" "${RIPPLE_CONFIG_PATH}"
  fi
}

is_externally_managed_error() {
  local stderr_file="$1"
  if [ ! -f "${stderr_file}" ]; then
    return 1
  fi

  grep -qi "externally-managed-environment" "${stderr_file}" \
    || grep -qi "This environment is externally managed" "${stderr_file}"
}

run_pip_install() {
  local python_bin="$1"
  local stdout_file="$2"
  local stderr_file="$3"
  local use_break_system_packages="${4:-0}"
  local -a pip_args=()

  if [ "${use_break_system_packages}" = "1" ]; then
    pip_args+=(--break-system-packages)
  fi
  pip_args+=(-e .)

  (
    cd "${RIPPLE_REPO_DIR}"
    "${python_bin}" -m pip install "${pip_args[@]}"
  ) >"${stdout_file}" 2>"${stderr_file}"
}

print_captured_output() {
  local stdout_file="$1"
  local stderr_file="$2"

  if [ -f "${stdout_file}" ]; then
    cat "${stdout_file}"
  fi
  if [ -f "${stderr_file}" ]; then
    cat "${stderr_file}" >&2
  fi
}

ensure_private_venv() {
  local base_python="$1"
  local venv_python="${RIPPLE_VENV_DIR}/bin/python"

  if [ -x "${venv_python}" ]; then
    printf '%s\n' "${venv_python}"
    return 0
  fi

  printf '%s\n' "==> 检测到系统 Python 受保护，正在创建 Ripple 私有虚拟环境：${RIPPLE_VENV_DIR}" >&2
  if ! "${base_python}" -m venv "${RIPPLE_VENV_DIR}"; then
    fail "无法创建 Ripple 私有虚拟环境。请先安装 python3-venv（Debian/Ubuntu 可执行：apt install python3-venv），或通过 RIPPLE_PYTHON_BIN 指向现有 Python 3.11+ 虚拟环境。"
  fi

  [ -x "${venv_python}" ] \
    || fail "虚拟环境已创建，但未找到 Python：${venv_python}"

  "${venv_python}" -m pip --version >/dev/null 2>&1 \
    || fail "虚拟环境缺少 pip：${venv_python}。请先安装 python3-venv / ensurepip 后重试。"

  printf '%s\n' "${venv_python}"
}

install_ripple_package() {
  local primary_python="$1"
  local stdout_file=""
  local stderr_file=""
  local fallback_python=""
  local initial_break_system_packages="0"
  local retry_break_system_packages="0"

  if [ "${RIPPLE_BREAK_SYSTEM_PACKAGES}" = "1" ]; then
    initial_break_system_packages="1"
  fi

  stdout_file="$(mktemp "${TMPDIR:-/tmp}/ripple-install.stdout.XXXXXX")"
  stderr_file="$(mktemp "${TMPDIR:-/tmp}/ripple-install.stderr.XXXXXX")"

  if run_pip_install "${primary_python}" "${stdout_file}" "${stderr_file}" "${initial_break_system_packages}"; then
    print_captured_output "${stdout_file}" "${stderr_file}"
    INSTALL_PYTHON_BIN="${primary_python}"
    cleanup_temp_file "${stdout_file}"
    cleanup_temp_file "${stderr_file}"
    return 0
  fi

  if ! is_externally_managed_error "${stderr_file}"; then
    print_captured_output "${stdout_file}" "${stderr_file}"
    cleanup_temp_file "${stdout_file}"
    cleanup_temp_file "${stderr_file}"
    fail "Ripple 安装失败。"
  fi

  if [ "${initial_break_system_packages}" = "0" ] && [ "${RIPPLE_BREAK_SYSTEM_PACKAGES}" != "0" ]; then
    cleanup_temp_file "${stdout_file}"
    cleanup_temp_file "${stderr_file}"
    stdout_file="$(mktemp "${TMPDIR:-/tmp}/ripple-install.stdout.XXXXXX")"
    stderr_file="$(mktemp "${TMPDIR:-/tmp}/ripple-install.stderr.XXXXXX")"
    retry_break_system_packages="1"
    printf '%s\n' "==> 检测到 PEP 668，正在使用 --break-system-packages 重试系统 Python 安装" >&2
    if run_pip_install "${primary_python}" "${stdout_file}" "${stderr_file}" "${retry_break_system_packages}"; then
      print_captured_output "${stdout_file}" "${stderr_file}"
      INSTALL_PYTHON_BIN="${primary_python}"
      cleanup_temp_file "${stdout_file}"
      cleanup_temp_file "${stderr_file}"
      return 0
    fi

    if ! is_externally_managed_error "${stderr_file}"; then
      print_captured_output "${stdout_file}" "${stderr_file}"
      cleanup_temp_file "${stdout_file}"
      cleanup_temp_file "${stderr_file}"
      fail "Ripple 在系统 Python 中使用 --break-system-packages 安装仍然失败。"
    fi
  fi

  cleanup_temp_file "${stdout_file}"
  cleanup_temp_file "${stderr_file}"

  fallback_python="$(ensure_private_venv "${primary_python}")"
  ensure_python_requirements "${fallback_python}"

  stdout_file="$(mktemp "${TMPDIR:-/tmp}/ripple-install.stdout.XXXXXX")"
  stderr_file="$(mktemp "${TMPDIR:-/tmp}/ripple-install.stderr.XXXXXX")"
  if run_pip_install "${fallback_python}" "${stdout_file}" "${stderr_file}"; then
    print_captured_output "${stdout_file}" "${stderr_file}"
    INSTALL_PYTHON_BIN="${fallback_python}"
    cleanup_temp_file "${stdout_file}"
    cleanup_temp_file "${stderr_file}"
    return 0
  fi

  print_captured_output "${stdout_file}" "${stderr_file}"
  cleanup_temp_file "${stdout_file}"
  cleanup_temp_file "${stderr_file}"
  fail "Ripple 在私有虚拟环境中的安装仍然失败。"
}

install_cli_wrapper() {
  local python_bin="$1"

  mkdir -p "${RIPPLE_BIN_DIR}"
  cat > "${RIPPLE_CLI_WRAPPER_PATH}" <<EOF
#!/usr/bin/env bash
set -euo pipefail
exec "${python_bin}" -m ripple.cli.app "\$@"
EOF
  chmod +x "${RIPPLE_CLI_WRAPPER_PATH}"
}

choose_public_cli_bin_dir() {
  local candidate=""

  if [ -n "${RIPPLE_PUBLIC_BIN_DIR}" ]; then
    printf '%s\n' "${RIPPLE_PUBLIC_BIN_DIR}"
    return 0
  fi

  for candidate in /usr/local/bin /opt/homebrew/bin "${HOME}/.local/bin" "${HOME}/bin"; do
    if ensure_dir_is_writable "${candidate}"; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done

  return 1
}

ensure_path_in_shell_startup() {
  local bin_dir="$1"
  local rc_file=""
  local marker_start="# >>> ripple-cli >>>"
  local marker_end="# <<< ripple-cli <<<"

  for rc_file in "${HOME}/.profile" "${HOME}/.bashrc" "${HOME}/.zshrc" "${HOME}/.zprofile"; do
    touch "${rc_file}"
    if grep -Fq "${marker_start}" "${rc_file}"; then
      continue
    fi
    printf '\n%s\nexport PATH="%s:$PATH"\n%s\n' "${marker_start}" "${bin_dir}" "${marker_end}" >> "${rc_file}"
  done
}

install_public_cli_command() {
  local public_bin_dir=""
  local public_cli_path=""

  public_bin_dir="$(choose_public_cli_bin_dir || true)"
  if [ -z "${public_bin_dir}" ]; then
    RIPPLE_PUBLIC_CLI_PATH="${RIPPLE_CLI_WRAPPER_PATH}"
    RIPPLE_PATH_STATUS_MESSAGE="未找到合适的公共 bin 目录；当前仍可通过内部包装器使用 Ripple CLI。"
    return 0
  fi

  mkdir -p "${public_bin_dir}"
  public_cli_path="${public_bin_dir}/ripple-cli"
  rm -f "${public_cli_path}"
  if ! ln -s "${RIPPLE_CLI_WRAPPER_PATH}" "${public_cli_path}" 2>/dev/null; then
    cat > "${public_cli_path}" <<EOF
#!/usr/bin/env bash
set -euo pipefail
exec "${RIPPLE_CLI_WRAPPER_PATH}" "\$@"
EOF
    chmod +x "${public_cli_path}"
  fi

  RIPPLE_PUBLIC_CLI_PATH="${public_cli_path}"

  if path_contains_dir "${public_bin_dir}"; then
    RIPPLE_PATH_STATUS_MESSAGE="已安装全局命令：${RIPPLE_PUBLIC_CLI_PATH}"
    return 0
  fi

  ensure_path_in_shell_startup "${public_bin_dir}"
  RIPPLE_PATH_STATUS_MESSAGE="已安装全局命令：${RIPPLE_PUBLIC_CLI_PATH}。如当前 shell 还未刷新，请重新打开终端后再直接运行 ripple-cli。"
}

set_openclaw_status() {
  OPENCLAW_STATUS_MESSAGE="$1"
}

resolve_openclaw_installer() {
  if [ -n "${RIPPLE_OPENCLAW_INSTALLER_PATH:-}" ]; then
    if [ -f "${RIPPLE_OPENCLAW_INSTALLER_PATH}" ]; then
      printf '%s\n' "${RIPPLE_OPENCLAW_INSTALLER_PATH}"
    fi
    return 0
  fi

  local candidate="${RIPPLE_REPO_DIR}/integrations/openclaw/install_local_skill.sh"
  if [ -f "${candidate}" ]; then
    printf '%s\n' "${candidate}"
  fi
}

install_openclaw_skill_if_possible() {
  local installer_path=""
  local config_path=""
  local skills_root="${OPENCLAW_SKILLS_DIR:-$HOME/.openclaw/skills}"
  local target_dir="${skills_root}/${OPENCLAW_SKILL_NAME}"

  if ! command -v openclaw >/dev/null 2>&1; then
    set_openclaw_status "未检测到 OpenClaw CLI，已跳过 ${OPENCLAW_SKILL_NAME} skill 安装。"
    return 0
  fi

  installer_path="$(resolve_openclaw_installer)"
  installer_path="$(trim_value "${installer_path}")"
  if [ -z "${installer_path}" ]; then
    set_openclaw_status "已检测到 OpenClaw CLI，但未找到 ${OPENCLAW_SKILL_NAME} 的安装脚本，已跳过 skill 安装。"
    return 0
  fi

  say "==> Installing OpenClaw skill: ${OPENCLAW_SKILL_NAME}"
  if ! OPENCLAW_SKILLS_DIR="${skills_root}" bash "${installer_path}" >/dev/null; then
    set_openclaw_status "已检测到 OpenClaw CLI，但复制 ${OPENCLAW_SKILL_NAME} 到 ${target_dir} 失败。"
    return 0
  fi

  if ! openclaw config set "skills.entries[\"${OPENCLAW_SKILL_NAME}\"].enabled" true >/dev/null 2>&1; then
    set_openclaw_status "已复制 ${OPENCLAW_SKILL_NAME} 到 ${target_dir}，但未能写入 OpenClaw 配置。"
    return 0
  fi

  if ! openclaw config validate --json >/dev/null 2>&1; then
    set_openclaw_status "已复制 ${OPENCLAW_SKILL_NAME} 到 ${target_dir}，也已更新 OpenClaw 配置，但当前配置校验未通过。"
    return 0
  fi

  config_path="$(openclaw config file 2>/dev/null || true)"
  config_path="$(trim_value "${config_path}")"
  if [ -n "${config_path}" ]; then
    set_openclaw_status "已安装 ${OPENCLAW_SKILL_NAME} 到 ${target_dir}，并更新 OpenClaw 配置 ${config_path}。OpenClaw 会热加载配置；为确保新 skill 可见，请新开一个 session。"
    return 0
  fi

  set_openclaw_status "已安装 ${OPENCLAW_SKILL_NAME} 到 ${target_dir}，并更新 OpenClaw 配置。OpenClaw 会热加载配置；为确保新 skill 可见，请新开一个 session。"
}

print_success_message() {
  say
  say "🌊 欢迎使用 Ripple"
  say
  say "安装已完成，CLI 与依赖已就绪。"
  say
  say "源码目录："
  say "  ${RIPPLE_REPO_DIR}"
  say
  say "Python 环境："
  say "  ${INSTALL_PYTHON_BIN}"
  say
  say "CLI 入口："
  say "  ${RIPPLE_PUBLIC_CLI_PATH}"
  say
  say "OpenClaw："
  say "  ${OPENCLAW_STATUS_MESSAGE}"
  say
  say "PATH："
  say "  ${RIPPLE_PATH_STATUS_MESSAGE}"
  say
  say "下一步建议："
  say "  1. 交互式配置大模型"
  say "     ripple-cli llm setup"
  say
  say "  2. 或直接编辑配置文件"
  say "     ${RIPPLE_CONFIG_PATH}"
}


detect_platform
require_command git
trap cleanup EXIT

PYTHON_BIN="$(choose_python)"
ensure_python_requirements "${PYTHON_BIN}"

clone_or_update_repo

say "==> Installing Ripple with ${PYTHON_BIN}"
install_ripple_package "${PYTHON_BIN}"
install_cli_wrapper "${INSTALL_PYTHON_BIN}"
install_public_cli_command

bootstrap_config
restore_user_config
install_openclaw_skill_if_possible
print_success_message
