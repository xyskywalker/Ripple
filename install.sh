#!/usr/bin/env bash
set -euo pipefail

RIPPLE_HOME_DIR="${RIPPLE_HOME_DIR:-$HOME/.ripple}"
RIPPLE_SRC_DIR="${RIPPLE_SRC_DIR:-$RIPPLE_HOME_DIR/src}"
RIPPLE_REPO_DIR="${RIPPLE_REPO_DIR:-$RIPPLE_SRC_DIR/Ripple}"
RIPPLE_REPO_URL="${RIPPLE_REPO_URL:-https://github.com/xyskywalker/Ripple.git}"
RIPPLE_REF="${RIPPLE_REF:-}"
RIPPLE_CONFIG_PATH="${RIPPLE_REPO_DIR}/llm_config.yaml"
OPENCLAW_SKILL_NAME="ripple-orchestrator"
OPENCLAW_STATUS_MESSAGE=""
PRESERVED_CONFIG_BACKUP=""

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
  local gateway_mode=""
  local installer_path=""
  local config_path=""
  local skills_root="${OPENCLAW_SKILLS_DIR:-$HOME/.openclaw/skills}"
  local target_dir="${skills_root}/${OPENCLAW_SKILL_NAME}"

  if ! command -v openclaw >/dev/null 2>&1; then
    set_openclaw_status "未检测到 OpenClaw CLI，已跳过 ${OPENCLAW_SKILL_NAME} skill 安装。"
    return 0
  fi

  gateway_mode="$(openclaw config get gateway.mode 2>/dev/null || true)"
  gateway_mode="$(trim_value "${gateway_mode}")"
  if [ -z "${gateway_mode}" ]; then
    set_openclaw_status "检测到 OpenClaw CLI，但未读取到 gateway.mode，已跳过 ${OPENCLAW_SKILL_NAME} skill 安装。"
    return 0
  fi

  if [ "${gateway_mode}" != "local" ]; then
    set_openclaw_status "检测到 OpenClaw CLI 当前为 ${gateway_mode} 模式，已跳过本机 ${OPENCLAW_SKILL_NAME} skill 安装。"
    return 0
  fi

  if ! openclaw gateway status --json --require-rpc >/dev/null 2>&1; then
    set_openclaw_status "检测到 OpenClaw CLI，但本机 Gateway 未通过 RPC 健康检查，已跳过 ${OPENCLAW_SKILL_NAME} skill 安装。"
    return 0
  fi

  installer_path="$(resolve_openclaw_installer)"
  installer_path="$(trim_value "${installer_path}")"
  if [ -z "${installer_path}" ]; then
    set_openclaw_status "OpenClaw 已运行，但未找到 ${OPENCLAW_SKILL_NAME} 的安装脚本，已跳过 skill 安装。"
    return 0
  fi

  say "==> Installing OpenClaw skill: ${OPENCLAW_SKILL_NAME}"
  if ! OPENCLAW_SKILLS_DIR="${skills_root}" bash "${installer_path}" >/dev/null; then
    set_openclaw_status "OpenClaw 已运行，但复制 ${OPENCLAW_SKILL_NAME} 到 ${target_dir} 失败。"
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
  say "  ${PYTHON_BIN}"
  say
  say "OpenClaw："
  say "  ${OPENCLAW_STATUS_MESSAGE}"
  say
  say "下一步建议："
  say "  1. 交互式配置大模型"
  say "     cd \"${RIPPLE_REPO_DIR}\" && ripple-cli llm setup"
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
(
  cd "${RIPPLE_REPO_DIR}"
  "${PYTHON_BIN}" -m pip install -e .
)

bootstrap_config
restore_user_config
install_openclaw_skill_if_possible
print_success_message
