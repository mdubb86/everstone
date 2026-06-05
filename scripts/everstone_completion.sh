# Bash completion for the `everstone` CLI. Generated form of `everstone
# --show-completion` (Click's standard completion template) — written
# directly to avoid shellingham process-tree detection during the docker
# build, which fails because BuildKit's parent shell is /bin/sh, not bash.
# This script is identical to what Typer's --install-completion would
# produce, just installed deterministically.
_everstone_completion() {
    local IFS=$'\n'
    COMPREPLY=( $( env COMP_WORDS="${COMP_WORDS[*]}" \
                   COMP_CWORD=$COMP_CWORD \
                   _EVERSTONE_COMPLETE=complete_bash $1 ) )
    return 0
}
complete -o default -F _everstone_completion everstone
