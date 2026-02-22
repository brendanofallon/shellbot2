
SHELLBOT_PATH=${SHELLBOT_PATH:-"$HOME/src/shellbot2"}

# Store the user's original prompt when this script loads
ORIGINAL_PROMPT=$PROMPT
ECHO_MODE_PROMPT="%F{green} 🤖  How can I help?%f "

# Variable to track current mode
CURRENT_MODE="NORMAL"  # Start in normal mode


# Detect gum availability once at load time
HAS_GUM=false
if command -v gum &> /dev/null; then
    HAS_GUM=true
fi

exec_shellbot() {
    uv run --project $SHELLBOT_PATH $SHELLBOT_PATH/src/shellbot2/cli.py --datadir $SHELLBOT_DATADIR daemon ask "$1"
}

# Accept-line handler for the non-gum path (ECHO mode with inline prompt)
custom_accept_line() {
    if [[ $CURRENT_MODE == "ECHO" && -n "$BUFFER" ]]; then
        echo ""
        exec_shellbot "$BUFFER"
        BUFFER=""
        zle reset-prompt
    fi
    zle .accept-line
}

switch_mode() {
    if [[ $CURRENT_MODE == "ECHO" ]]; then
        PROMPT=$ORIGINAL_PROMPT
        CURRENT_MODE="NORMAL"
    elif $HAS_GUM; then
        local message
        message=$(gum write --placeholder "How can I help?" --header " 🤖 shellbot" --width 80)
        if [[ ${#message} -gt 1 ]]; then
            exec_shellbot "$message"
        fi
    else
        PROMPT=$ECHO_MODE_PROMPT
        CURRENT_MODE="ECHO"
    fi
    zle reset-prompt
}

# Create widgets from functions
zle -N switch_mode
zle -N accept-line custom_accept_line

# Bind to Control-T
bindkey '^[[Z' switch_mode
