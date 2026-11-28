# shellcheck shell=bash
# Which consoles are configured. Sourced by the init step, which checks
# their settings, and by /usr/local/bin/ucg-backup, which backs them up one
# after the other. Console 1 uses the settings without a suffix (UCG_HOST,
# ...), console N the same names ending in _N (UCG_HOST_2, ...). A console
# exists when its UCG_HOST is set; numbers may have gaps. Nothing is taken
# over from console 1.

# The settings each console has for itself. Everything else is shared.
UCG_CONSOLE_SETTINGS=(UCG_HOST UCG_USERNAME UCG_PASSWORD UCG_SITE UCG_NAME NETWORK_BACKUP SYSTEM_BACKUP VERIFY_SSL UCG_CERT_SHA256)
# A console number, as backup.py accepts it in UCG_CONSOLE.
UCG_CONSOLE_RE='[1-9][0-9]{0,3}'

# Value of a variable without surrounding blanks; empty means "not set".
ucg_trimmed() {
    local value=${!1:-}
    value=${value#"${value%%[![:space:]]*}"}
    value=${value%"${value##*[![:space:]]}"}
    printf '%s' "${value}"
}

# The suffix of console N's settings: none for console 1.
ucg_suffix() {
    if [[ $1 == 1 ]]; then printf ''; else printf '_%s' "$1"; fi
}

# The folder of console N below /backups: UCG_NAME(_N), else console-N;
# empty (/backups itself) for console 1 without a name. backup.py has the
# same rule.
ucg_folder() {
    local name
    name=$(ucg_trimmed "UCG_NAME$(ucg_suffix "$1")")
    if [[ -z ${name} && $1 != 1 ]]; then name=console-$1; fi
    printf '%s' "${name}"
}

# The name of console N in the log: its folder, console-1 for /backups.
ucg_console_label() {
    local folder
    folder=$(ucg_folder "$1")
    printf '%s' "${folder:-console-$1}"
}

# The numbers of the configured consoles, in order, one per line: 1, then
# every N from 2 to 9999 whose UCG_HOST_N is set. (UCG_HOST_1 is refused by
# ucg_check_suffixes.)
ucg_consoles() {
    local name
    {
        echo 1
        for name in $(compgen -e); do
            if [[ ${name} =~ ^UCG_HOST_(${UCG_CONSOLE_RE})$ ]] && [[ -n $(ucg_trimmed "${name}") ]]; then
                echo "${BASH_REMATCH[1]}"
            fi
        done
    } | sort -nu
}

# Prints an ERROR line for every suffixed setting that belongs to no
# console, and fails if there is one: a second console whose UCG_HOST_2 is
# missing would otherwise never be backed up, without a word.
ucg_check_suffixes() {
    local name base n ok=0 settings
    settings=$(IFS='|'; printf '%s' "${UCG_CONSOLE_SETTINGS[*]}")
    for name in $(compgen -e); do
        [[ ${name} =~ ^(${settings})_([0-9]+)$ ]] || continue
        [[ -n $(ucg_trimmed "${name}") ]] || continue
        base=${BASH_REMATCH[1]} n=${BASH_REMATCH[2]}
        if [[ ! ${n} =~ ^${UCG_CONSOLE_RE}$ ]] || [[ ${n} == 1 ]]; then
            echo "ERROR: ${name} is not a setting: console 1 uses ${base} without a suffix, the others end in _2 to _9999" >&2
            ok=1
        elif [[ -z $(ucg_trimmed "UCG_HOST_${n}") ]]; then
            echo "ERROR: ${name} is set, but UCG_HOST_${n} is not: console ${n} needs its own UCG_HOST_${n}" >&2
            ok=1
        fi
    done
    return "${ok}"
}
