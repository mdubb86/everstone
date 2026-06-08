# Bash completion for the `esadmin` CLI. Generated form of `esadmin
# --show-completion` (Click's standard completion template) — written
# directly to avoid shellingham process-tree detection during the docker
# build, which fails because BuildKit's parent shell is /bin/sh, not bash.
# This script is identical to what Typer's --install-completion would
# produce, just installed deterministically.
_esadmin_completion() {
    local IFS=$'\n'
    COMPREPLY=( $( env COMP_WORDS="${COMP_WORDS[*]}" \
                   COMP_CWORD=$COMP_CWORD \
                   _ESADMIN_COMPLETE=complete_bash $1 ) )
    return 0
}
complete -o default -F _esadmin_completion esadmin
