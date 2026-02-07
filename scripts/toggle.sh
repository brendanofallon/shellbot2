
SHELLBOT_PATH=${SHELLBOT_PATH:-"$HOME/src/shellbot2"}

# Store the user's original prompt when this script loads
ORIGINAL_PROMPT=$PROMPT
ECHO_MODE_PROMPT="%F{green} 🤖  How can I help?%f "

# Variable to track current mode
CURRENT_MODE="NORMAL"  # Start in normal mode


# Define the echo function for echo mode
exec_shellbot() {
    uv run --project $SHELLBOT_PATH $SHELLBOT_PATH/src/shellbot2/cli.py --datadir $SHELLBOT_DATADIR daemon ask "$BUFFER"
}

# Create a custom accept-line widget
custom_accept_line() {
    if [[ $CURRENT_MODE == "ECHO" && -n "$BUFFER" ]]; then
        echo "" # Insert a single newline
        exec_shellbot
        BUFFER=""  # Clear the command buffer
        zle reset-prompt
    fi
    zle .accept-line
}

switch_mode() {
    if [[ $CURRENT_MODE == "ECHO" ]]; then
        PROMPT=$ORIGINAL_PROMPT
        CURRENT_MODE="NORMAL"
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
