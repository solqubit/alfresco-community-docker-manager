#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# The requirement is any Python 3.11+, whatever its name: RHEL/Rocky/Alma and Ubuntu 22.04
# keep an older python3 and ship newer interpreters as python3.11, python3.12…
acdm_find_python() {
    local candidate
    for candidate in python3 python3.14 python3.13 python3.12 python3.11; do
        if command -v "$candidate" >/dev/null &&
            "$candidate" -c 'import sys;sys.exit(0 if sys.version_info >= (3,11) else 1)' >/dev/null 2>&1; then
            acdm_python=$(command -v "$candidate")
            return 0
        fi
    done
    return 1
}
acdm_python=''
acdm_python_found=false
if command -v python3 >/dev/null; then acdm_python_found=true; fi
if ! acdm_find_python; then
    # Language selection must also work before Python/curses is available.
    acdm_language=${ACDM_STARTUP_LANGUAGE:-}
    acdm_state_dir=${ACDM_STATE_DIR:-/opt/alfresco-community-docker-manager}
    acdm_menu=true
    acdm_options() {
        while (( $# )); do
            case "$1" in
                --lang) (( $# >= 2 )) || return; acdm_language=$2; shift 2;;
                --lang=*) acdm_language=${1#*=}; shift;;
                --state-dir) (( $# >= 2 )) || return; acdm_state_dir=$2; shift 2;;
                --state-dir=*) acdm_state_dir=${1#*=}; shift;;
                --plain) export ACDM_UI=plain; shift;;
                menu) return;;
                *) acdm_menu=false; return;;
            esac
        done
    }
    if ! acdm_options "$@"; then
        printf 'Falta un valor para la opción / Missing option value: --lang es|en, --state-dir PATH\n' >&2
        exit 2
    fi
    if [[ -z $acdm_language && -f $acdm_state_dir/language ]]; then
        IFS= read -r acdm_language < "$acdm_state_dir/language" || :
        case "$acdm_language" in es|en) ;; *) acdm_language='';; esac
    fi
    case "$acdm_language" in ''|es|en) ;; *) printf 'Idioma no válido / Invalid language: es, en\n' >&2; exit 1;; esac
    acdm_animated=false acdm_tone=false acdm_terminal_state='' acdm_spinner=''
    if [[ -t 0 && -t 2 && ${TERM:-dumb} != dumb && ${ACDM_UI:-auto} != plain ]] && command -v stty >/dev/null; then
        acdm_animated=true
        [[ -n ${NO_COLOR+x} ]] || acdm_tone=true
    fi
    acdm_t() {
        case "$1" in
            python) acdm_es='Python 3 es necesario'; acdm_en='Python 3 is required';;
            cancel) acdm_es='Cancelar'; acdm_en='Cancel';;
            install) acdm_es='Instalar Python 3'; acdm_en='Install Python 3';;
            ready) acdm_es='Python 3 listo'; acdm_en='Python 3 ready';;
            failed) acdm_es='Error al instalar'; acdm_en='Installation failed';;
            manual) acdm_es='Instala Python 3 primero.'; acdm_en='Install Python 3 first.';;
            minimum) acdm_es='Se necesita Python 3.11 o posterior.'; acdm_en='Python 3.11 or later is required.';;
            keys) acdm_es='↑/↓ Elegir · Enter Confirmar · Esc Volver'; acdm_en='↑/↓ Select · Enter Confirm · Esc Back';;
            plain) acdm_es='Opción (Enter = seleccionada, b = volver): '; acdm_en='Choice (Enter = selected, b = back): ';;
        esac
        if [[ $acdm_language == en ]]; then printf '%s' "$acdm_en"
        elif [[ -z $acdm_language ]]; then printf '%s / %s' "$acdm_es" "$acdm_en"
        else printf '%s' "$acdm_es"; fi
    }
    acdm_color() { if [[ $acdm_tone == true ]]; then printf '\033[%sm' "$1" >&2; fi; }
    acdm_restore() {
        if [[ -n $acdm_spinner ]]; then kill "$acdm_spinner" 2>/dev/null || :; wait "$acdm_spinner" 2>/dev/null || :; acdm_spinner=''; fi
        if [[ -n $acdm_terminal_state ]]; then stty "$acdm_terminal_state"; acdm_terminal_state=''; fi
        [[ $acdm_animated != true ]] || printf '\033[?25h' >&2
    }
    trap acdm_restore EXIT
    trap 'acdm_restore; exit 130' INT TERM
    acdm_header() {
        acdm_color '1;35'; printf '  Alfresco Community Docker Manager\n' >&2; acdm_color 0
        printf '  ────────────────────────────────────────────────────────\n\n' >&2
        printf '  [?] %s\n\n' "$1" >&2
    }
    acdm_choose() {
        acdm_choice=0
        if [[ $acdm_animated == true ]]; then
            acdm_terminal_state=$(stty -g)
            stty -echo -icanon min 1 time 0
            printf '\033[?25l' >&2
            while :; do
                printf '\033[2J\033[H' >&2
                acdm_header "$1"
                for acdm_i in 0 1; do
                    if (( acdm_i == acdm_choice )); then acdm_color '1;37;44'; printf '  > ' >&2
                    else printf '    ' >&2; fi
                    if (( acdm_i == 0 )); then printf '%s' "$2" >&2; else printf '%s' "$3" >&2; fi
                    acdm_color 0; printf '\n' >&2
                done
                printf '\n  %s\n' "$(acdm_t keys)" >&2
                IFS= read -rsn 1 acdm_key || return 1
                case "$acdm_key" in
                    '') break;;
                    $'\t') acdm_choice=$((1-acdm_choice));;
                    $'\e')
                        IFS= read -rsn 1 -t 0.15 acdm_next || return 1
                        [[ $acdm_next == '[' || $acdm_next == O ]] || continue
                        IFS= read -rsn 1 -t 0.15 acdm_next || continue
                        [[ $acdm_next != A && $acdm_next != B ]] || acdm_choice=$((1-acdm_choice));;
                    $'\003'|$'\004') return 1;;
                esac
            done
            acdm_restore
        else
            acdm_header "$1"
            printf '  1) %s\n  2) %s\n' "$2" "$3" >&2
            while :; do
                printf '  %s' "$(acdm_t plain)" >&2
                IFS= read -r acdm_answer || return 1
                # $4 distinguishes the language question from yes/no: "si" must never mean English.
                if [[ ${4:-confirm} == language ]]; then
                    case "${acdm_answer,,}" in
                        ''|1|es|español|espanol|spanish) acdm_choice=0; break;;
                        2|en|english|inglés|ingles) acdm_choice=1; break;;
                        b|back|volver) return 1;;
                    esac
                else
                    case "${acdm_answer,,}" in
                        ''|1|n|no|cancel|cancelar) acdm_choice=0; break;;
                        2|y|yes|s|si|sí|install|instalar) acdm_choice=1; break;;
                        b|back|volver) return 1;;
                    esac
                fi
            done
        fi
    }
    if [[ $acdm_menu == true && -z $acdm_language ]]; then
        acdm_choose 'Idioma / Language' 'Español' 'English' language || exit 1
        if (( acdm_choice == 0 )); then acdm_language=es; else acdm_language=en; fi
    fi
    acdm_language=${acdm_language:-es}
    export ACDM_STARTUP_LANGUAGE=$acdm_language
    acdm_title=$(acdm_t python)
    [[ $acdm_python_found == false ]] || acdm_title=$(acdm_t minimum)
    acdm_pm=''
    for acdm_candidate in apt-get dnf zypper apk; do
        if command -v "$acdm_candidate" >/dev/null; then acdm_pm=$acdm_candidate; break; fi
    done
    # Try the distribution's default interpreter first, then its newer named packages.
    acdm_install_python() {
        local package
        case "$acdm_pm" in
            apt-get)
                DEBIAN_FRONTEND=noninteractive apt-get update || return
                for package in python3 python3.12 python3.11; do
                    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-remove "$package" ca-certificates && acdm_find_python && return 0
                done;;
            dnf)
                for package in python3 python3.12 python3.11; do
                    dnf -y install "$package" && acdm_find_python && return 0
                done;;
            zypper)
                for package in python3 python313 python312 python311; do
                    zypper --non-interactive install "$package" && acdm_find_python && return 0
                done;;
            apk) apk add python3 && acdm_find_python && return 0;;
        esac
        return 1
    }
    if [[ $(uname -s) == Linux && $EUID == 0 && -n $acdm_pm ]]; then
        acdm_choose "$acdm_title" "$(acdm_t cancel)" "$(acdm_t install)" || exit 1
        (( acdm_choice == 1 )) || exit 1
        acdm_log_dir=$(mktemp -d "${TMPDIR:-/tmp}/acdm-bootstrap.XXXXXXXX")
        acdm_log="$acdm_log_dir/python.log"
        if [[ $acdm_animated == true ]]; then
            printf '\033[2J\033[H' >&2
            acdm_header "$acdm_title"
            (
                trap - EXIT INT TERM
                acdm_frames=('⠋' '⠙' '⠹' '⠸' '⠼' '⠴' '⠦' '⠧' '⠇' '⠏')
                acdm_i=0 acdm_started=$SECONDS
                while :; do
                    acdm_elapsed=$((SECONDS-acdm_started))
                    printf '\r\033[2K  ' >&2; acdm_color '1;34'
                    printf '[%s]' "${acdm_frames[acdm_i]}" >&2; acdm_color 0
                    printf ' %s · %02d:%02d' "$(acdm_t install)" "$((acdm_elapsed/60))" "$((acdm_elapsed%60))" >&2
                    acdm_i=$(((acdm_i+1)%10))
                    sleep 0.1
                done
            ) &
            acdm_spinner=$!
        else
            printf '  [*] %s\n' "$(acdm_t install)" >&2
        fi
        # Package managers must never wait for an invisible prompt behind the spinner.
        if acdm_install_python < /dev/null > "$acdm_log" 2>&1; then
            acdm_restore
            printf '\n  [+] %s\n' "$(acdm_t ready)" >&2
        else
            acdm_restore
            printf '\n  [✗] %s\n  Log: %s\n' "$(acdm_t failed)" "$acdm_log" >&2
            exit 1
        fi
    else
        acdm_header "$acdm_title"
        printf '  [!] %s\n' "$(acdm_t manual)" >&2
        exit 1
    fi
    trap - EXIT INT TERM
fi
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
if ! acdm_find_python; then
    printf '%s\n' 'Se necesita Python 3.11+ / Python 3.11+ required.' >&2
    exit 1
fi
exec "$acdm_python" -P -m acdm "$@"
