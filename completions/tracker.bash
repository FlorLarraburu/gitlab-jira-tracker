# git-jira-tracker — Bash completion
# Install: source this file from ~/.bashrc or ~/.bash_profile
#   echo "source ~/git-jira-tracker/completions/tracker.bash" >> ~/.bashrc
# Or copy to /etc/bash_completion.d/tracker

_tracker_completion() {
    local cur prev
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"

    local commands="mr stack stale status stop restart log pending sync mrs doctor help"

    case "${prev}" in
        tracker)
            COMPREPLY=($(compgen -W "${commands}" -- "${cur}"))
            return 0
            ;;
        mr)
            COMPREPLY=($(compgen -W "--ready" -- "${cur}"))
            return 0
            ;;
        stack)
            COMPREPLY=($(compgen -W "--list --update --mr" -- "${cur}"))
            return 0
            ;;
        stale)
            COMPREPLY=($(compgen -W "--notify" -- "${cur}"))
            return 0
            ;;
        pending)
            COMPREPLY=($(compgen -W "--retry" -- "${cur}"))
            return 0
            ;;
        --update)
            # Suggest local branch names
            local branches
            branches=$(git branch --format='%(refname:short)' 2>/dev/null)
            COMPREPLY=($(compgen -W "${branches}" -- "${cur}"))
            return 0
            ;;
    esac

    COMPREPLY=($(compgen -W "${commands}" -- "${cur}"))
}

complete -F _tracker_completion tracker
